from __future__ import annotations

import json
import math
from itertools import combinations
from pathlib import Path

from joblib import Parallel, delayed
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score

from .settings import CATEGORIES, CATEGORICAL_LEVELS, CONTROLS, EXPLANATORY, MODEL_GENRES


def powerset(values: list[str]):
    for size in range(len(values) + 1):
        yield from combinations(values, size)


def factorial_weight(size: int, total: int) -> float:
    return math.factorial(size) * math.factorial(total - size - 1) / math.factorial(total)


def genre_frame(base: pd.DataFrame, genre: str) -> pd.DataFrame:
    frame = base.copy()
    frame["관람의향"] = frame[f"의향__{genre}"].astype(int)
    frame["장르매체경험"] = np.where(frame[f"매체__{genre}"].eq(1), "있음", "없음")
    frame["장르최근예술교육"] = np.where(frame[f"교육__{genre}"].eq(1), "있음", "없음")
    return frame


def design_matrix(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    parts = [pd.Series(1.0, index=frame.index, name="상수")]
    mapping: dict[str, list[str]] = {}
    for variable in CONTROLS + EXPLANATORY:
        values = pd.Categorical(frame[variable], categories=CATEGORICAL_LEVELS[variable])
        dummies = pd.get_dummies(
            values,
            prefix=variable,
            prefix_sep="=",
            drop_first=True,
            dtype=float,
        )
        parts.append(dummies)
        mapping[variable] = list(dummies.columns)
    return pd.concat(parts, axis=1), mapping


def fit_logit(x: np.ndarray, y: np.ndarray, weights: np.ndarray) -> LogisticRegression:
    model = LogisticRegression(
        C=1e6,
        solver="newton-cholesky",
        fit_intercept=False,
        max_iter=1000,
        tol=1e-7,
    )
    model.fit(x, y, sample_weight=weights / weights.mean())
    return model


def _evaluate_subset(
    subset: tuple[str, ...],
    x_all: np.ndarray,
    all_columns: list[str],
    base_columns: list[str],
    variable_columns: dict[str, list[str]],
    y: np.ndarray,
    weights: np.ndarray,
) -> tuple[frozenset[str], float, float, float, float]:
    selected = base_columns.copy()
    for variable in subset:
        selected.extend(variable_columns[variable])
    indices = np.array([all_columns.index(column) for column in selected], dtype=int)
    x = x_all[:, indices]
    model = fit_logit(x, y, weights)
    probability = np.clip(model.predict_proba(x)[:, 1], 1e-12, 1 - 1e-12)
    normalized_weight = weights / weights.mean()
    log_likelihood = float(
        np.sum(normalized_weight * (y * np.log(probability) + (1 - y) * np.log(1 - probability)))
    )
    income_full_index = all_columns.index("소득집단=저소득 집단")
    income_position = int(np.flatnonzero(indices == income_full_index)[0])
    beta = model.coef_[0]
    eta = x @ beta
    eta_other = eta - x[:, income_position] * beta[income_position]
    eta_low = eta_other + beta[income_position]
    p_other = 1 / (1 + np.exp(-np.clip(eta_other, -35, 35)))
    p_low = 1 / (1 + np.exp(-np.clip(eta_low, -35, 35)))
    other = 100 * float(np.average(p_other, weights=weights))
    low = 100 * float(np.average(p_low, weights=weights))
    return frozenset(subset), low, other, other - low, log_likelihood


def exact_owen(
    values: dict[frozenset[str], float],
    genre: str,
    direction: str,
) -> pd.DataFrame:
    """범주 순서와 범주 안 변수 순서를 각각 평균한 정확 Owen 값."""
    category_names = list(CATEGORIES)
    rows: list[dict[str, object]] = []
    for target_category, members in CATEGORIES.items():
        other_categories = [name for name in category_names if name != target_category]
        for target in members:
            other_members = [name for name in members if name != target]
            value = 0.0
            for outer_tuple in powerset(other_categories):
                outer_weight = factorial_weight(len(outer_tuple), len(category_names))
                outer_variables = frozenset(
                    variable for category in outer_tuple for variable in CATEGORIES[category]
                )
                for inner_tuple in powerset(other_members):
                    inner = frozenset(inner_tuple)
                    inner_weight = factorial_weight(len(inner_tuple), len(members))
                    before = outer_variables | inner
                    after = before | {target}
                    increment = values[after] - values[before]
                    if direction == "gap_reduction":
                        increment *= -1
                    value += outer_weight * inner_weight * increment
            rows.append({"장르": genre, "범주": target_category, "변수": target, "Owen값": value})
    return pd.DataFrame(rows)


def _genre_rates(frame: pd.DataFrame, genre: str) -> list[dict[str, object]]:
    rows = []
    for group in CATEGORICAL_LEVELS["소득집단"]:
        cell = frame.loc[frame["소득집단"].eq(group)]
        rows.append(
            {
                "장르": genre,
                "소득집단": group,
                "표본수": len(cell),
                "의향자수": int(cell["관람의향"].sum()),
                "가중의향률_퍼센트": 100
                * float(np.average(cell["관람의향"], weights=cell["가중치"])),
            }
        )
    return rows


def analyze_models(base: pd.DataFrame, output_dir: Path, jobs: int = 1) -> dict[str, pd.DataFrame]:
    output_dir.mkdir(parents=True, exist_ok=True)
    joblib_temp = output_dir / "_joblib_tmp"
    joblib_temp.mkdir(parents=True, exist_ok=True)
    base.to_csv(output_dir / "00_model_analysis_frame.csv", index=False, encoding="utf-8-sig")
    income_rows: list[dict[str, object]] = []
    subset_parts: list[pd.DataFrame] = []
    importance_parts: list[pd.DataFrame] = []
    gap_parts: list[pd.DataFrame] = []
    gap_summary_rows: list[dict[str, object]] = []
    metric_rows: list[dict[str, object]] = []

    subsets = [tuple(subset) for subset in powerset(EXPLANATORY)]
    for genre in MODEL_GENRES:
        print(f"[{genre}] {len(subsets):,}개 부분모형 적합", flush=True)
        frame = genre_frame(base, genre)
        income_rows.extend(_genre_rates(frame, genre))
        x_frame, mapping = design_matrix(frame)
        x_all = x_frame.to_numpy(float)
        columns = list(x_frame.columns)
        y = frame["관람의향"].to_numpy(int)
        weights = frame["가중치"].to_numpy(float)
        base_columns = ["상수"] + [column for variable in CONTROLS for column in mapping[variable]]
        variable_columns = {variable: mapping[variable] for variable in EXPLANATORY}
        evaluated = Parallel(
            n_jobs=jobs,
            batch_size=8,
            verbose=5,
            temp_folder=str(joblib_temp),
            max_nbytes="10M",
        )(
            delayed(_evaluate_subset)(
                subset, x_all, columns, base_columns, variable_columns, y, weights
            )
            for subset in subsets
        )
        gap_values = {key: gap for key, _, _, gap, _ in evaluated}
        score_values = {key: score for key, _, _, _, score in evaluated}
        subset_table = pd.DataFrame(
            {
                "장르": genre,
                "포함변수": [" + ".join(sorted(key)) if key else "보정변수만" for key, *_ in evaluated],
                "변수수": [len(key) for key, *_ in evaluated],
                "저소득_표준화의향확률_퍼센트": [low for _, low, *_ in evaluated],
                "그외_표준화의향확률_퍼센트": [other for _, _, other, *_ in evaluated],
                "보정격차_퍼센트포인트": [gap for _, _, _, gap, _ in evaluated],
                "가중로그우도": [score for *_, score in evaluated],
            }
        )
        subset_parts.append(subset_table)

        importance = exact_owen(score_values, genre, direction="increase")
        total_score = score_values[frozenset(EXPLANATORY)] - score_values[frozenset()]
        importance["관람의향_모형적합도_기여율_퍼센트"] = 100 * importance["Owen값"] / total_score
        importance_parts.append(importance)

        gap = exact_owen(gap_values, genre, direction="gap_reduction")
        base_gap = gap_values[frozenset()]
        full_gap = gap_values[frozenset(EXPLANATORY)]
        explained = base_gap - full_gap
        gap["격차설명량_퍼센트포인트"] = gap["Owen값"]
        gap["설명된격차내_구성비_퍼센트"] = 100 * gap["Owen값"] / explained
        gap_parts.append(gap)
        gap_summary_rows.append(
            {
                "장르": genre,
                "기준보정격차_퍼센트포인트": base_gap,
                "전체모형잔여격차_퍼센트포인트": full_gap,
                "설명격차_퍼센트포인트": explained,
                "설명비율_퍼센트": 100 * explained / base_gap,
                "Owen합_검증": float(gap["Owen값"].sum()),
            }
        )
        full_eval = next(row for row in evaluated if row[0] == frozenset(EXPLANATORY))
        full_model = fit_logit(x_all, y, weights)
        probability = full_model.predict_proba(x_all)[:, 1]
        metric_rows.append(
            {
                "장르": genre,
                "표본수": len(frame),
                "의향자수": int(y.sum()),
                "가중의향률_퍼센트": 100 * float(np.average(y, weights=weights)),
                "AUC": roc_auc_score(y, probability, sample_weight=weights),
                "Brier": brier_score_loss(y, probability, sample_weight=weights),
                "전체모형_가중로그우도": full_eval[-1],
            }
        )

    income = pd.DataFrame(income_rows)
    subsets_table = pd.concat(subset_parts, ignore_index=True)
    importance_variable = pd.concat(importance_parts, ignore_index=True)
    gap_variable = pd.concat(gap_parts, ignore_index=True)
    gap_summary = pd.DataFrame(gap_summary_rows)
    metrics = pd.DataFrame(metric_rows)
    importance_category = (
        importance_variable.groupby(["장르", "범주"], as_index=False)["Owen값"].sum()
    )
    importance_category["관람의향_모형적합도_기여율_퍼센트"] = (
        100
        * importance_category["Owen값"]
        / importance_category.groupby("장르")["Owen값"].transform("sum")
    )
    gap_category = (
        gap_variable.groupby(["장르", "범주"], as_index=False)["격차설명량_퍼센트포인트"].sum()
        .merge(gap_summary[["장르", "설명격차_퍼센트포인트"]], on="장르", how="left")
    )
    gap_category["설명된격차내_구성비_퍼센트"] = (
        100 * gap_category["격차설명량_퍼센트포인트"] / gap_category["설명격차_퍼센트포인트"]
    )
    importance_mean = (
        importance_category.groupby("범주", as_index=False)["관람의향_모형적합도_기여율_퍼센트"]
        .mean()
        .rename(columns={"관람의향_모형적합도_기여율_퍼센트": "네장르_동일가중평균_기여율_퍼센트"})
    )
    gap_mean = (
        gap_category.groupby("범주", as_index=False)["설명된격차내_구성비_퍼센트"]
        .mean()
        .rename(columns={"설명된격차내_구성비_퍼센트": "네장르_동일가중평균_구성비_퍼센트"})
    )

    outputs = {
        "01_genre_income_intention_rates.csv": income,
        "02_all_subset_models.csv": subsets_table,
        "03_intention_variable_owen.csv": importance_variable,
        "04_intention_category_owen.csv": importance_category,
        "05_gap_variable_owen.csv": gap_variable,
        "06_gap_category_owen.csv": gap_category,
        "07_gap_summary.csv": gap_summary,
        "08_model_metrics.csv": metrics,
        "09_mean_intention_category_contribution.csv": importance_mean,
        "10_mean_gap_category_contribution.csv": gap_mean,
    }
    for filename, table in outputs.items():
        table.to_csv(output_dir / filename, index=False, encoding="utf-8-sig")
    audit = {
        "표본": "2023~2025년 만 20세 이상",
        "표본수": int(len(base)),
        "장르": list(MODEL_GENRES),
        "보정변수": CONTROLS,
        "설명변수": EXPLANATORY,
        "분해범주": CATEGORIES,
        "장르당_부분모형수": len(subsets),
        "직접관람경험_설명변수포함": False,
        "해석": "반복 횡단면 자료의 모형 기반 연관·격차분해이며 인과효과가 아님",
    }
    (output_dir / "11_model_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return outputs

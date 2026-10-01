from __future__ import annotations

import json
import math
from itertools import combinations
from pathlib import Path

from joblib import Parallel, delayed
import numpy as np
import pandas as pd

from .models import design_matrix, fit_logit, genre_frame, powerset
from .settings import CATEGORIES, CONTROLS, MODEL_GENRES, OPPORTUNITY, PRACTICAL, SOCIAL

SEED = 20260928


def _bootstrap_counts(n: int, strata: list[np.ndarray], seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    counts = np.zeros(n, dtype=float)
    for indices in strata:
        counts[indices] = rng.multinomial(len(indices), np.full(len(indices), 1 / len(indices)))
    return counts


def _gap(
    x_all: np.ndarray,
    indices: np.ndarray,
    y: np.ndarray,
    weights: np.ndarray,
    income_index_all: int,
) -> float:
    x = x_all[:, indices]
    model = fit_logit(x, y, weights)
    beta = model.coef_[0]
    income_position = int(np.flatnonzero(indices == income_index_all)[0])
    eta = x @ beta
    eta_other = eta - x[:, income_position] * beta[income_position]
    eta_low = eta_other + beta[income_position]
    p_other = 1 / (1 + np.exp(-np.clip(eta_other, -35, 35)))
    p_low = 1 / (1 + np.exp(-np.clip(eta_low, -35, 35)))
    return 100 * (
        float(np.average(p_other, weights=weights))
        - float(np.average(p_low, weights=weights))
    )


def _category_shapley(gaps: dict[frozenset[str], float]) -> dict[str, float]:
    categories = list(CATEGORIES)
    total = len(categories)
    result: dict[str, float] = {}
    for target in categories:
        others = [category for category in categories if category != target]
        value = 0.0
        for size in range(len(others) + 1):
            weight = math.factorial(size) * math.factorial(total - size - 1) / math.factorial(total)
            for subset in combinations(others, size):
                before = frozenset(subset)
                after = before | {target}
                value += weight * (gaps[before] - gaps[after])
        result[target] = value
    return result


def _prepare(base: pd.DataFrame):
    prepared: dict[str, dict[str, object]] = {}
    category_names = list(CATEGORIES)
    for genre in MODEL_GENRES:
        frame = genre_frame(base, genre)
        x_frame, mapping = design_matrix(frame)
        columns = list(x_frame.columns)
        base_columns = ["상수"] + [column for variable in CONTROLS for column in mapping[variable]]
        category_columns = {
            category: [column for variable in variables for column in mapping[variable]]
            for category, variables in CATEGORIES.items()
        }
        subset_indices = {}
        for subset in powerset(category_names):
            selected = base_columns.copy()
            for category in subset:
                selected.extend(category_columns[category])
            subset_indices[frozenset(subset)] = np.array(
                [columns.index(column) for column in selected], dtype=int
            )
        prepared[genre] = {
            "x": x_frame.to_numpy(float),
            "y": frame["관람의향"].to_numpy(int),
            "weight": frame["가중치"].to_numpy(float),
            "income_index": columns.index("소득집단=저소득 집단"),
            "subset_indices": subset_indices,
        }
    key = base["조사연도"].astype(str) + "|" + base["소득집단"].astype(str)
    strata = [np.flatnonzero(key.to_numpy() == value) for value in sorted(key.unique())]
    return prepared, strata


def _one_replicate(
    replicate: int,
    n: int,
    prepared: dict[str, dict[str, object]],
    strata: list[np.ndarray],
) -> list[dict[str, object]]:
    counts = _bootstrap_counts(n, strata, SEED + replicate * 104729)
    rows: list[dict[str, object]] = []
    for genre in MODEL_GENRES:
        item = prepared[genre]
        weights = item["weight"] * counts
        gaps = {
            subset: _gap(
                item["x"], indices, item["y"], weights, item["income_index"]
            )
            for subset, indices in item["subset_indices"].items()
        }
        contributions = _category_shapley(gaps)
        explained = gaps[frozenset()] - gaps[frozenset(CATEGORIES)]
        for category, contribution in contributions.items():
            rows.append(
                {
                    "반복": replicate,
                    "장르": genre,
                    "범주": category,
                    "Owen_격차설명량_퍼센트포인트": contribution,
                    "설명격차_퍼센트포인트": explained,
                }
            )
    return rows


def _summarize_ci(replicates: pd.DataFrame, point: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (genre, category), group in replicates.groupby(["장르", "범주"]):
        values = group["Owen_격차설명량_퍼센트포인트"].to_numpy()
        lower, upper = np.quantile(values, [0.025, 0.975])
        estimate = point.loc[
            point["장르"].eq(genre) & point["범주"].eq(category),
            "격차설명량_퍼센트포인트",
        ].iloc[0]
        rows.append(
            {
                "장르": genre,
                "범주": category,
                "점추정_격차설명량_퍼센트포인트": estimate,
                "부트스트랩_표준오차": values.std(ddof=1),
                "95CI_하한_퍼센트포인트": lower,
                "95CI_상한_퍼센트포인트": upper,
                "95CI_0포함": "포함" if lower <= 0 <= upper else "포함하지 않음",
                "유효반복수": len(values),
            }
        )
    return pd.DataFrame(rows)


def _axis(frame: pd.DataFrame, categories: list[str]) -> pd.Series:
    return frame.loc[frame["범주"].isin(categories)].groupby(["반복", "장르"])[
        "Owen_격차설명량_퍼센트포인트"
    ].sum()


def _point_axis(frame: pd.DataFrame, categories: list[str]) -> pd.Series:
    return frame.loc[frame["범주"].isin(categories)].groupby("장르")[
        "격차설명량_퍼센트포인트"
    ].sum()


def _contrast(
    replicates: pd.DataFrame,
    point: pd.DataFrame,
    practical_categories: list[str],
    specification: str,
) -> pd.DataFrame:
    opportunity_rep = _axis(replicates, OPPORTUNITY)
    practical_rep = _axis(replicates, practical_categories)
    contrast = (opportunity_rep - practical_rep).rename("차이").reset_index()
    opportunity_point = _point_axis(point, OPPORTUNITY)
    practical_point = _point_axis(point, practical_categories)
    rows = []
    for genre in MODEL_GENRES:
        values = contrast.loc[contrast["장르"].eq(genre), "차이"].to_numpy()
        lower, upper = np.quantile(values, [0.025, 0.975])
        rows.append(
            {
                "검정사양": specification,
                "분석단위": genre,
                "문화의식형성기회_점추정_퍼센트포인트": opportunity_point[genre],
                "현실적여건_점추정_퍼센트포인트": practical_point[genre],
                "기여량차이_점추정_퍼센트포인트": opportunity_point[genre] - practical_point[genre],
                "95CI_하한_퍼센트포인트": lower,
                "95CI_상한_퍼센트포인트": upper,
                "단측_p값": (1 + np.count_nonzero(values <= 0)) / (len(values) + 1),
                "유효반복수": len(values),
            }
        )
    overall = contrast.groupby("반복")["차이"].mean().to_numpy()
    lower, upper = np.quantile(overall, [0.025, 0.975])
    rows.append(
        {
            "검정사양": specification,
            "분석단위": "네 장르 동일가중 평균",
            "문화의식형성기회_점추정_퍼센트포인트": opportunity_point.reindex(MODEL_GENRES).mean(),
            "현실적여건_점추정_퍼센트포인트": practical_point.reindex(MODEL_GENRES).mean(),
            "기여량차이_점추정_퍼센트포인트": (
                opportunity_point - practical_point
            ).reindex(MODEL_GENRES).mean(),
            "95CI_하한_퍼센트포인트": lower,
            "95CI_상한_퍼센트포인트": upper,
            "단측_p값": (1 + np.count_nonzero(overall <= 0)) / (len(overall) + 1),
            "유효반복수": len(overall),
        }
    )
    return pd.DataFrame(rows)


def run_bootstrap(
    base: pd.DataFrame,
    model_output_dir: Path,
    output_dir: Path,
    reps: int,
    jobs: int,
) -> dict[str, pd.DataFrame]:
    output_dir.mkdir(parents=True, exist_ok=True)
    joblib_temp = output_dir / "_joblib_tmp"
    joblib_temp.mkdir(parents=True, exist_ok=True)
    prepared, strata = _prepare(base)
    print(f"범주별 층화 부트스트랩 {reps}회", flush=True)
    nested = Parallel(
        n_jobs=jobs,
        batch_size=1,
        verbose=5,
        temp_folder=str(joblib_temp),
        max_nbytes="10M",
    )(
        delayed(_one_replicate)(replicate, len(base), prepared, strata)
        for replicate in range(reps)
    )
    replicates = pd.DataFrame([row for block in nested for row in block])
    point = pd.read_csv(model_output_dir / "06_gap_category_owen.csv")
    ci = _summarize_ci(replicates, point)
    contrast = pd.concat(
        [
            _contrast(replicates, point, PRACTICAL, "주 분석: 시간·비용·건강·지역"),
            _contrast(replicates, point, PRACTICAL + SOCIAL, "민감도 분석: 사회적 관계 포함"),
        ],
        ignore_index=True,
    )
    outputs = {
        "01_bootstrap_replicates.csv": replicates,
        "02_category_confidence_intervals.csv": ci,
        "03_opportunity_practical_contrast.csv": contrast,
    }
    for filename, frame in outputs.items():
        frame.to_csv(output_dir / filename, index=False, encoding="utf-8-sig")
    audit = {
        "재표집": "조사연도×소득집단 층화 응답자 비모수 부트스트랩",
        "반복수": reps,
        "난수시드": SEED,
        "반복당_모형수": len(MODEL_GENRES) * 2 ** len(CATEGORIES),
        "단측귀무가설": "문화 의식 형성 기회 - 현실적 여건 <= 0",
        "인과해석": False,
    }
    (output_dir / "04_bootstrap_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return outputs

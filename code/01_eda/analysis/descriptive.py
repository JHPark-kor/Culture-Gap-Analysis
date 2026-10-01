from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .data import clean_usage_field
from .settings import INCOME_LEVELS


def weighted_rate(frame: pd.DataFrame, column: str) -> float:
    return 100 * float(np.average(frame[column], weights=frame["가중치"]))


def analyze_culture_nuri(source: Path, output_dir: Path) -> tuple[pd.DataFrame, dict[str, object]]:
    raw = pd.read_excel(source, sheet_name="2025")
    normalized = {column: "".join(str(column).split()) for column in raw.columns}
    total_col = next(column for column, label in normalized.items() if label == "이용건수")
    count_cols = [column for column in raw.columns if str(column).endswith("(건)")]
    district_rows = raw.iloc[:-1].copy()
    total_row = raw.iloc[-1]

    def clean_numeric(series: pd.Series) -> pd.Series:
        return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False), errors="coerce")

    total_usage = int(clean_numeric(pd.Series([total_row[total_col]])).iloc[0])
    district_usage = int(clean_numeric(district_rows[total_col]).sum())
    rows: list[dict[str, object]] = []
    for column in count_cols:
        total_value = int(clean_numeric(pd.Series([total_row[column]])).iloc[0])
        district_value = int(clean_numeric(district_rows[column]).sum())
        if total_value != district_value:
            raise AssertionError(
                f"자치구 합계 불일치: {clean_usage_field(column)} {district_value:,} != {total_value:,}"
            )
        rows.append({"분야": clean_usage_field(column), "이용건수": total_value})
    if district_usage != total_usage:
        raise AssertionError(f"전체 이용건수 불일치: {district_usage:,} != {total_usage:,}")
    if sum(row["이용건수"] for row in rows) != total_usage:
        raise AssertionError("분야별 이용건수의 합이 전체 이용건수와 다릅니다.")

    result = pd.DataFrame(rows)
    result["비중_퍼센트"] = result["이용건수"] / total_usage * 100
    result = result.sort_values("이용건수", ascending=False).reset_index(drop=True)
    result.insert(0, "순위", np.arange(1, len(result) + 1))
    summary = {
        "기준연도": 2025,
        "지역": "서울특별시",
        "자치구수": int(len(district_rows)),
        "전체이용건수": total_usage,
        "도서영화합계_비중_퍼센트": float(
            result.loc[result["분야"].isin(["도서", "영화"]), "비중_퍼센트"].sum()
        ),
        "공연전시합계_비중_퍼센트": float(
            result.loc[result["분야"].isin(["공연", "전시"]), "비중_퍼센트"].sum()
        ),
        "원자료_파일명": source.name,
        "검증": {
            "총계행과_자치구합계_일치": True,
            "분야별합계와_전체이용건수_일치": True,
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_dir / "01_culture_nuri_field_usage.csv", index=False, encoding="utf-8-sig")
    (output_dir / "01_culture_nuri_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result, summary


def analyze_income_stages(frame: pd.DataFrame, output_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows: list[dict[str, object]] = []
    state_rows: list[dict[str, object]] = []
    for group in INCOME_LEVELS[::-1]:
        cell = frame.loc[frame["소득집단"].eq(group)].copy()
        intenders = cell.loc[cell["관람의향"].eq(1)]
        summary_rows.append(
            {
                "소득집단": group,
                "표본수": len(cell),
                "최근관람자수": int(cell["최근관람"].sum()),
                "관람의향자수": int(cell["관람의향"].sum()),
                "가중_최근관람률_퍼센트": weighted_rate(cell, "최근관람"),
                "가중_관람의향률_퍼센트": weighted_rate(cell, "관람의향"),
                "의향자중_최근관람자수": int(intenders["최근관람"].sum()),
                "의향자중_가중_최근관람률_퍼센트": weighted_rate(intenders, "최근관람"),
            }
        )
        for intention, intention_label in [(0, "무의향"), (1, "의향")]:
            for attended, attended_label in [(0, "비관람"), (1, "관람")]:
                condition = cell["관람의향"].eq(intention) & cell["최근관람"].eq(attended)
                state = cell.loc[condition]
                state_rows.append(
                    {
                        "소득집단": group,
                        "상태": f"{attended_label}·{intention_label}",
                        "표본수": len(state),
                        "소득집단내_가중비율_퍼센트": 100
                        * state["가중치"].sum()
                        / cell["가중치"].sum(),
                    }
                )
    summary = pd.DataFrame(summary_rows)
    states = pd.DataFrame(state_rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_dir / "02_descriptive_analysis_frame.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(output_dir / "03_income_stage_summary.csv", index=False, encoding="utf-8-sig")
    states.to_csv(output_dir / "04_income_four_states.csv", index=False, encoding="utf-8-sig")
    return summary, states

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


EXPECTED = {
    "descriptive_n": 30325,
    "adult_model_n": 28779,
    "descriptive_group_n": {"저소득 집단": 4366, "그 외 소득집단": 25959},
    "adult_group_n": {"저소득 집단": 4230, "그 외 소득집단": 24549},
    "recent_attendance": {"저소득 집단": 9.03404, "그 외 소득집단": 19.17165},
    "intention": {"저소득 집단": 16.90197, "그 외 소득집단": 33.24546},
    "attendance_among_intenders": {"저소득 집단": 45.36841, "그 외 소득집단": 49.59166},
    "nuri_share": {"도서": 35.130096, "영화": 25.039637, "공연": 0.970206, "음악": 0.839015, "전시": 0.138694},
    "intention_category": {"문화 자본": 36.246516, "최근 문화 경험": 33.917376, "신체·정신적 여건": 11.115694},
    "gap_category": {"문화 자본": 65.605162, "최근 문화 경험": 1.077908, "신체·정신적 여건": 19.433513},
}


def _close(actual: float, expected: float, tolerance: float, label: str, checks: list[dict[str, object]]) -> None:
    passed = bool(np.isclose(actual, expected, atol=tolerance, rtol=0))
    checks.append({"검사": label, "실제": actual, "기대": expected, "허용오차": tolerance, "통과": passed})


def validate(output_root: Path) -> dict[str, object]:
    descriptive_dir = output_root / "01_descriptive"
    model_dir = output_root / "02_models"
    checks: list[dict[str, object]] = []
    desc_frame = pd.read_csv(descriptive_dir / "02_descriptive_analysis_frame.csv")
    model_frame = pd.read_csv(model_dir / "00_model_analysis_frame.csv")
    checks.append({"검사": "전 연령 표본수", "실제": len(desc_frame), "기대": EXPECTED["descriptive_n"], "통과": len(desc_frame) == EXPECTED["descriptive_n"]})
    checks.append({"검사": "성인 모형 표본수", "실제": len(model_frame), "기대": EXPECTED["adult_model_n"], "통과": len(model_frame) == EXPECTED["adult_model_n"]})

    stage = pd.read_csv(descriptive_dir / "03_income_stage_summary.csv").set_index("소득집단")
    for group, expected in EXPECTED["descriptive_group_n"].items():
        checks.append({"검사": f"전 연령 {group} 표본수", "실제": int(stage.loc[group, "표본수"]), "기대": expected, "통과": int(stage.loc[group, "표본수"]) == expected})
    for group, expected in EXPECTED["recent_attendance"].items():
        _close(stage.loc[group, "가중_최근관람률_퍼센트"], expected, 1e-4, f"{group} 최근 관람률", checks)
    for group, expected in EXPECTED["intention"].items():
        _close(stage.loc[group, "가중_관람의향률_퍼센트"], expected, 1e-4, f"{group} 관람의향률", checks)
    for group, expected in EXPECTED["attendance_among_intenders"].items():
        _close(stage.loc[group, "의향자중_가중_최근관람률_퍼센트"], expected, 1e-4, f"{group} 의향자 중 관람률", checks)

    nuri = pd.read_csv(descriptive_dir / "01_culture_nuri_field_usage.csv").set_index("분야")
    for field, expected in EXPECTED["nuri_share"].items():
        _close(nuri.loc[field, "비중_퍼센트"], expected, 1e-5, f"문화누리 {field} 비중", checks)

    adult_counts = model_frame["소득집단"].value_counts().to_dict()
    for group, expected in EXPECTED["adult_group_n"].items():
        actual = int(adult_counts[group])
        checks.append({"검사": f"성인 {group} 표본수", "실제": actual, "기대": expected, "통과": actual == expected})

    intention = pd.read_csv(model_dir / "09_mean_intention_category_contribution.csv").set_index("범주")
    for category, expected in EXPECTED["intention_category"].items():
        _close(intention.loc[category, "네장르_동일가중평균_기여율_퍼센트"], expected, 1e-3, f"관람의향 기여율 {category}", checks)
    gap = pd.read_csv(model_dir / "10_mean_gap_category_contribution.csv").set_index("범주")
    for category, expected in EXPECTED["gap_category"].items():
        _close(gap.loc[category, "네장르_동일가중평균_구성비_퍼센트"], expected, 1e-3, f"소득격차 기여율 {category}", checks)
    _close(float(intention["네장르_동일가중평균_기여율_퍼센트"].sum()), 100.0, 1e-7, "관람의향 범주 기여율 합계", checks)
    _close(float(gap["네장르_동일가중평균_구성비_퍼센트"].sum()), 100.0, 1e-7, "격차 범주 기여율 합계", checks)

    result = {
        "전체통과": all(row["통과"] for row in checks),
        "검사수": len(checks),
        "실패수": sum(not row["통과"] for row in checks),
        "검사결과": checks,
    }
    (output_root / "validation_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not result["전체통과"]:
        failed = [row for row in checks if not row["통과"]]
        raise AssertionError(f"재현 검증 실패: {failed}")
    return result

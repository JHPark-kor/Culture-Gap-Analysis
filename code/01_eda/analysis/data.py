from __future__ import annotations

from pathlib import Path
import re

import numpy as np
import pandas as pd

from .settings import (
    ALL_PURE_ART_GENRES,
    CATEGORICAL_LEVELS,
    CONTROLS,
    EXPLANATORY,
    INCLUSIVE_CUTOFF_WON,
    MODEL_GENRES,
    YEARS,
)

HH_COLUMN = {
    2023: "전체 가구원수(본인 포함)",
    2024: "일반적 특성_전체 동거 가구원 수",
    2025: "전체 동거 가구 수",
}


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def first_field(frame: pd.DataFrame, exact: tuple[str, ...] = (), contains: tuple[str, ...] = ()) -> str:
    for column in exact:
        if column in frame.columns:
            return column
    for column in frame.columns:
        if all(token in column for token in contains):
            return column
    raise KeyError(f"열을 찾지 못했습니다. exact={exact}, contains={contains}")


def genre_field(frame: pd.DataFrame, phrases: tuple[str, ...], genre: str) -> str:
    aliases = ("미술전시회", "미술") if genre == "미술전시회" else (genre,)
    candidates = [
        column for column in frame.columns
        if any(f"<{alias}>" in column for alias in aliases)
        and all(phrase in column for phrase in phrases)
    ]
    if not candidates:
        candidates = [
            column for column in frame.columns
            if any(alias in column for alias in aliases)
            and all(phrase in column for phrase in phrases)
        ]
    if not candidates:
        raise KeyError(f"{genre} / {phrases}: 해당 열을 찾지 못했습니다.")
    return candidates[0]


def binary_yes(frame: pd.DataFrame, phrase: str, genre: str) -> pd.Series:
    values = numeric(frame[genre_field(frame, (phrase,), genre)])
    unexpected = set(values.dropna().unique()) - {1, 2}
    if unexpected:
        raise AssertionError(f"{genre} / {phrase}: 예상 밖 코드 {unexpected}")
    if values.isna().any():
        raise AssertionError(f"{genre} / {phrase}: 결측 {int(values.isna().sum())}건")
    return values.eq(1).astype(int)


def pure_art_direct_count(frame: pd.DataFrame) -> pd.Series:
    values = [
        numeric(frame[genre_field(frame, ("직접관람", "횟수"), genre)]).fillna(0)
        for genre in ALL_PURE_ART_GENRES
    ]
    return pd.concat(values, axis=1).sum(axis=1)


def pure_art_intention(frame: pd.DataFrame) -> pd.Series:
    values = [
        numeric(frame[genre_field(frame, ("향후 1년 이내 직접관람 의향",), genre)]).eq(1)
        for genre in ALL_PURE_ART_GENRES
    ]
    return pd.concat(values, axis=1).any(axis=1).astype(int)


def _income_group(year: int, household: pd.Series, income_band: pd.Series) -> pd.Series:
    household_for_threshold = household.clip(upper=6)
    threshold_code = household_for_threshold.map(INCLUSIVE_CUTOFF_WON[year]) / 100
    return pd.Series(
        np.where(income_band.le(threshold_code), "저소득 집단", "그 외 소득집단"),
        index=household.index,
    )


def survey_files(survey_dir: Path) -> dict[int, Path]:
    result: dict[int, Path] = {}
    for year in YEARS:
        matches = sorted(survey_dir.glob(f"{year}_*.csv"))
        if len(matches) != 1:
            raise FileNotFoundError(
                f"{survey_dir}에서 {year}_*.csv가 1개여야 합니다. 발견={len(matches)}"
            )
        result[year] = matches[0]
    return result


def load_raw_waves(survey_dir: Path) -> dict[int, pd.DataFrame]:
    return {
        year: pd.read_csv(path, encoding="cp949", low_memory=False)
        for year, path in survey_files(survey_dir).items()
    }


def _common_columns(year: int, raw: pd.DataFrame) -> dict[str, pd.Series]:
    weight_col = first_field(
        raw,
        exact=("가중치", "최종가중치_모집단 기준"),
        contains=("가중",),
    )
    age_col = first_field(
        raw,
        exact=("연령", "특성_연령", "일반적 특성_연령"),
        contains=("연령",),
    )
    sex_col = first_field(
        raw,
        exact=("성별", "특성_성별", "일반적 특성_성별"),
        contains=("성별",),
    )
    education_col = first_field(
        raw,
        exact=("학력", "특성_학력", "일반적 특성_학력", "최종학력"),
        contains=("학력",),
    )
    household_col = (
        HH_COLUMN[year]
        if HH_COLUMN[year] in raw.columns
        else first_field(raw, contains=("가구원 수",))
    )
    income_col = first_field(
        raw,
        exact=("가구소득", "특성_가구소득"),
        contains=("가구소득",),
    )
    disability_col = first_field(
        raw,
        exact=("장애등록여부2", "장애등록 여부", "특성_장애등록 여부", "일반적 특성_장애등록 여부"),
        contains=("장애등록",),
    )
    region_col = first_field(
        raw,
        exact=("지역규모", "특성_지역규모"),
        contains=("지역규모",),
    )
    spending_col = first_field(
        raw,
        exact=("지난 1년 동안 문화예술을 향유하기 위해 지출한 비용에 대한 인식",),
        contains=("지출한 비용에 대한 인식",),
    )
    time_col = first_field(
        raw,
        exact=("문화예술행사 향유 시간적 여건",),
        contains=("향유 시간적 여건",),
    )
    physical_col = first_field(
        raw,
        exact=(
            "건강인식 <신체적 건강>",
            "주관적 건강상태_본인의 신체 건강상태 인식",
            "본인의 신체 건강상태 인식",
        ),
        contains=("신체", "건강"),
    )
    mental_col = first_field(
        raw,
        exact=(
            "건강인식 <정신적 건강>",
            "주관적 건강상태_본인의 정신(스트레스, 우울감 등) 건강상태 인식",
            "본인의 정신(스트레스^ 우울감 등) 건강상태 인식",
        ),
        contains=("정신", "건강"),
    )
    social_col = first_field(
        raw,
        exact=(
            "건강인식 <사회적 건강>",
            "주관적 건강상태_본인의 사회적 관계(고립감, 외로움 등) 인식",
            "본인의 사회적 관계(고립감^ 외로움 등) 인식",
        ),
        contains=("사회적",),
    )
    child_ed_col = first_field(
        raw,
        exact=("문화예술교육 과거 경험_유아기 및 아동기",),
        contains=("문화예술교육 과거 경험", "유아기"),
    )
    youth_ed_col = first_field(
        raw,
        exact=("문화예술교육 과거 경험_청소년기",),
        contains=("문화예술교육 과거 경험", "청소년기"),
    )
    household = numeric(raw[household_col])
    income_band = numeric(raw[income_col])
    return {
        "weight": numeric(raw[weight_col]),
        "age": numeric(raw[age_col]),
        "sex": numeric(raw[sex_col]),
        "education": numeric(raw[education_col]),
        "household": household,
        "income_band": income_band,
        "income_group": _income_group(year, household, income_band),
        "disability": numeric(raw[disability_col]),
        "region": numeric(raw[region_col]),
        "spending": numeric(raw[spending_col]),
        "time": numeric(raw[time_col]),
        "physical": numeric(raw[physical_col]),
        "mental": numeric(raw[mental_col]),
        "social": numeric(raw[social_col]),
        "child_ed": numeric(raw[child_ed_col]),
        "youth_ed": numeric(raw[youth_ed_col]),
    }


def build_descriptive_frame(raw_waves: dict[int, pd.DataFrame]) -> pd.DataFrame:
    waves: list[pd.DataFrame] = []
    for year in YEARS:
        raw = raw_waves[year]
        base = _common_columns(year, raw)
        out = pd.DataFrame(index=raw.index)
        out["조사연도"] = str(year)
        out["가중치"] = base["weight"]
        out["소득집단"] = base["income_group"]
        out["관람의향"] = pure_art_intention(raw)
        out["최근관람"] = pure_art_direct_count(raw).gt(0).astype(int)
        waves.append(out)
    frame = pd.concat(waves, ignore_index=True)
    if len(frame) != 30325:
        raise AssertionError(f"전 연령 표본은 30,325명이어야 합니다: {len(frame):,}")
    if frame.isna().any().any():
        raise AssertionError(f"전 연령 분석표 결측: {frame.isna().sum().to_dict()}")
    return frame


def _collapse_low_scores(series: pd.Series) -> pd.Series:
    return series.astype(object).where(~series.isin([1, 2]), "1~2")


def build_model_frame(raw_waves: dict[int, pd.DataFrame]) -> pd.DataFrame:
    waves: list[pd.DataFrame] = []
    for year in YEARS:
        raw = raw_waves[year]
        base = _common_columns(year, raw)
        adult = base["age"].ge(2)
        out = pd.DataFrame(index=raw.index)
        out["가중치"] = base["weight"]
        out["소득집단"] = base["income_group"]
        out["연령대"] = base["age"].map(
            {2: "20대", 3: "30대", 4: "40대", 5: "50대", 6: "60대", 7: "70세 이상"}
        )
        out["성별"] = base["sex"].map({1: "남성", 2: "여성"})
        out["가구원수"] = np.select(
            [base["household"].eq(1), base["household"].eq(2)],
            ["1인", "2인"],
            default="3인 이상",
        )
        out["조사연도"] = str(year)
        out["교육수준"] = base["education"].map(
            {1: "초졸 이하", 2: "중졸", 3: "고졸", 4: "대학 이상"}
        )
        out["아동기예술교육"] = np.where(base["child_ed"].eq(1), "있음", "없음")
        out["청소년기예술교육"] = np.where(base["youth_ed"].eq(1), "있음", "없음")
        out["문화비충분도"] = base["spending"]
        out["시간여건"] = base["time"]
        out["신체건강"] = _collapse_low_scores(base["physical"])
        out["정신건강"] = _collapse_low_scores(base["mental"])
        social_oriented = 8 - base["social"] if year >= 2024 else base["social"]
        out["사회관계"] = _collapse_low_scores(social_oriented)
        out["장애여부"] = np.where(base["disability"].eq(1), "해당 없음", "장애 있음")
        out["지역규모"] = base["region"].map({1: "대도시", 2: "중소도시", 3: "읍면지역"})
        for genre in MODEL_GENRES:
            out[f"의향__{genre}"] = binary_yes(raw, "향후 1년 이내 직접관람 의향", genre)
            out[f"매체__{genre}"] = binary_yes(raw, "매체를 이용한 문화예술행사 관람 경험", genre)
            out[f"교육__{genre}"] = binary_yes(raw, "문화예술교육 경험 여부", genre)
        waves.append(out.loc[adult].reset_index(drop=True))

    frame = pd.concat(waves, ignore_index=True)
    if len(frame) != 28779:
        raise AssertionError(f"성인 표본은 28,779명이어야 합니다: {len(frame):,}")
    required = ["가중치", *CONTROLS]
    required += [v for v in EXPLANATORY if v not in ("장르매체경험", "장르최근예술교육")]
    for genre in MODEL_GENRES:
        required += [f"의향__{genre}", f"매체__{genre}", f"교육__{genre}"]
    missing = frame[required].isna().sum()
    if missing.gt(0).any():
        raise AssertionError(f"성인 분석표 필수 변수 결측: {missing[missing.gt(0)].to_dict()}")
    for variable, levels in CATEGORICAL_LEVELS.items():
        if variable in ("장르매체경험", "장르최근예술교육"):
            continue
        unexpected = set(frame[variable].unique()) - set(levels)
        if unexpected:
            raise AssertionError(f"{variable} 예상 밖 범주: {unexpected}")
    return frame


def clean_usage_field(column: object) -> str:
    return re.sub(r"\s*\(건\)\s*$", "", str(column).replace("\n", " ")).strip()

#!/usr/bin/env python3
"""Create one Seoul performance-facility record per exact address/coordinate cluster."""

from __future__ import annotations

import argparse
import hashlib
import re
import unicodedata
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = PROJECT_ROOT / Path(
    "data/raw/spatial/accessibility/facilities/seoul_show_facilities.csv"
)
DEFAULT_OUTPUT_DIR = Path("outputs/fixed_accessibility_inputs/facilities")


def normalize_address(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value))).strip()


def make_cluster_id(address: str, latitude: float, longitude: float) -> str:
    key = f"{address}|{latitude:.10f}|{longitude:.10f}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12].upper()
    return f"PFCL_{digest}"


def join_unique(values: pd.Series) -> str:
    return " | ".join(dict.fromkeys(str(value) for value in values if pd.notna(value)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    facilities = pd.read_csv(args.input, encoding="utf-8-sig")
    required = {"내부번호", "공연시설명", "주소_원문", "위도", "경도"}
    missing = required.difference(facilities.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    if facilities["내부번호"].duplicated().any():
        raise ValueError("내부번호 must be unique before clustering")
    if facilities[["주소_원문", "위도", "경도"]].isna().any().any():
        raise ValueError("주소_원문, 위도, 경도 must be populated for every Seoul facility")

    facilities = facilities.copy()
    facilities["주소_정규화"] = facilities["주소_원문"].map(normalize_address)
    facilities["위도"] = facilities["위도"].astype(float)
    facilities["경도"] = facilities["경도"].astype(float)
    key_columns = ["주소_정규화", "위도", "경도"]
    facilities["facility_cluster_id"] = [
        make_cluster_id(address, latitude, longitude)
        for address, latitude, longitude in facilities[key_columns].itertuples(
            index=False, name=None
        )
    ]

    cluster_rows: list[dict[str, object]] = []
    for cluster_id, group in facilities.groupby("facility_cluster_id", sort=False):
        first = group.iloc[0]
        names = join_unique(group["공연시설명"])
        ids = join_unique(group["내부번호"])
        cluster_rows.append(
            {
                "facility_cluster_id": cluster_id,
                "공연시설명": names,
                "주소_원문": first["주소_원문"],
                "주소_정규화": first["주소_정규화"],
                "위도": float(first["위도"]),
                "경도": float(first["경도"]),
                "원본시설수": int(len(group)),
                "원본내부번호목록": ids,
                "원본공연시설명목록": names,
                "통합여부": bool(len(group) > 1),
                "통합기준": "주소_정규화+위도완전일치+경도완전일치",
            }
        )
    clusters = pd.DataFrame(cluster_rows)

    cluster_lookup = clusters.set_index("facility_cluster_id")
    mapping = facilities[
        ["내부번호", "공연시설명", "주소_원문", "위도", "경도", "facility_cluster_id"]
    ].copy()
    mapping["cluster_공연시설명"] = mapping["facility_cluster_id"].map(
        cluster_lookup["공연시설명"]
    )
    mapping["cluster_원본시설수"] = mapping["facility_cluster_id"].map(
        cluster_lookup["원본시설수"]
    )
    mapping["통합대상여부"] = mapping["cluster_원본시설수"].gt(1)

    if len(mapping) != len(facilities):
        raise AssertionError("Mapping row count changed")
    if mapping["내부번호"].nunique() != len(facilities):
        raise AssertionError("Mapping does not retain every source facility exactly once")
    if clusters["facility_cluster_id"].duplicated().any():
        raise AssertionError("Cluster IDs are not unique")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    cluster_path = args.output_dir / "seoul_show_facilities_deduplicated.csv"
    mapping_path = args.output_dir / "seoul_show_facility_cluster_mapping.csv"
    clusters.to_csv(cluster_path, index=False, encoding="utf-8-sig")
    mapping.to_csv(mapping_path, index=False, encoding="utf-8-sig")

    merged_groups = int(clusters["통합여부"].sum())
    merged_source_rows = int(mapping["통합대상여부"].sum())
    print(f"source_rows={len(facilities)}")
    print(f"cluster_rows={len(clusters)}")
    print(f"merged_groups={merged_groups}")
    print(f"source_rows_in_merged_groups={merged_source_rows}")
    print(f"cluster_output={cluster_path}")
    print(f"mapping_output={mapping_path}")


if __name__ == "__main__":
    main()

"""Validate the fixed grid-to-facility transit path outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


DEFAULT_PATHS = Path(
    "outputs/fixed_accessibility_inputs/transit_paths/"
    "transit_grid_facility_paths_90min_walk15min.parquet"
)
DEFAULT_GRID_SUMMARY = Path(
    "outputs/fixed_accessibility_inputs/transit_paths/transit_grid_category_summary.csv"
)
DEFAULT_BUILD_SUMMARY = Path(
    "outputs/fixed_accessibility_inputs/transit_paths/transit_path_build_summary.json"
)
DEFAULT_FACILITIES = Path(
    "outputs/fixed_accessibility_inputs/network_snap/facility_walk_node_snap.parquet"
)
DEFAULT_GRIDS = Path(
    "outputs/fixed_accessibility_inputs/network_snap/grid_walk_node_snap.parquet"
)
DEFAULT_OUTPUT = Path(
    "outputs/fixed_accessibility_inputs/transit_paths/transit_path_validation.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths", type=Path, default=DEFAULT_PATHS)
    parser.add_argument("--grid-summary", type=Path, default=DEFAULT_GRID_SUMMARY)
    parser.add_argument("--build-summary", type=Path, default=DEFAULT_BUILD_SUMMARY)
    parser.add_argument("--facilities", type=Path, default=DEFAULT_FACILITIES)
    parser.add_argument("--grids", type=Path, default=DEFAULT_GRIDS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    parquet_file = pq.ParquetFile(args.paths)
    grids = pd.read_parquet(args.grids, columns=["GRID_CD", "취약노인수"])
    eligible_grid_ids = set(grids.loc[grids["취약노인수"] > 0, "GRID_CD"].astype(str))
    columns = [
        "GRID_CD",
        "facility_id",
        "facility_category",
        "movement_time_excluding_wait_min",
        "total_expected_wait_min",
        "journey_time_with_wait_min",
        "total_transit_walk_time_min",
        "transfer_count",
        "distance_burden",
        "slope_burden",
        "transfer_burden",
        "wait_burden",
        "transport_burden",
        "generalized_cost_transit",
    ]
    burden_columns = [
        "distance_burden",
        "slope_burden",
        "transfer_burden",
        "wait_burden",
        "transport_burden",
        "generalized_cost_transit",
    ]

    issues: list[str] = []
    seen_facilities: set[str] = set()
    path_rows = 0
    paths_over_total_walk_limit = 0
    ineligible_grid_path_rows = 0
    null_cells = 0
    duplicate_grid_facility_pairs = 0
    negative_transfer_counts = 0
    maximum_time_error = 0.0
    journey_min = np.inf
    journey_max = -np.inf
    burden_ranges = {
        column: [np.inf, -np.inf] for column in burden_columns
    }
    rows_by_category: dict[str, int] = {}

    for row_group_number in range(parquet_file.num_row_groups):
        frame = parquet_file.read_row_group(
            row_group_number, columns=columns
        ).to_pandas()
        path_rows += len(frame)
        ineligible_grid_path_rows += int(
            (~frame["GRID_CD"].astype(str).isin(eligible_grid_ids)).sum()
        )
        null_cells += int(frame.isna().sum().sum())
        duplicate_grid_facility_pairs += int(
            frame[["GRID_CD", "facility_id"]].duplicated().sum()
        )
        facility_values = frame["facility_id"].astype(str).unique()
        if len(facility_values) != 1:
            issues.append(
                f"row group {row_group_number} contains {len(facility_values)} facilities"
            )
        else:
            facility_id = str(facility_values[0])
            if facility_id in seen_facilities:
                issues.append(f"facility {facility_id} appears in multiple row groups")
            seen_facilities.add(facility_id)
        negative_transfer_counts += int((frame["transfer_count"] < 0).sum())
        time_error = (
            frame["journey_time_with_wait_min"]
            - frame["movement_time_excluding_wait_min"]
            - frame["total_expected_wait_min"]
        ).abs()
        maximum_time_error = max(maximum_time_error, float(time_error.max()))
        journey_min = min(journey_min, float(frame["journey_time_with_wait_min"].min()))
        journey_max = max(journey_max, float(frame["journey_time_with_wait_min"].max()))
        paths_over_total_walk_limit += int(
            (frame["total_transit_walk_time_min"] > 15.0 + 1e-9).sum()
        )
        for column in burden_columns:
            burden_ranges[column][0] = min(
                burden_ranges[column][0], float(frame[column].min())
            )
            burden_ranges[column][1] = max(
                burden_ranges[column][1], float(frame[column].max())
            )
        for category, count in frame["facility_category"].value_counts().items():
            rows_by_category[str(category)] = rows_by_category.get(str(category), 0) + int(
                count
            )

    grid_summary = pd.read_csv(args.grid_summary, dtype={"GRID_CD": "string"})
    build_summary = json.loads(args.build_summary.read_text(encoding="utf-8"))
    facilities = pd.read_parquet(args.facilities, columns=["facility_id"])
    all_facilities = set(facilities["facility_id"].astype(str))
    facilities_without_paths = sorted(all_facilities - seen_facilities)
    summary_count = int(
        grid_summary["transit_facility_count_90min_walk15min"].sum()
    )

    if null_cells:
        issues.append(f"path table contains {null_cells} null cells")
    if duplicate_grid_facility_pairs:
        issues.append(
            f"path table contains {duplicate_grid_facility_pairs} duplicate grid-facility pairs"
        )
    if negative_transfer_counts:
        issues.append(f"path table contains {negative_transfer_counts} negative transfer counts")
    if journey_max > 90.0 + 1e-9:
        issues.append(f"maximum journey time exceeds 90 minutes: {journey_max}")
    if paths_over_total_walk_limit:
        issues.append(
            f"{paths_over_total_walk_limit} paths exceed 15 minutes of total walking"
        )
    if ineligible_grid_path_rows:
        issues.append(
            f"{ineligible_grid_path_rows} paths use a grid with 취약노인수 <= 0"
        )
    if (pd.to_numeric(grids["취약노인수"], errors="coerce") <= 0).any():
        issues.append("grid input contains 취약노인수 <= 0")
    if maximum_time_error > 1e-7:
        issues.append(f"path component time mismatch reaches {maximum_time_error}")
    for column, (minimum, maximum) in burden_ranges.items():
        if minimum < -1e-12 or maximum > 1.0 + 1e-12:
            issues.append(f"{column} is outside [0, 1]: {minimum}, {maximum}")
    if path_rows != int(build_summary["path_rows"]):
        issues.append("path row count does not match the build summary")
    if summary_count != path_rows:
        issues.append("90-minute/15-minute grid summary counts do not reconcile")
    if len(facilities_without_paths) != int(
        build_summary["facilities_without_transit_path"]
    ):
        issues.append("facility coverage does not match the build summary")

    result = {
        "status": "passed" if not issues else "failed",
        "path_rows": int(path_rows),
        "parquet_row_groups": int(parquet_file.num_row_groups),
        "facilities_with_paths": int(len(seen_facilities)),
        "facilities_without_paths": facilities_without_paths,
        "duplicate_grid_facility_pairs": int(duplicate_grid_facility_pairs),
        "null_cells": int(null_cells),
        "journey_time_min": float(journey_min),
        "journey_time_max": float(journey_max),
        "paths_over_15_minutes_total_walking": int(paths_over_total_walk_limit),
        "ineligible_grid_path_rows": int(ineligible_grid_path_rows),
        "maximum_component_time_error_min": float(maximum_time_error),
        "burden_ranges": {
            key: [float(value[0]), float(value[1])]
            for key, value in burden_ranges.items()
        },
        "rows_by_category": rows_by_category,
        "summary_count_90min_walk15min": summary_count,
        "issues": issues,
    }
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if issues:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

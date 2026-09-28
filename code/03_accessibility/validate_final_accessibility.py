"""Independently validate best paths and beta accessibility aggregation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


DEFAULT_PATHS = Path(
    "outputs/fixed_accessibility_inputs/final_accessibility/"
    "grid_facility_best_paths_mode_limits.parquet"
)
DEFAULT_GRID_ACCESSIBILITY = Path(
    "outputs/fixed_accessibility_inputs/final_accessibility/"
    "grid_category_accessibility_beta_sensitivity.csv"
)
DEFAULT_GRID_SNAP = Path(
    "outputs/fixed_accessibility_inputs/network_snap/grid_walk_node_snap.parquet"
)
DEFAULT_OUTPUT = Path(
    "outputs/fixed_accessibility_inputs/final_accessibility/"
    "final_accessibility_validation.json"
)

BETA_VALUES = (2, 3, 4)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths", type=Path, default=DEFAULT_PATHS)
    parser.add_argument(
        "--grid-accessibility", type=Path, default=DEFAULT_GRID_ACCESSIBILITY
    )
    parser.add_argument("--grid-snap", type=Path, default=DEFAULT_GRID_SNAP)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    parquet_file = pq.ParquetFile(args.paths)
    grid_table = pd.read_parquet(
        args.grid_snap, columns=["GRID_CD", "취약노인수"]
    )
    if (pd.to_numeric(grid_table["취약노인수"], errors="coerce") <= 0).any():
        raise RuntimeError("Grid snap input contains 취약노인수 <= 0")
    grid_ids = grid_table["GRID_CD"].astype(str)
    grid_to_code = pd.Series(
        np.arange(len(grid_ids), dtype=np.int32), index=grid_ids
    )
    accessibility = pd.read_csv(
        args.grid_accessibility,
        dtype={"GRID_CD": "string", "facility_category": "string"},
    )
    if len(accessibility) != len(grid_ids):
        raise RuntimeError("Accessibility output must contain one row per grid")
    if accessibility["GRID_CD"].duplicated().any():
        raise RuntimeError("Accessibility output contains duplicate GRID_CD rows")
    if set(accessibility["facility_category"].astype(str)) != {"전체"}:
        raise RuntimeError("Accessibility output facility_category must be 전체")
    grid_count = len(grid_ids)
    aggregate_size = grid_count
    walk_candidate_count = np.zeros(aggregate_size, dtype=np.int64)
    transit_candidate_count = np.zeros(aggregate_size, dtype=np.int64)
    feasible_count = np.zeros(aggregate_size, dtype=np.int64)
    walk_count = np.zeros(aggregate_size, dtype=np.int64)
    transit_count = np.zeros(aggregate_size, dtype=np.int64)
    beta_sums = {
        beta: np.zeros(aggregate_size, dtype=np.float64) for beta in BETA_VALUES
    }

    columns = [
        "GRID_CD",
        "facility_id",
        "facility_category",
        "selected_mode",
        "candidate_walk_available",
        "candidate_transit_available",
        "candidate_walk_generalized_cost",
        "candidate_transit_generalized_cost",
        "generalized_cost",
        "journey_time_with_wait_min",
        "total_walk_time_min",
        "movement_time_excluding_wait_min",
        "total_expected_wait_min",
        "transfer_count",
        "distance_burden",
        "slope_burden",
        "transport_burden",
        "access_stop_id",
        "first_pattern_id",
        "egress_stop_id",
    ]
    required_columns = [
        "GRID_CD",
        "facility_id",
        "facility_category",
        "selected_mode",
        "generalized_cost",
        "journey_time_with_wait_min",
        "total_walk_time_min",
        "movement_time_excluding_wait_min",
        "total_expected_wait_min",
        "transfer_count",
        "distance_burden",
        "slope_burden",
        "transport_burden",
    ]
    issues: list[str] = []
    seen_facilities: set[str] = set()
    total_rows = 0
    duplicate_pairs = 0
    required_null_cells = 0
    mode_candidate_errors = 0
    selected_cost_errors = 0
    generalized_cost_errors = 0
    journey_time_errors = 0
    mode_limit_errors = 0
    mode_detail_errors = 0
    burden_min = np.inf
    burden_max = -np.inf

    for row_group_number in range(parquet_file.num_row_groups):
        frame = parquet_file.read_row_group(
            row_group_number, columns=columns
        ).to_pandas()
        total_rows += len(frame)
        duplicate_pairs += int(frame[["GRID_CD", "facility_id"]].duplicated().sum())
        required_null_cells += int(frame[required_columns].isna().sum().sum())
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

        walk_available = frame["candidate_walk_available"].to_numpy(dtype=bool)
        transit_available = frame["candidate_transit_available"].to_numpy(dtype=bool)
        walk_cost = frame["candidate_walk_generalized_cost"].to_numpy(dtype=float)
        transit_cost = frame["candidate_transit_generalized_cost"].to_numpy(dtype=float)
        selected_mode = frame["selected_mode"].astype(str).to_numpy()
        selected_cost = frame["generalized_cost"].to_numpy(dtype=float)
        is_walk = selected_mode == "walk"
        is_transit = selected_mode == "transit"
        mode_candidate_errors += int(
            ((walk_available != np.isfinite(walk_cost))
             | (transit_available != np.isfinite(transit_cost))
             | (is_walk & ~walk_available)
             | (is_transit & ~transit_available)
             | ~(is_walk | is_transit)).sum()
        )
        chosen_candidate_cost = np.where(is_walk, walk_cost, transit_cost)
        minimum_candidate_cost = np.fmin(walk_cost, transit_cost)
        selected_cost_errors += int(
            (
                ~np.isclose(selected_cost, chosen_candidate_cost, rtol=0.0, atol=1e-12)
                | ~np.isclose(selected_cost, minimum_candidate_cost, rtol=0.0, atol=1e-12)
            ).sum()
        )
        expected_generalized = (
            frame["distance_burden"].to_numpy(dtype=float)
            + frame["slope_burden"].to_numpy(dtype=float)
            + frame["transport_burden"].to_numpy(dtype=float)
        ) / 3.0
        generalized_cost_errors += int(
            (~np.isclose(selected_cost, expected_generalized, rtol=0.0, atol=1e-12)).sum()
        )
        journey = frame["journey_time_with_wait_min"].to_numpy(dtype=float)
        total_walk = frame["total_walk_time_min"].to_numpy(dtype=float)
        component_journey = (
            frame["movement_time_excluding_wait_min"].to_numpy(dtype=float)
            + frame["total_expected_wait_min"].to_numpy(dtype=float)
        )
        journey_time_errors += int(
            (~np.isclose(journey, component_journey, rtol=0.0, atol=1e-9)).sum()
        )
        mode_limit_errors += int(
            (
                (is_walk & (journey > 20.0 + 1e-9))
                | (is_walk & ~np.isclose(total_walk, journey, rtol=0.0, atol=1e-9))
                | (is_transit & (journey > 90.0 + 1e-9))
                | (is_transit & (total_walk > 15.0 + 1e-9))
            ).sum()
        )
        has_transit_detail = (
            frame["access_stop_id"].notna().to_numpy()
            & frame["first_pattern_id"].notna().to_numpy()
            & frame["egress_stop_id"].notna().to_numpy()
        )
        mode_detail_errors += int(
            ((is_transit & ~has_transit_detail) | (is_walk & has_transit_detail)).sum()
        )
        burdens = frame[
            ["distance_burden", "slope_burden", "transport_burden"]
        ].to_numpy(dtype=float)
        burden_min = min(burden_min, float(burdens.min()))
        burden_max = max(burden_max, float(burdens.max()))

        grid_codes = frame["GRID_CD"].astype(str).map(grid_to_code)
        if grid_codes.isna().any():
            issues.append(f"row group {row_group_number} contains unknown grids")
            continue
        flat_codes = grid_codes.to_numpy(dtype=np.int32)
        np.add.at(feasible_count, flat_codes, 1)
        np.add.at(walk_candidate_count, flat_codes[walk_available], 1)
        np.add.at(transit_candidate_count, flat_codes[transit_available], 1)
        np.add.at(walk_count, flat_codes[is_walk], 1)
        np.add.at(transit_count, flat_codes[is_transit], 1)
        for beta in BETA_VALUES:
            np.add.at(beta_sums[beta], flat_codes, np.exp(-beta * selected_cost))

    expected_flat_codes = accessibility["GRID_CD"].astype(str).map(
        grid_to_code
    ).to_numpy(dtype=np.int32)
    aggregate_differences = {
        "walk_facility_count_20min": int(
            np.count_nonzero(
                walk_candidate_count[expected_flat_codes]
                != accessibility["walk_facility_count_20min"].to_numpy(
                    dtype=np.int64
                )
            )
        ),
        "transit_facility_count_90min_walk15min": int(
            np.count_nonzero(
                transit_candidate_count[expected_flat_codes]
                != accessibility[
                    "transit_facility_count_90min_walk15min"
                ].to_numpy(dtype=np.int64)
            )
        ),
        "facility_count_any_feasible_mode": int(
            np.count_nonzero(
                feasible_count[expected_flat_codes]
                != accessibility["facility_count_any_feasible_mode"].to_numpy(
                    dtype=np.int64
                )
            )
        ),
        "selected_walk_path_count": int(
            np.count_nonzero(
                walk_count[expected_flat_codes]
                != accessibility["selected_walk_path_count"].to_numpy(dtype=np.int64)
            )
        ),
        "selected_transit_path_count": int(
            np.count_nonzero(
                transit_count[expected_flat_codes]
                != accessibility["selected_transit_path_count"].to_numpy(dtype=np.int64)
            )
        ),
    }
    beta_max_abs_differences: dict[str, float] = {}
    for beta in BETA_VALUES:
        column = f"accessibility_beta_{beta}"
        beta_max_abs_differences[column] = float(
            np.max(
                np.abs(
                    beta_sums[beta][expected_flat_codes]
                    - accessibility[column].to_numpy(dtype=float)
                )
            )
        )

    overall_facility_count = accessibility["overall_facility_count"].to_numpy(
        dtype=np.int64
    )
    if np.any(overall_facility_count <= 0):
        issues.append("overall_facility_count must be positive")
    overall_beta_3_by_grid = beta_sums[3]
    expected_overall_sum = overall_beta_3_by_grid[
        accessibility["GRID_CD"].astype(str).map(grid_to_code).to_numpy(dtype=np.int32)
    ]
    expected_overall_normalized = expected_overall_sum / overall_facility_count
    overall_differences = {
        "overall_accessibility_beta_3_sum": float(
            np.max(
                np.abs(
                    expected_overall_sum
                    - accessibility["overall_accessibility_beta_3_sum"].to_numpy(
                        dtype=float
                    )
                )
            )
        ),
        "overall_accessibility_beta_3_normalized_0_1": float(
            np.max(
                np.abs(
                    expected_overall_normalized
                    - accessibility[
                        "overall_accessibility_beta_3_normalized_0_1"
                    ].to_numpy(dtype=float)
                )
            )
        ),
        "overall_accessibility_beta_3_percent_0_100": float(
            np.max(
                np.abs(
                    100.0 * expected_overall_normalized
                    - accessibility[
                        "overall_accessibility_beta_3_percent_0_100"
                    ].to_numpy(dtype=float)
                )
            )
        ),
        "overall_accessibility_deficit_0_1": float(
            np.max(
                np.abs(
                    1.0
                    - expected_overall_normalized
                    - accessibility["overall_accessibility_deficit_0_1"].to_numpy(
                        dtype=float
                    )
                )
            )
        ),
    }

    if duplicate_pairs:
        issues.append(f"found {duplicate_pairs} duplicate grid-facility pairs")
    if required_null_cells:
        issues.append(f"found {required_null_cells} null cells in required fields")
    if mode_candidate_errors:
        issues.append(f"found {mode_candidate_errors} mode/candidate availability errors")
    if selected_cost_errors:
        issues.append(f"found {selected_cost_errors} selected-cost errors")
    if generalized_cost_errors:
        issues.append(f"found {generalized_cost_errors} generalized-cost errors")
    if journey_time_errors:
        issues.append(f"found {journey_time_errors} journey-time errors")
    if mode_limit_errors:
        issues.append(f"found {mode_limit_errors} mode-specific time-limit errors")
    if mode_detail_errors:
        issues.append(f"found {mode_detail_errors} mode-detail errors")
    if burden_min < -1e-12 or burden_max > 1.0 + 1e-12:
        issues.append(f"burdens are outside [0, 1]: {burden_min}, {burden_max}")
    for column, count in aggregate_differences.items():
        if count:
            issues.append(f"{column} differs in {count} grid-category rows")
    for column, difference in beta_max_abs_differences.items():
        if difference > 1e-9:
            issues.append(f"{column} maximum aggregation difference is {difference}")
    for column, difference in overall_differences.items():
        if difference > 1e-9:
            issues.append(f"{column} maximum aggregation difference is {difference}")

    result = {
        "status": "passed" if not issues else "failed",
        "path_rows": int(total_rows),
        "row_groups": int(parquet_file.num_row_groups),
        "facilities_with_paths": int(len(seen_facilities)),
        "duplicate_grid_facility_pairs": int(duplicate_pairs),
        "required_null_cells": int(required_null_cells),
        "mode_candidate_errors": int(mode_candidate_errors),
        "selected_cost_errors": int(selected_cost_errors),
        "generalized_cost_errors": int(generalized_cost_errors),
        "journey_time_errors": int(journey_time_errors),
        "mode_limit_errors": int(mode_limit_errors),
        "mode_detail_errors": int(mode_detail_errors),
        "burden_range": [float(burden_min), float(burden_max)],
        "aggregate_row_differences": aggregate_differences,
        "beta_max_absolute_differences": beta_max_abs_differences,
        "overall_accessibility_max_absolute_differences": overall_differences,
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

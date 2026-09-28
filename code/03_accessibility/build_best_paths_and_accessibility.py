"""Prioritize feasible walking paths, then calculate beta sensitivity.

Exhibition facilities are intentionally absent until their dataset is supplied.
The current categories are performance and sports viewing facilities.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


DEFAULT_WALK_PATHS = Path(
    "outputs/fixed_accessibility_inputs/walk_paths/"
    "walk_grid_facility_paths_20min.parquet"
)
DEFAULT_TRANSIT_PATHS = Path(
    "outputs/fixed_accessibility_inputs/transit_paths/"
    "transit_grid_facility_paths_90min_walk15min.parquet"
)
DEFAULT_GRID_SNAP = Path(
    "outputs/fixed_accessibility_inputs/network_snap/grid_walk_node_snap.parquet"
)
DEFAULT_FACILITY_SNAP = Path(
    "outputs/fixed_accessibility_inputs/network_snap/facility_walk_node_snap.parquet"
)
DEFAULT_OUTPUT_DIR = Path("outputs/fixed_accessibility_inputs/final_accessibility")

BETA_VALUES = (2.0, 3.0, 4.0)


FINAL_SCHEMA = pa.schema(
    [
        ("GRID_CD", pa.string()),
        ("facility_id", pa.string()),
        ("facility_category", pa.string()),
        ("selected_mode", pa.string()),
        ("candidate_walk_available", pa.bool_()),
        ("candidate_transit_available", pa.bool_()),
        ("candidate_walk_generalized_cost", pa.float64()),
        ("candidate_transit_generalized_cost", pa.float64()),
        ("generalized_cost", pa.float64()),
        ("journey_time_with_wait_min", pa.float64()),
        ("total_walk_time_min", pa.float64()),
        ("movement_time_excluding_wait_min", pa.float64()),
        ("total_expected_wait_min", pa.float64()),
        ("transfer_count", pa.int16()),
        ("distance_burden", pa.float64()),
        ("slope_burden", pa.float64()),
        ("transport_burden", pa.float64()),
        ("known_total_walk_distance_m", pa.float64()),
        ("path_mean_abs_slope_deg", pa.float64()),
        ("path_max_abs_slope_deg", pa.float64()),
        ("path_steep_ge_8deg_share", pa.float64()),
        ("access_stop_id", pa.string()),
        ("first_pattern_id", pa.string()),
        ("egress_stop_id", pa.string()),
    ]
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--walk-paths", type=Path, default=DEFAULT_WALK_PATHS)
    parser.add_argument("--transit-paths", type=Path, default=DEFAULT_TRANSIT_PATHS)
    parser.add_argument("--grid-snap", type=Path, default=DEFAULT_GRID_SNAP)
    parser.add_argument("--facility-snap", type=Path, default=DEFAULT_FACILITY_SNAP)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def _normalize_walk(frame: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame(
        {
            "GRID_CD": frame["GRID_CD"].astype(str),
            "facility_id": frame["facility_id"].astype(str),
            "facility_category": frame["facility_category"].astype(str),
            "selected_mode": "walk",
            "generalized_cost": frame["generalized_cost_walk"].astype(float),
            "journey_time_with_wait_min": frame["elderly_walk_time_min"].astype(float),
            "total_walk_time_min": frame["elderly_walk_time_min"].astype(float),
            "movement_time_excluding_wait_min": frame["elderly_walk_time_min"].astype(
                float
            ),
            "total_expected_wait_min": 0.0,
            "transfer_count": np.int16(0),
            "distance_burden": frame["distance_burden"].astype(float),
            "slope_burden": frame["slope_burden"].astype(float),
            "transport_burden": 0.0,
            "known_total_walk_distance_m": frame["total_walk_distance_m"].astype(float),
            "path_mean_abs_slope_deg": frame["path_mean_abs_slope_deg"].astype(float),
            "path_max_abs_slope_deg": frame["path_max_abs_slope_deg"].astype(float),
            "path_steep_ge_8deg_share": frame["path_steep_ge_8deg_share"].astype(float),
            "access_stop_id": None,
            "first_pattern_id": None,
            "egress_stop_id": None,
            "mode_priority": np.int8(0),
        }
    )
    return result


def _normalize_transit(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "GRID_CD": frame["GRID_CD"].astype(str),
            "facility_id": frame["facility_id"].astype(str),
            "facility_category": frame["facility_category"].astype(str),
            "selected_mode": "transit",
            "generalized_cost": frame["generalized_cost_transit"].astype(float),
            "journey_time_with_wait_min": frame["journey_time_with_wait_min"].astype(float),
            "total_walk_time_min": frame[
                "total_transit_walk_time_min"
            ].astype(float),
            "movement_time_excluding_wait_min": frame[
                "movement_time_excluding_wait_min"
            ].astype(float),
            "total_expected_wait_min": frame["total_expected_wait_min"].astype(float),
            "transfer_count": frame["transfer_count"].astype(np.int16),
            "distance_burden": frame["distance_burden"].astype(float),
            "slope_burden": frame["slope_burden"].astype(float),
            "transport_burden": frame["transport_burden"].astype(float),
            "known_total_walk_distance_m": frame[
                "known_total_walk_distance_m"
            ].astype(float),
            "path_mean_abs_slope_deg": frame["path_mean_abs_slope_deg"].astype(float),
            "path_max_abs_slope_deg": frame["path_max_abs_slope_deg"].astype(float),
            "path_steep_ge_8deg_share": frame["path_steep_ge_8deg_share"].astype(float),
            "access_stop_id": frame["access_stop_id"].astype(str),
            "first_pattern_id": frame["first_pattern_id"].astype(str),
            "egress_stop_id": frame["egress_stop_id"].astype(str),
            "mode_priority": np.int8(1),
        }
    )


def _row_group_facility_map(parquet_file: pq.ParquetFile) -> dict[str, int]:
    result: dict[str, int] = {}
    for row_group_number in range(parquet_file.num_row_groups):
        values = parquet_file.read_row_group(
            row_group_number, columns=["facility_id"]
        )["facility_id"].unique()
        if len(values) != 1:
            raise RuntimeError(
                f"Transit row group {row_group_number} contains multiple facilities"
            )
        facility_id = str(values[0].as_py())
        if facility_id in result:
            raise RuntimeError(f"Transit facility {facility_id} has multiple row groups")
        result[facility_id] = row_group_number
    return result


def _write_final_frame(
    frame: pd.DataFrame,
    writer: pq.ParquetWriter | None,
    output_path: Path,
) -> pq.ParquetWriter:
    table = pa.Table.from_pandas(
        frame[FINAL_SCHEMA.names], schema=FINAL_SCHEMA, preserve_index=False
    )
    if writer is None:
        writer = pq.ParquetWriter(output_path, FINAL_SCHEMA, compression="zstd")
    writer.write_table(table)
    return writer


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "grid_facility_best_paths_mode_limits.parquet"
    grid_output_path = (
        args.output_dir / "grid_category_accessibility_beta_sensitivity.csv"
    )

    walk_columns = [
        "GRID_CD",
        "facility_id",
        "facility_category",
        "total_walk_distance_m",
        "elderly_walk_time_min",
        "path_mean_abs_slope_deg",
        "slope_burden",
        "path_max_abs_slope_deg",
        "path_steep_ge_8deg_share",
        "distance_burden",
        "generalized_cost_walk",
    ]
    transit_columns = [
        "GRID_CD",
        "facility_id",
        "facility_category",
        "access_stop_id",
        "first_pattern_id",
        "egress_stop_id",
        "known_total_walk_distance_m",
        "movement_time_excluding_wait_min",
        "total_expected_wait_min",
        "journey_time_with_wait_min",
        "total_transit_walk_time_min",
        "transfer_count",
        "path_mean_abs_slope_deg",
        "slope_burden",
        "path_max_abs_slope_deg",
        "path_steep_ge_8deg_share",
        "distance_burden",
        "transport_burden",
        "generalized_cost_transit",
    ]
    walk = pd.read_parquet(args.walk_paths, columns=walk_columns)
    if walk[["GRID_CD", "facility_id"]].duplicated().any():
        raise RuntimeError("Walking candidates contain duplicate grid-facility pairs")
    walk_groups = walk.groupby(walk["facility_id"].astype(str), sort=False).indices
    transit_file = pq.ParquetFile(args.transit_paths)
    transit_row_groups = _row_group_facility_map(transit_file)
    grids = pd.read_parquet(args.grid_snap).reset_index(drop=True)
    facilities = pd.read_parquet(args.facility_snap).reset_index(drop=True)
    if "취약노인수" not in grids.columns:
        raise RuntimeError("Grid snap input is missing 취약노인수")
    if (pd.to_numeric(grids["취약노인수"], errors="raise") <= 0).any():
        raise RuntimeError("Grid snap input contains grids with 취약노인수 <= 0")
    grid_to_code = pd.Series(
        np.arange(len(grids), dtype=np.int32), index=grids["GRID_CD"].astype(str)
    )

    categories = facilities["facility_category"].drop_duplicates().astype(str).tolist()
    stats: dict[str, dict[str, np.ndarray]] = {}
    for category in categories:
        stats[category] = {
            "walk_candidate": np.zeros(len(grids), dtype=np.int32),
            "transit_candidate": np.zeros(len(grids), dtype=np.int32),
            "feasible_any": np.zeros(len(grids), dtype=np.int32),
            "walk_selected": np.zeros(len(grids), dtype=np.int32),
            "transit_selected": np.zeros(len(grids), dtype=np.int32),
            "nearest_cost": np.full(len(grids), np.inf, dtype=np.float64),
            "nearest_cost_time": np.full(len(grids), np.nan, dtype=np.float64),
            "nearest_facility": np.full(len(grids), "", dtype=object),
            "nearest_mode": np.full(len(grids), "", dtype=object),
            "minimum_time": np.full(len(grids), np.inf, dtype=np.float64),
            **{
                f"accessibility_beta_{int(beta)}": np.zeros(
                    len(grids), dtype=np.float64
                )
                for beta in BETA_VALUES
            },
        }

    writer: pq.ParquetWriter | None = None
    written_rows = 0
    walk_selected_rows = 0
    transit_selected_rows = 0
    both_candidate_rows = 0
    facilities_without_any_path = 0

    try:
        for facility_number, facility in enumerate(facilities.itertuples(index=False), 1):
            facility_id = str(facility.facility_id)
            walk_indices = walk_groups.get(facility_id)
            if walk_indices is None:
                walk_normalized = pd.DataFrame()
            else:
                walk_normalized = _normalize_walk(
                    walk.iloc[np.asarray(walk_indices, dtype=np.int64)].reset_index(drop=True)
                )
            transit_row_group = transit_row_groups.get(facility_id)
            if transit_row_group is None:
                transit_normalized = pd.DataFrame()
            else:
                transit_frame = transit_file.read_row_group(
                    transit_row_group, columns=transit_columns
                ).to_pandas()
                transit_normalized = _normalize_transit(transit_frame)

            if walk_normalized.empty and transit_normalized.empty:
                facilities_without_any_path += 1
                continue

            walk_cost = (
                walk_normalized.set_index("GRID_CD")["generalized_cost"]
                if not walk_normalized.empty
                else pd.Series(dtype=float)
            )
            transit_cost = (
                transit_normalized.set_index("GRID_CD")["generalized_cost"]
                if not transit_normalized.empty
                else pd.Series(dtype=float)
            )
            candidates = pd.concat(
                [walk_normalized, transit_normalized], ignore_index=True
            )
            candidates = candidates.sort_values(
                ["GRID_CD", "mode_priority", "generalized_cost"], kind="stable"
            )
            best = candidates.drop_duplicates("GRID_CD", keep="first").reset_index(
                drop=True
            )
            best["candidate_walk_generalized_cost"] = best["GRID_CD"].map(walk_cost)
            best["candidate_transit_generalized_cost"] = best["GRID_CD"].map(
                transit_cost
            )
            best["candidate_walk_available"] = best[
                "candidate_walk_generalized_cost"
            ].notna()
            best["candidate_transit_available"] = best[
                "candidate_transit_generalized_cost"
            ].notna()
            both_candidate_rows += int(
                (
                    best["candidate_walk_available"]
                    & best["candidate_transit_available"]
                ).sum()
            )
            walk_selected_rows += int(best["selected_mode"].eq("walk").sum())
            transit_selected_rows += int(best["selected_mode"].eq("transit").sum())
            writer = _write_final_frame(best, writer, output_path)
            written_rows += len(best)

            grid_codes = best["GRID_CD"].astype(str).map(grid_to_code)
            if grid_codes.isna().any():
                raise RuntimeError("A selected path refers to an unknown grid")
            grid_codes_array = grid_codes.to_numpy(dtype=np.int32)
            category_stats = stats[str(facility.facility_category)]
            journey_time = best["journey_time_with_wait_min"].to_numpy(dtype=float)
            generalized_cost = best["generalized_cost"].to_numpy(dtype=float)
            walk_available = best["candidate_walk_available"].to_numpy(dtype=bool)
            transit_available = best["candidate_transit_available"].to_numpy(dtype=bool)
            category_stats["walk_candidate"][
                grid_codes_array[walk_available]
            ] += 1
            category_stats["transit_candidate"][
                grid_codes_array[transit_available]
            ] += 1
            category_stats["feasible_any"][grid_codes_array] += 1
            is_walk = best["selected_mode"].eq("walk").to_numpy()
            category_stats["walk_selected"][grid_codes_array[is_walk]] += 1
            category_stats["transit_selected"][grid_codes_array[~is_walk]] += 1
            for beta in BETA_VALUES:
                category_stats[f"accessibility_beta_{int(beta)}"][
                    grid_codes_array
                ] += np.exp(-beta * generalized_cost)

            lower_cost = generalized_cost < category_stats["nearest_cost"][
                grid_codes_array
            ]
            if np.any(lower_cost):
                target = grid_codes_array[lower_cost]
                category_stats["nearest_cost"][target] = generalized_cost[lower_cost]
                category_stats["nearest_cost_time"][target] = journey_time[lower_cost]
                category_stats["nearest_facility"][target] = facility_id
                category_stats["nearest_mode"][target] = best.loc[
                    lower_cost, "selected_mode"
                ].astype(str).to_numpy()
            category_stats["minimum_time"][grid_codes_array] = np.minimum(
                category_stats["minimum_time"][grid_codes_array], journey_time
            )

            if facility_number % 25 == 0 or facility_number == len(facilities):
                print(
                    f"  facilities={facility_number:,}/{len(facilities):,}, "
                    f"best_path_rows={written_rows:,}",
                    flush=True,
                )
    finally:
        if writer is not None:
            writer.close()
    if writer is None:
        raise RuntimeError("No final grid-facility paths were generated")

    grid_frames: list[pd.DataFrame] = []
    base = grids[
        ["GRID_CD", "행정동코드", "시군구", "행정동", "취약노인수"]
    ].copy()
    for category in categories:
        category_stats = stats[category]
        frame = base.copy()
        frame["facility_category"] = category
        frame["walk_facility_count_20min"] = category_stats["walk_candidate"]
        frame["transit_facility_count_90min_walk15min"] = category_stats[
            "transit_candidate"
        ]
        frame["facility_count_any_feasible_mode"] = category_stats[
            "feasible_any"
        ]
        frame["selected_walk_path_count"] = category_stats["walk_selected"]
        frame["selected_transit_path_count"] = category_stats["transit_selected"]
        frame["nearest_generalized_cost"] = np.where(
            np.isfinite(category_stats["nearest_cost"]),
            category_stats["nearest_cost"],
            np.nan,
        )
        frame["nearest_cost_journey_time_min"] = category_stats[
            "nearest_cost_time"
        ]
        frame["nearest_facility_id"] = pd.Series(
            category_stats["nearest_facility"], dtype="string"
        ).replace("", pd.NA)
        frame["nearest_selected_mode"] = pd.Series(
            category_stats["nearest_mode"], dtype="string"
        ).replace("", pd.NA)
        frame["minimum_journey_time_min"] = np.where(
            np.isfinite(category_stats["minimum_time"]),
            category_stats["minimum_time"],
            np.nan,
        )
        for beta in BETA_VALUES:
            frame[f"accessibility_beta_{int(beta)}"] = category_stats[
                f"accessibility_beta_{int(beta)}"
            ]
        grid_frames.append(frame)
    category_accessibility = pd.concat(grid_frames, ignore_index=True)
    count_columns = [
        "walk_facility_count_20min",
        "transit_facility_count_90min_walk15min",
        "facility_count_any_feasible_mode",
        "selected_walk_path_count",
        "selected_transit_path_count",
    ]
    sum_columns = [
        *count_columns,
        *[f"accessibility_beta_{int(beta)}" for beta in BETA_VALUES],
    ]
    totals = category_accessibility.groupby("GRID_CD", sort=False)[sum_columns].sum()
    nearest = (
        category_accessibility.sort_values(
            ["GRID_CD", "nearest_generalized_cost"],
            kind="stable",
            na_position="last",
        )
        .drop_duplicates("GRID_CD", keep="first")
        .set_index("GRID_CD")
    )
    minimum_time = category_accessibility.groupby("GRID_CD", sort=False)[
        "minimum_journey_time_min"
    ].min()

    grid_accessibility = base.copy()
    grid_accessibility["facility_category"] = "전체"
    for column in sum_columns:
        values = grid_accessibility["GRID_CD"].map(totals[column]).fillna(0)
        grid_accessibility[column] = (
            values.astype(np.int64) if column in count_columns else values
        )
    grid_accessibility["nearest_generalized_cost"] = grid_accessibility[
        "GRID_CD"
    ].map(nearest["nearest_generalized_cost"])
    grid_accessibility["nearest_cost_journey_time_min"] = grid_accessibility[
        "GRID_CD"
    ].map(nearest["nearest_cost_journey_time_min"])
    grid_accessibility["nearest_facility_id"] = grid_accessibility["GRID_CD"].map(
        nearest["nearest_facility_id"]
    )
    grid_accessibility["nearest_selected_mode"] = grid_accessibility["GRID_CD"].map(
        nearest["nearest_selected_mode"]
    )
    grid_accessibility["minimum_journey_time_min"] = grid_accessibility[
        "GRID_CD"
    ].map(minimum_time)

    overall_beta_3_sum = grid_accessibility["accessibility_beta_3"]
    overall_facility_count = int(len(facilities))
    overall_normalized = overall_beta_3_sum / overall_facility_count
    grid_accessibility["overall_facility_count"] = overall_facility_count
    grid_accessibility["overall_accessibility_beta_3_sum"] = overall_beta_3_sum
    grid_accessibility["overall_accessibility_beta_3_normalized_0_1"] = (
        overall_normalized
    )
    grid_accessibility["overall_accessibility_beta_3_percent_0_100"] = (
        100.0 * overall_normalized
    )
    grid_accessibility["overall_accessibility_deficit_0_1"] = (
        1.0 - overall_normalized
    )
    grid_accessibility.to_csv(grid_output_path, index=False, encoding="utf-8-sig")

    distribution_rows: list[dict[str, object]] = []
    correlation_rows: list[dict[str, object]] = []
    for category, group in grid_accessibility.groupby("facility_category", sort=False):
        for beta in BETA_VALUES:
            column = f"accessibility_beta_{int(beta)}"
            values = group[column].astype(float)
            distribution_rows.append(
                {
                    "facility_category": str(category),
                    "beta": beta,
                    "minimum": float(values.min()),
                    "p10": float(values.quantile(0.10)),
                    "median": float(values.median()),
                    "mean": float(values.mean()),
                    "p90": float(values.quantile(0.90)),
                    "maximum": float(values.max()),
                    "zero_grid_count": int((values == 0).sum()),
                }
            )
        for beta_a in BETA_VALUES:
            for beta_b in BETA_VALUES:
                if beta_b <= beta_a:
                    continue
                values_a = group[f"accessibility_beta_{int(beta_a)}"].astype(float)
                values_b = group[f"accessibility_beta_{int(beta_b)}"].astype(float)
                correlation_rows.append(
                    {
                        "facility_category": str(category),
                        "beta_a": beta_a,
                        "beta_b": beta_b,
                        "pearson_correlation": float(values_a.corr(values_b, method="pearson")),
                        "spearman_rank_correlation": float(
                            values_a.corr(values_b, method="spearman")
                        ),
                    }
                )
    pd.DataFrame(distribution_rows).to_csv(
        args.output_dir / "beta_sensitivity_distribution.csv",
        index=False,
        encoding="utf-8-sig",
    )
    pd.DataFrame(correlation_rows).to_csv(
        args.output_dir / "beta_sensitivity_correlations.csv",
        index=False,
        encoding="utf-8-sig",
    )

    output_rows = pq.ParquetFile(output_path).metadata.num_rows
    if output_rows != written_rows:
        raise RuntimeError("Final path Parquet row count does not match generated rows")
    if int(grid_accessibility["facility_count_any_feasible_mode"].sum()) != written_rows:
        raise RuntimeError("Feasible grid-facility counts do not reconcile to path rows")
    summary = {
        "available_facility_categories": categories,
        "exhibition_status": "skipped_until_dataset_is_supplied",
        "path_selection": "walk_if_available_else_transit",
        "walk_priority_rule": (
            "Any facility reachable within 20 minutes on foot uses the walking "
            "path regardless of the transit generalized cost."
        ),
        "population_filter": "취약노인수 > 0",
        "maximum_walk_candidate_time_min": 20.0,
        "maximum_transit_total_walk_time_min": 15.0,
        "maximum_transit_journey_time_min": 90.0,
        "beta_values": list(BETA_VALUES),
        "accessibility_formula": "sum(exp(-beta * generalized_cost))",
        "overall_accessibility_formula": (
            "100 / all_facilities * sum(exp(-3 * generalized_cost)); "
            "unreachable facilities contribute 0"
        ),
        "overall_accessibility_scope": "all facility categories combined",
        "overall_facility_count": overall_facility_count,
        "facility_rows": int(len(facilities)),
        "facilities_without_any_path": int(facilities_without_any_path),
        "final_grid_facility_path_rows": int(written_rows),
        "pairs_with_both_walk_and_transit_candidates": int(both_candidate_rows),
        "walk_selected_rows": int(walk_selected_rows),
        "transit_selected_rows": int(transit_selected_rows),
        "grid_output_rows": int(len(grid_accessibility)),
        "category_calculation_rows_before_combining": int(
            len(category_accessibility)
        ),
        "grid_count": int(len(grids)),
    }
    (args.output_dir / "final_accessibility_build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

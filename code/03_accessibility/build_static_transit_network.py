"""Build a static transit routing network from the all-day median GTFS inputs.

The network keeps route-pattern stop occurrences as distinct states so that
repeated visits to the same physical stop do not lose their sequence position.
It contains scheduled ride edges, initial boarding edges, and transfer edges
whose waits use the fixed full-service-day median headways.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


DEFAULT_INPUT_DIR = Path("outputs/fixed_accessibility_inputs/transit")
DEFAULT_SNAP_DIR = Path("outputs/fixed_accessibility_inputs/network_snap")
DEFAULT_OUTPUT_DIR = Path("outputs/fixed_accessibility_inputs/transit_network")


def _gtfs_time_to_seconds(series: pd.Series) -> pd.Series:
    parts = series.astype("string").str.split(":", expand=True)
    if parts.shape[1] != 3:
        raise ValueError("GTFS time must contain hour, minute, and second")
    numeric = parts.apply(pd.to_numeric, errors="coerce")
    return numeric[0] * 3600.0 + numeric[1] * 60.0 + numeric[2]


def _haversine_m(
    lon1: pd.Series,
    lat1: pd.Series,
    lon2: pd.Series,
    lat2: pd.Series,
) -> np.ndarray:
    radius_m = 6_371_008.8
    lon1_rad = np.radians(lon1.to_numpy(dtype=float))
    lat1_rad = np.radians(lat1.to_numpy(dtype=float))
    lon2_rad = np.radians(lon2.to_numpy(dtype=float))
    lat2_rad = np.radians(lat2.to_numpy(dtype=float))
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    value = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon / 2.0) ** 2
    )
    return 2.0 * radius_m * np.arcsin(np.sqrt(np.clip(value, 0.0, 1.0)))


def _quantile(series: pd.Series, probability: float) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.quantile(probability))


def _build_states(input_dir: Path, snap_dir: Path) -> pd.DataFrame:
    pattern_stops = pd.read_parquet(input_dir / "route_pattern_stops.parquet")
    headways = pd.read_csv(
        input_dir / "route_pattern_headways_all_day.csv",
        dtype={"pattern_id": "string", "route_id": "string"},
    )
    stop_snaps = pd.read_parquet(snap_dir / "transit_stop_walk_node_snap.parquet")

    pattern_stops = pattern_stops.sort_values(
        ["pattern_id", "stop_sequence", "stop_id"], kind="stable"
    ).reset_index(drop=True)
    pattern_stops["pattern_state_index"] = (
        pattern_stops.groupby("pattern_id", sort=False).cumcount().astype("int32")
    )
    pattern_stops["pattern_stop_occurrence"] = (
        pattern_stops.groupby(["pattern_id", "stop_id"], sort=False)
        .cumcount()
        .add(1)
        .astype("int16")
    )
    pattern_stops["state_id"] = (
        pattern_stops["pattern_id"].astype(str)
        + "__I"
        + pattern_stops["pattern_state_index"].astype(str).str.zfill(4)
    )
    pattern_stops["arrival_sec"] = _gtfs_time_to_seconds(pattern_stops["arrival_time"])
    pattern_stops["departure_sec"] = _gtfs_time_to_seconds(pattern_stops["departure_time"])

    headway_columns = [
        "pattern_id",
        "median_headway_min",
        "expected_initial_wait_min",
        "headway_source",
        "daily_departures",
        "route_short_name",
        "route_long_name",
        "route_type",
    ]
    states = pattern_stops.merge(
        headways[headway_columns],
        on="pattern_id",
        how="left",
        validate="many_to_one",
    )
    snap_columns = [
        "stop_id",
        "stop_name",
        "stop_lat",
        "stop_lon",
        "x_epsg5179",
        "y_epsg5179",
        "walk_node_id",
        "walk_snap_distance_m",
        "walk_snap_time_elderly_min",
        "within_1km_of_walk_network",
    ]
    states = states.merge(
        stop_snaps[snap_columns],
        on="stop_id",
        how="left",
        validate="many_to_one",
    )
    if states["state_id"].duplicated().any():
        raise RuntimeError("Transit state IDs are not unique")
    if states["median_headway_min"].isna().any():
        raise RuntimeError("Some route-pattern states have no median headway")
    if states["walk_node_id"].isna().any():
        raise RuntimeError("Some transit stops have no walk-network snap record")
    return states


def _build_ride_edges(states: pd.DataFrame) -> pd.DataFrame:
    work = states.sort_values(["pattern_id", "pattern_state_index"], kind="stable").copy()
    next_columns = {
        "state_id": "to_state_id",
        "stop_id": "to_stop_id",
        "stop_sequence": "to_stop_sequence",
        "pattern_state_index": "to_pattern_state_index",
        "arrival_sec": "to_arrival_sec",
        "stop_lat": "to_stop_lat",
        "stop_lon": "to_stop_lon",
    }
    for source, target in next_columns.items():
        work[target] = work.groupby("pattern_id", sort=False)[source].shift(-1)
    work = work.loc[work["to_state_id"].notna()].copy()

    work["raw_in_vehicle_time_sec"] = work["to_arrival_sec"] - work["departure_sec"]
    work["cross_midnight_adjusted"] = work["raw_in_vehicle_time_sec"] < 0
    work["in_vehicle_time_sec"] = np.where(
        work["cross_midnight_adjusted"],
        work["raw_in_vehicle_time_sec"] + 86_400.0,
        work["raw_in_vehicle_time_sec"],
    )
    work["in_vehicle_time_min"] = work["in_vehicle_time_sec"] / 60.0
    work["segment_distance_m"] = _haversine_m(
        work["stop_lon"], work["stop_lat"], work["to_stop_lon"], work["to_stop_lat"]
    )
    work["scheduled_speed_kmh"] = np.where(
        work["in_vehicle_time_sec"] > 0,
        work["segment_distance_m"] / work["in_vehicle_time_sec"] * 3.6,
        np.nan,
    )
    work["edge_type"] = "ride"
    work["expected_wait_min"] = 0.0
    work["transfer_increment"] = np.int8(0)
    work["boarding_increment"] = np.int8(0)

    columns = [
        "edge_type",
        "state_id",
        "to_state_id",
        "pattern_id",
        "route_id",
        "stop_id",
        "to_stop_id",
        "stop_sequence",
        "to_stop_sequence",
        "pattern_state_index",
        "to_pattern_state_index",
        "departure_time",
        "arrival_time",
        "departure_sec",
        "to_arrival_sec",
        "raw_in_vehicle_time_sec",
        "cross_midnight_adjusted",
        "in_vehicle_time_min",
        "segment_distance_m",
        "scheduled_speed_kmh",
        "expected_wait_min",
        "transfer_increment",
        "boarding_increment",
    ]
    ride_edges = work[columns].rename(
        columns={
            "state_id": "from_state_id",
            "stop_id": "from_stop_id",
            "stop_sequence": "from_stop_sequence",
            "pattern_state_index": "from_pattern_state_index",
            "departure_time": "from_departure_time",
            "arrival_time": "from_arrival_time",
            "departure_sec": "from_departure_sec",
        }
    )
    if (ride_edges["in_vehicle_time_min"] < 0).any():
        raise RuntimeError("Negative ride time remained after midnight adjustment")
    return ride_edges


def _build_boarding_edges(states: pd.DataFrame) -> pd.DataFrame:
    boarding = states[
        [
            "state_id",
            "pattern_id",
            "route_id",
            "stop_id",
            "stop_sequence",
            "pattern_state_index",
            "pattern_stop_occurrence",
            "median_headway_min",
            "expected_initial_wait_min",
            "headway_source",
        ]
    ].copy()
    boarding.insert(0, "edge_type", "board")
    boarding = boarding.rename(
        columns={
            "state_id": "to_state_id",
            "expected_initial_wait_min": "expected_wait_min",
        }
    )
    boarding["from_stop_id"] = boarding["stop_id"]
    boarding["edge_time_min"] = boarding["expected_wait_min"]
    boarding["transfer_increment"] = np.int8(0)
    boarding["boarding_increment"] = np.int8(1)
    return boarding[
        [
            "edge_type",
            "from_stop_id",
            "to_state_id",
            "pattern_id",
            "route_id",
            "stop_id",
            "stop_sequence",
            "pattern_state_index",
            "pattern_stop_occurrence",
            "median_headway_min",
            "expected_wait_min",
            "edge_time_min",
            "headway_source",
            "transfer_increment",
            "boarding_increment",
        ]
    ]


TRANSFER_SCHEMA = pa.schema(
    [
        ("edge_type", pa.string()),
        ("from_state_id", pa.string()),
        ("to_state_id", pa.string()),
        ("from_pattern_id", pa.string()),
        ("to_pattern_id", pa.string()),
        ("from_route_id", pa.string()),
        ("to_route_id", pa.string()),
        ("from_stop_id", pa.string()),
        ("to_stop_id", pa.string()),
        ("from_stop_sequence", pa.int32()),
        ("to_stop_sequence", pa.int32()),
        ("from_pattern_state_index", pa.int32()),
        ("to_pattern_state_index", pa.int32()),
        ("from_stop_occurrence", pa.int16()),
        ("to_stop_occurrence", pa.int16()),
        ("explicit_transfer", pa.bool_()),
        ("min_transfer_time_min", pa.float64()),
        ("next_route_median_headway_min", pa.float64()),
        ("expected_wait_min", pa.float64()),
        ("edge_time_min", pa.float64()),
        ("transfer_increment", pa.int8()),
        ("boarding_increment", pa.int8()),
    ]
)


def _write_state_transfer_edges(
    pattern_transfer_path: Path,
    states: pd.DataFrame,
    output_path: Path,
) -> tuple[int, int]:
    state_map = states[
        [
            "pattern_id",
            "stop_id",
            "state_id",
            "stop_sequence",
            "pattern_state_index",
            "pattern_stop_occurrence",
        ]
    ]
    from_map = state_map.rename(
        columns={
            "pattern_id": "from_pattern_id",
            "stop_id": "from_stop_id",
            "state_id": "from_state_id",
            "stop_sequence": "from_stop_sequence",
            "pattern_state_index": "from_pattern_state_index",
            "pattern_stop_occurrence": "from_stop_occurrence",
        }
    )
    to_map = state_map.rename(
        columns={
            "pattern_id": "to_pattern_id",
            "stop_id": "to_stop_id",
            "state_id": "to_state_id",
            "stop_sequence": "to_stop_sequence",
            "pattern_state_index": "to_pattern_state_index",
            "pattern_stop_occurrence": "to_stop_occurrence",
        }
    )
    output_columns = TRANSFER_SCHEMA.names
    writer = pq.ParquetWriter(output_path, TRANSFER_SCHEMA, compression="zstd")
    input_count = 0
    output_count = 0
    parquet_file = pq.ParquetFile(pattern_transfer_path)
    try:
        for batch_number, batch in enumerate(parquet_file.iter_batches(batch_size=100_000), 1):
            transfer = batch.to_pandas()
            input_count += len(transfer)
            expanded = transfer.merge(
                from_map,
                on=["from_pattern_id", "from_stop_id"],
                how="inner",
                validate="many_to_many",
            ).merge(
                to_map,
                on=["to_pattern_id", "to_stop_id"],
                how="inner",
                validate="many_to_many",
            )
            expanded["edge_type"] = "transfer"
            expanded["edge_time_min"] = (
                expanded["min_transfer_time_min"] + expanded["expected_wait_min"]
            )
            expanded["boarding_increment"] = np.int8(1)
            table = pa.Table.from_pandas(
                expanded[output_columns], schema=TRANSFER_SCHEMA, preserve_index=False
            )
            writer.write_table(table)
            output_count += len(expanded)
            print(
                f"  transfer batches={batch_number:,}, pattern edges={input_count:,}, "
                f"state edges={output_count:,}",
                flush=True,
            )
    finally:
        writer.close()
    return input_count, output_count


def build_static_network(input_dir: Path, snap_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Building route-pattern stop states", flush=True)
    states = _build_states(input_dir, snap_dir)
    states_path = output_dir / "transit_pattern_stop_states.parquet"
    states.to_parquet(states_path, index=False, compression="zstd")

    print("Building scheduled ride edges", flush=True)
    ride_edges = _build_ride_edges(states)
    ride_path = output_dir / "transit_pattern_ride_edges.parquet"
    ride_edges.to_parquet(ride_path, index=False, compression="zstd")

    print("Building initial boarding edges", flush=True)
    boarding_edges = _build_boarding_edges(states)
    boarding_path = output_dir / "transit_pattern_boarding_edges.parquet"
    boarding_edges.to_parquet(boarding_path, index=False, compression="zstd")

    print("Expanding pattern transfers to ordered stop states", flush=True)
    transfer_path = output_dir / "transit_pattern_state_transfer_edges.parquet"
    pattern_transfer_count, state_transfer_count = _write_state_transfer_edges(
        input_dir / "route_pattern_transfer_wait_edges.parquet",
        states,
        transfer_path,
    )

    expected_ride_edges = int(
        len(states) - states["pattern_id"].nunique()
    )
    summary = {
        "headway_basis": "full_service_day_median_of_consecutive_first_stop_departures",
        "time_band_condition": "none",
        "route_pattern_stop_states": int(len(states)),
        "route_patterns": int(states["pattern_id"].nunique()),
        "physical_stops": int(states["stop_id"].nunique()),
        "states_with_walk_node_within_1km": int(
            states["within_1km_of_walk_network"].fillna(False).sum()
        ),
        "physical_stops_with_walk_node_within_1km": int(
            states.loc[
                states["within_1km_of_walk_network"].fillna(False), "stop_id"
            ].nunique()
        ),
        "ride_edges": int(len(ride_edges)),
        "expected_ride_edges": expected_ride_edges,
        "ride_edge_count_matches_expected": bool(len(ride_edges) == expected_ride_edges),
        "negative_raw_ride_time_edges": int(
            (ride_edges["raw_in_vehicle_time_sec"] < 0).sum()
        ),
        "cross_midnight_adjusted_ride_edges": int(
            ride_edges["cross_midnight_adjusted"].sum()
        ),
        "zero_ride_time_edges": int((ride_edges["in_vehicle_time_min"] == 0).sum()),
        "median_ride_time_min": _quantile(ride_edges["in_vehicle_time_min"], 0.50),
        "p95_ride_time_min": _quantile(ride_edges["in_vehicle_time_min"], 0.95),
        "median_segment_distance_m": _quantile(ride_edges["segment_distance_m"], 0.50),
        "p95_scheduled_speed_kmh": _quantile(ride_edges["scheduled_speed_kmh"], 0.95),
        "ride_edges_over_130_kmh": int(
            (ride_edges["scheduled_speed_kmh"] > 130).sum()
        ),
        "boarding_edges": int(len(boarding_edges)),
        "boarding_edge_count_matches_states": bool(len(boarding_edges) == len(states)),
        "median_initial_wait_min": _quantile(boarding_edges["expected_wait_min"], 0.50),
        "pattern_transfer_edges": int(pattern_transfer_count),
        "state_transfer_edges": int(state_transfer_count),
        "state_transfer_expansion_factor": float(state_transfer_count / pattern_transfer_count),
        "transfer_edge_expected_wait_definition": "next_pattern_median_headway_divided_by_2",
        "transfer_edge_time_definition": "gtfs_min_transfer_time_plus_expected_wait",
        "state_definition": "route_pattern_plus_ordered_canonical_stop_occurrence",
    }
    summary_path = output_dir / "static_transit_network_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pd.DataFrame([summary]).to_csv(
        output_dir / "static_transit_network_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--snap-dir", type=Path, default=DEFAULT_SNAP_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    build_static_network(arguments.input_dir, arguments.snap_dir, arguments.output_dir)

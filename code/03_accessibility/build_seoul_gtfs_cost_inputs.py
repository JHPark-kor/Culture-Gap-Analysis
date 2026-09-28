"""Filter the national GTFS to Seoul and build all-day transit burden inputs.

The representative headway is the median interval between consecutive first-stop
departures for each directional route pattern over the full service day.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import shapely


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GTFS_DIR = PROJECT_ROOT / "data/raw/transport/seoul_gtfs"
DEFAULT_BOUNDARY = (
    PROJECT_ROOT / "data/raw/spatial/accessibility/boundary/seoul_gu.json"
)
DEFAULT_OUTPUT_DIR = Path("outputs/fixed_accessibility_inputs/transit")
ALLOWED_ROUTE_TYPES = (0, 1)  # Local/city buses and metropolitan urban rail in this feed.


def _reader(path: Path, columns: list[str]) -> pacsv.CSVStreamingReader:
    type_map = {
        "trip_id": pa.string(),
        "arrival_time": pa.string(),
        "departure_time": pa.string(),
        "stop_id": pa.string(),
        "stop_sequence": pa.int32(),
        "pickup_type": pa.int8(),
        "drop_off_type": pa.int8(),
        "timepoint": pa.int8(),
    }
    return pacsv.open_csv(
        path,
        read_options=pacsv.ReadOptions(block_size=64 * 1024 * 1024, use_threads=True),
        convert_options=pacsv.ConvertOptions(
            include_columns=columns,
            column_types={column: type_map[column] for column in columns},
            strings_can_be_null=False,
        ),
    )


def _time_to_seconds(value: object) -> float:
    if value is None or pd.isna(value):
        return math.nan
    pieces = str(value).split(":")
    if len(pieces) != 3:
        return math.nan
    try:
        hour, minute, second = (int(piece) for piece in pieces)
    except ValueError:
        return math.nan
    return float(hour * 3600 + minute * 60 + second)


def _seconds_to_clock(seconds: float) -> str:
    if not math.isfinite(seconds):
        return ""
    value = int(round(seconds))
    return f"{value // 3600:02d}:{(value % 3600) // 60:02d}:{value % 60:02d}"


def _pattern_id(route_id: str, first_stop_id: str, last_stop_id: str) -> str:
    key = f"{route_id}|{first_stop_id}|{last_stop_id}"
    return "P_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _headway_summary(frame: pd.DataFrame, key: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for group_id, group in frame.groupby(key, sort=False):
        departures = np.sort(group["first_departure_sec"].dropna().unique())
        gaps = np.diff(departures)
        gaps = gaps[gaps > 0]
        rows.append(
            {
                key: group_id,
                "daily_departures": int(len(departures)),
                "observed_intervals": int(len(gaps)),
                "median_headway_min": float(np.median(gaps) / 60.0) if len(gaps) else math.nan,
                "p10_headway_min": float(np.quantile(gaps, 0.10) / 60.0) if len(gaps) else math.nan,
                "p90_headway_min": float(np.quantile(gaps, 0.90) / 60.0) if len(gaps) else math.nan,
                "first_departure": _seconds_to_clock(float(departures.min())) if len(departures) else "",
                "last_departure": _seconds_to_clock(float(departures.max())) if len(departures) else "",
            }
        )
    return pd.DataFrame(rows)


def _update_trip_endpoints(
    chunk: pd.DataFrame,
    first_by_trip: dict[str, tuple[int, str, str]],
    last_by_trip: dict[str, tuple[int, str, str]],
    stop_count_by_trip: dict[str, int],
) -> None:
    chunk = chunk.dropna(subset=["trip_id", "stop_id", "stop_sequence"])
    if chunk.empty:
        return
    first_rows = chunk.loc[chunk.groupby("trip_id")["stop_sequence"].idxmin()]
    last_rows = chunk.loc[chunk.groupby("trip_id")["stop_sequence"].idxmax()]
    for row in first_rows.itertuples(index=False):
        current = first_by_trip.get(row.trip_id)
        candidate = (int(row.stop_sequence), str(row.stop_id), str(row.departure_time))
        if current is None or candidate[0] < current[0]:
            first_by_trip[str(row.trip_id)] = candidate
    for row in last_rows.itertuples(index=False):
        current = last_by_trip.get(row.trip_id)
        candidate = (int(row.stop_sequence), str(row.stop_id), str(row.arrival_time))
        if current is None or candidate[0] > current[0]:
            last_by_trip[str(row.trip_id)] = candidate
    for trip_id, count in chunk.groupby("trip_id").size().items():
        key = str(trip_id)
        stop_count_by_trip[key] = stop_count_by_trip.get(key, 0) + int(count)


def _write_transfer_edges(
    output_path: Path,
    patterns_by_stop: dict[str, list[str]],
    explicit_transfers: pd.DataFrame,
    headway_by_pattern: dict[str, float],
    route_by_pattern: dict[str, str],
) -> int:
    schema = pa.schema(
        [
            ("from_pattern_id", pa.string()),
            ("to_pattern_id", pa.string()),
            ("from_route_id", pa.string()),
            ("to_route_id", pa.string()),
            ("from_stop_id", pa.string()),
            ("to_stop_id", pa.string()),
            ("explicit_transfer", pa.bool_()),
            ("min_transfer_time_min", pa.float64()),
            ("next_route_median_headway_min", pa.float64()),
            ("expected_wait_min", pa.float64()),
            ("transfer_increment", pa.int8()),
        ]
    )
    writer = pq.ParquetWriter(output_path, schema, compression="zstd")
    buffer: list[dict[str, object]] = []
    written = 0

    def flush() -> None:
        nonlocal buffer, written
        if not buffer:
            return
        table = pa.Table.from_pylist(buffer, schema=schema)
        writer.write_table(table)
        written += len(buffer)
        buffer = []

    def append_edges(
        from_stop_id: str,
        to_stop_id: str,
        explicit: bool,
        min_transfer_time_min: float,
    ) -> None:
        from_patterns = patterns_by_stop.get(from_stop_id, [])
        to_patterns = patterns_by_stop.get(to_stop_id, [])
        for from_pattern in from_patterns:
            for to_pattern in to_patterns:
                if from_pattern == to_pattern:
                    continue
                headway = float(headway_by_pattern[to_pattern])
                buffer.append(
                    {
                        "from_pattern_id": from_pattern,
                        "to_pattern_id": to_pattern,
                        "from_route_id": route_by_pattern[from_pattern],
                        "to_route_id": route_by_pattern[to_pattern],
                        "from_stop_id": from_stop_id,
                        "to_stop_id": to_stop_id,
                        "explicit_transfer": explicit,
                        "min_transfer_time_min": float(min_transfer_time_min),
                        "next_route_median_headway_min": headway,
                        "expected_wait_min": headway / 2.0,
                        "transfer_increment": 1,
                    }
                )
                if len(buffer) >= 100_000:
                    flush()

    for stop_id, patterns in patterns_by_stop.items():
        if len(patterns) > 1:
            append_edges(stop_id, stop_id, False, 0.0)

    for row in explicit_transfers.itertuples(index=False):
        if int(row.transfer_type) == 3:
            continue
        from_stop_id = str(row.from_stop_id)
        to_stop_id = str(row.to_stop_id)
        if from_stop_id == to_stop_id:
            continue
        minimum = 0.0 if pd.isna(row.min_transfer_time) else float(row.min_transfer_time) / 60.0
        append_edges(from_stop_id, to_stop_id, True, minimum)

    flush()
    writer.close()
    return written


def build_transit_inputs(gtfs_dir: Path, boundary_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    subset_dir = output_dir / "seoul_gtfs_analysis_subset"
    subset_dir.mkdir(parents=True, exist_ok=True)

    routes = pd.read_csv(gtfs_dir / "routes.txt", dtype={"route_id": "string"})
    trips = pd.read_csv(
        gtfs_dir / "trips.txt",
        dtype={"route_id": "string", "service_id": "string", "trip_id": "string"},
    )
    stops = pd.read_csv(
        gtfs_dir / "stops.txt",
        dtype={"stop_id": "string", "stop_name": "string"},
    )
    boundary = gpd.read_file(boundary_path).to_crs(4326).geometry.union_all()
    in_seoul = shapely.contains_xy(
        boundary,
        stops["stop_lon"].to_numpy(dtype=float),
        stops["stop_lat"].to_numpy(dtype=float),
    )
    seoul_stop_ids = set(stops.loc[in_seoul, "stop_id"].astype(str))

    allowed_routes = routes.loc[routes["route_type"].isin(ALLOWED_ROUTE_TYPES)].copy()
    allowed_route_ids = set(allowed_routes["route_id"].astype(str))
    candidate_trips = trips.loc[trips["route_id"].astype(str).isin(allowed_route_ids)].copy()
    candidate_trip_ids = set(candidate_trips["trip_id"].astype(str))
    candidate_trip_values = pa.array(sorted(candidate_trip_ids), type=pa.string())
    seoul_stop_values = pa.array(sorted(seoul_stop_ids), type=pa.string())

    stop_times_path = gtfs_dir / "stop_times.txt"
    print(
        f"Pass 1: finding bus/urban-rail trips serving {len(seoul_stop_ids):,} Seoul stops",
        flush=True,
    )
    selected_trip_ids: set[str] = set()
    for batch_number, batch in enumerate(_reader(stop_times_path, ["trip_id", "stop_id"]), 1):
        trip_mask = pc.is_in(batch.column("trip_id"), value_set=candidate_trip_values)
        stop_mask = pc.is_in(batch.column("stop_id"), value_set=seoul_stop_values)
        matched = pc.filter(batch.column("trip_id"), pc.and_(trip_mask, stop_mask))
        if len(matched):
            selected_trip_ids.update(str(value) for value in pc.unique(matched).to_pylist())
        if batch_number % 5 == 0:
            print(
                f"  pass1 batches={batch_number:,}, selected trips={len(selected_trip_ids):,}",
                flush=True,
            )

    selected_trips = candidate_trips.loc[
        candidate_trips["trip_id"].astype(str).isin(selected_trip_ids)
    ].copy()
    selected_route_ids = set(selected_trips["route_id"].astype(str))
    selected_routes = routes.loc[routes["route_id"].astype(str).isin(selected_route_ids)].copy()
    selected_trip_values = pa.array(sorted(selected_trip_ids), type=pa.string())

    print(
        f"Pass 2: extracting {len(selected_trip_ids):,} trips on {len(selected_route_ids):,} routes",
        flush=True,
    )
    all_columns = [
        "trip_id",
        "arrival_time",
        "departure_time",
        "stop_id",
        "stop_sequence",
        "pickup_type",
        "drop_off_type",
        "timepoint",
    ]
    filtered_parquet = output_dir / "seoul_stop_times.parquet"
    parquet_writer: pq.ParquetWriter | None = None
    first_by_trip: dict[str, tuple[int, str, str]] = {}
    last_by_trip: dict[str, tuple[int, str, str]] = {}
    stop_count_by_trip: dict[str, int] = {}
    selected_stop_ids: set[str] = set()
    filtered_stop_time_rows = 0

    for batch_number, batch in enumerate(_reader(stop_times_path, all_columns), 1):
        mask = pc.is_in(batch.column("trip_id"), value_set=selected_trip_values)
        filtered = pa.Table.from_batches([batch]).filter(mask)
        if not len(filtered):
            continue
        if parquet_writer is None:
            parquet_writer = pq.ParquetWriter(filtered_parquet, filtered.schema, compression="zstd")
        parquet_writer.write_table(filtered)
        filtered_stop_time_rows += len(filtered)
        selected_stop_ids.update(str(value) for value in pc.unique(filtered["stop_id"]).to_pylist())
        _update_trip_endpoints(
            filtered.select(
                ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"]
            ).to_pandas(),
            first_by_trip,
            last_by_trip,
            stop_count_by_trip,
        )
        if batch_number % 5 == 0:
            print(
                f"  pass2 batches={batch_number:,}, stop-time rows={filtered_stop_time_rows:,}",
                flush=True,
            )
    if parquet_writer is None:
        raise RuntimeError("No Seoul GTFS stop_times rows were selected")
    parquet_writer.close()

    trip_endpoints = []
    for trip_id in selected_trip_ids:
        first = first_by_trip.get(trip_id)
        last = last_by_trip.get(trip_id)
        if first is None or last is None:
            continue
        trip_endpoints.append(
            {
                "trip_id": trip_id,
                "first_stop_id": first[1],
                "first_departure_time": first[2],
                "first_departure_sec": _time_to_seconds(first[2]),
                "last_stop_id": last[1],
                "last_arrival_time": last[2],
                "stop_count": stop_count_by_trip.get(trip_id, 0),
            }
        )
    endpoints = pd.DataFrame(trip_endpoints)
    trip_meta = selected_trips.merge(endpoints, on="trip_id", how="inner", validate="one_to_one")
    trip_meta["pattern_id"] = [
        _pattern_id(str(route), str(first), str(last))
        for route, first, last in zip(
            trip_meta["route_id"], trip_meta["first_stop_id"], trip_meta["last_stop_id"]
        )
    ]

    pattern_headways = _headway_summary(trip_meta, "pattern_id")
    route_headways = _headway_summary(trip_meta, "route_id")
    route_headway_map = route_headways.set_index("route_id")["median_headway_min"]
    valid_pattern_headways = pattern_headways["median_headway_min"].dropna()
    global_median = float(valid_pattern_headways.median()) if len(valid_pattern_headways) else 15.0

    pattern_meta = (
        trip_meta.sort_values(["pattern_id", "stop_count"], ascending=[True, False])
        .drop_duplicates("pattern_id")
        [["pattern_id", "route_id", "first_stop_id", "last_stop_id", "trip_id", "stop_count"]]
        .rename(columns={"trip_id": "canonical_trip_id"})
    )
    pattern_headways = pattern_meta.merge(
        pattern_headways, on="pattern_id", how="left", validate="one_to_one"
    )
    pattern_headways["route_headway_fallback_min"] = pattern_headways["route_id"].map(
        route_headway_map
    )
    pattern_headways["headway_source"] = np.where(
        pattern_headways["median_headway_min"].notna(), "pattern_median", "route_median"
    )
    pattern_headways["median_headway_min"] = pattern_headways[
        "median_headway_min"
    ].fillna(pattern_headways["route_headway_fallback_min"])
    pattern_headways["headway_source"] = np.where(
        pattern_headways["median_headway_min"].notna(),
        pattern_headways["headway_source"],
        "global_median",
    )
    pattern_headways["median_headway_min"] = pattern_headways[
        "median_headway_min"
    ].fillna(global_median)
    pattern_headways["expected_initial_wait_min"] = (
        pattern_headways["median_headway_min"] / 2.0
    )
    route_columns = [
        column
        for column in ["route_id", "route_short_name", "route_long_name", "route_type"]
        if column in selected_routes.columns
    ]
    pattern_headways = pattern_headways.merge(
        selected_routes[route_columns], on="route_id", how="left", validate="many_to_one"
    )
    pattern_headways.to_csv(
        output_dir / "route_pattern_headways_all_day.csv", index=False, encoding="utf-8-sig"
    )
    route_headways = route_headways.merge(
        selected_routes[route_columns], on="route_id", how="left", validate="one_to_one"
    )
    route_headways.to_csv(
        output_dir / "route_headways_all_day.csv", index=False, encoding="utf-8-sig"
    )
    trip_meta.to_parquet(output_dir / "trip_route_patterns.parquet", index=False)

    canonical_trip_ids = set(pattern_headways["canonical_trip_id"].astype(str))
    canonical_values = pa.array(sorted(canonical_trip_ids), type=pa.string())
    trip_to_pattern = dict(
        zip(pattern_headways["canonical_trip_id"].astype(str), pattern_headways["pattern_id"])
    )
    pattern_stop_frames: list[pd.DataFrame] = []
    parquet_file = pq.ParquetFile(filtered_parquet)
    for batch in parquet_file.iter_batches(
        batch_size=500_000,
        columns=["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
    ):
        mask = pc.is_in(batch.column("trip_id"), value_set=canonical_values)
        filtered = pa.Table.from_batches([batch]).filter(mask)
        if len(filtered):
            pattern_stop_frames.append(filtered.to_pandas())
    pattern_stops = pd.concat(pattern_stop_frames, ignore_index=True)
    pattern_stops["pattern_id"] = pattern_stops["trip_id"].map(trip_to_pattern)
    pattern_stops = pattern_stops.merge(
        pattern_headways[["pattern_id", "route_id"]],
        on="pattern_id",
        how="left",
        validate="many_to_one",
    ).sort_values(["pattern_id", "stop_sequence"])
    pattern_stops.to_parquet(output_dir / "route_pattern_stops.parquet", index=False)

    patterns_by_stop = {
        str(stop_id): sorted(set(group["pattern_id"].astype(str)))
        for stop_id, group in pattern_stops.groupby("stop_id", sort=False)
    }
    transfer_source = pd.read_csv(
        gtfs_dir / "transfers.txt",
        dtype={"from_stop_id": "string", "to_stop_id": "string"},
    )
    transfer_source = transfer_source.loc[
        transfer_source["from_stop_id"].astype(str).isin(selected_stop_ids)
        & transfer_source["to_stop_id"].astype(str).isin(selected_stop_ids)
    ].copy()
    headway_by_pattern = dict(
        zip(pattern_headways["pattern_id"], pattern_headways["median_headway_min"])
    )
    route_by_pattern = dict(zip(pattern_headways["pattern_id"], pattern_headways["route_id"]))
    transfer_edge_count = _write_transfer_edges(
        output_dir / "route_pattern_transfer_wait_edges.parquet",
        patterns_by_stop,
        transfer_source,
        headway_by_pattern,
        route_by_pattern,
    )

    selected_stops = stops.loc[stops["stop_id"].astype(str).isin(selected_stop_ids)].copy()
    selected_transfers = transfer_source.copy()
    selected_routes.to_csv(subset_dir / "routes.txt", index=False, encoding="utf-8")
    selected_trips.to_csv(subset_dir / "trips.txt", index=False, encoding="utf-8")
    selected_stops.to_csv(subset_dir / "stops.txt", index=False, encoding="utf-8")
    selected_transfers.to_csv(subset_dir / "transfers.txt", index=False, encoding="utf-8")
    for name in ["agency.txt", "calendar.txt"]:
        (subset_dir / name).write_bytes((gtfs_dir / name).read_bytes())

    methodology = {
        "headway_basis": "full_service_day_median_of_consecutive_first_stop_departures",
        "pattern_definition": "route_id + first_stop_id + last_stop_id",
        "allowed_route_types": list(ALLOWED_ROUTE_TYPES),
        "initial_wait": "first boarded pattern median headway / 2",
        "transfer_wait": "each subsequently boarded pattern median headway / 2",
        "transfer_count": "number of boarded patterns - 1",
        "transfer_burden": "min(transfer_count / 3, 1)",
        "wait_burden": "min(total_expected_wait_min / 20, 1)",
        "transport_burden": "0.5 * transfer_burden + 0.5 * wait_burden",
    }
    (output_dir / "transit_cost_methodology.json").write_text(
        json.dumps(methodology, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    headway_values = pattern_headways["median_headway_min"]
    summary = {
        "source_gtfs": str(gtfs_dir),
        "boundary": str(boundary_path),
        "headway_basis": methodology["headway_basis"],
        "all_stops": int(len(stops)),
        "stops_inside_seoul_boundary": int(len(seoul_stop_ids)),
        "selected_stops_on_seoul_serving_trips": int(len(selected_stops)),
        "selected_routes": int(len(selected_routes)),
        "selected_trips": int(len(selected_trips)),
        "selected_stop_time_rows": int(filtered_stop_time_rows),
        "route_patterns": int(len(pattern_headways)),
        "route_patterns_using_route_fallback": int(
            pattern_headways["headway_source"].eq("route_median").sum()
        ),
        "route_patterns_using_global_fallback": int(
            pattern_headways["headway_source"].eq("global_median").sum()
        ),
        "median_pattern_headway_min": float(headway_values.median()),
        "p10_pattern_headway_min": float(headway_values.quantile(0.10)),
        "p90_pattern_headway_min": float(headway_values.quantile(0.90)),
        "median_expected_initial_wait_min": float(
            pattern_headways["expected_initial_wait_min"].median()
        ),
        "transfer_wait_edges": int(transfer_edge_count),
        "explicit_gtfs_transfers_retained": int(len(selected_transfers)),
    }
    (output_dir / "transit_build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pd.DataFrame([summary]).to_csv(
        output_dir / "transit_build_summary.csv", index=False, encoding="utf-8-sig"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gtfs-dir", type=Path, default=DEFAULT_GTFS_DIR)
    parser.add_argument("--boundary", type=Path, default=DEFAULT_BOUNDARY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    build_transit_inputs(arguments.gtfs_dir, arguments.boundary, arguments.output_dir)

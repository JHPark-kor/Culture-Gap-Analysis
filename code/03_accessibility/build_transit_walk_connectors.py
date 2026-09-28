"""Build 900 m walk connectors for grid-transit-facility routing.

Access connectors follow the directed walk network from a grid to a stop.
Egress connectors follow it from a stop to a facility. Off-network snap
segments count toward distance and time but not toward DEM slope metrics.
"""

from __future__ import annotations

import argparse
import heapq
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


DEFAULT_NODES = Path(
    "outputs/fixed_accessibility_inputs/terrain/seoul_walk_nodes_elevation.parquet"
)
DEFAULT_EDGES = Path(
    "outputs/fixed_accessibility_inputs/terrain/seoul_walk_edges_terrain.parquet"
)
DEFAULT_GRID_SNAP = Path(
    "outputs/fixed_accessibility_inputs/network_snap/grid_walk_node_snap.parquet"
)
DEFAULT_STOP_SNAP = Path(
    "outputs/fixed_accessibility_inputs/network_snap/transit_stop_walk_node_snap.parquet"
)
DEFAULT_FACILITY_SNAP = Path(
    "outputs/fixed_accessibility_inputs/network_snap/facility_walk_node_snap.parquet"
)
DEFAULT_OUTPUT_DIR = Path("outputs/fixed_accessibility_inputs/transit_connectors")

MAX_CONNECTOR_DISTANCE_M = 900.0
WALK_SPEED_M_PER_MIN = 60.0
WRITE_BATCH_ROWS = 100_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", type=Path, default=DEFAULT_NODES)
    parser.add_argument("--edges", type=Path, default=DEFAULT_EDGES)
    parser.add_argument("--grid-snap", type=Path, default=DEFAULT_GRID_SNAP)
    parser.add_argument("--stop-snap", type=Path, default=DEFAULT_STOP_SNAP)
    parser.add_argument("--facility-snap", type=Path, default=DEFAULT_FACILITY_SNAP)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--maximum-distance-m", type=float, default=MAX_CONNECTOR_DISTANCE_M
    )
    return parser.parse_args()


def build_reverse_adjacency(
    nodes: pd.DataFrame, edges: pd.DataFrame
) -> tuple[list[list[tuple[int, float, float, int]]], dict[int, int]]:
    node_ids = nodes["node_id"].astype("int64").to_numpy()
    node_to_position = {int(node_id): position for position, node_id in enumerate(node_ids)}
    reverse_adjacency: list[list[tuple[int, float, float, int]]] = [
        [] for _ in range(len(nodes))
    ]
    missing_edges = 0
    for edge in edges.itertuples(index=False):
        u_position = node_to_position.get(int(edge.u))
        v_position = node_to_position.get(int(edge.v))
        if u_position is None or v_position is None:
            missing_edges += 1
            continue
        reverse_adjacency[v_position].append(
            (
                u_position,
                float(edge.length_m),
                float(edge.slope_abs_deg),
                int(edge.steep_ge_8deg),
            )
        )
    if missing_edges:
        raise RuntimeError(f"Walk edges reference {missing_edges} missing nodes")
    return reverse_adjacency, node_to_position


def reverse_shortest_walk_paths(
    destination_position: int,
    adjacency: list[list[tuple[int, float, float, int]]],
    cutoff_m: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    node_count = len(adjacency)
    distances = np.full(node_count, np.inf, dtype=np.float64)
    slope_length_sum = np.zeros(node_count, dtype=np.float64)
    max_slope = np.zeros(node_count, dtype=np.float32)
    steep_length = np.zeros(node_count, dtype=np.float64)
    distances[destination_position] = 0.0
    queue: list[tuple[float, int]] = [(0.0, destination_position)]

    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances[node]:
            continue
        if distance > cutoff_m:
            break
        for predecessor, edge_length, edge_slope, edge_is_steep in adjacency[node]:
            candidate = distance + edge_length
            if candidate > cutoff_m or candidate >= distances[predecessor]:
                continue
            distances[predecessor] = candidate
            slope_length_sum[predecessor] = (
                slope_length_sum[node] + edge_length * edge_slope
            )
            max_slope[predecessor] = max(max_slope[node], edge_slope)
            steep_length[predecessor] = steep_length[node] + (
                edge_length if edge_is_steep else 0.0
            )
            heapq.heappush(queue, (candidate, predecessor))
    return distances, slope_length_sum, max_slope, steep_length


def _empty_access_buffer() -> dict[str, list[object]]:
    return {
        "GRID_CD": [],
        "stop_id": [],
        "grid_walk_node_id": [],
        "stop_walk_node_id": [],
        "network_walk_distance_m": [],
        "grid_snap_distance_m": [],
        "stop_snap_distance_m": [],
        "total_walk_distance_m": [],
        "elderly_walk_time_min": [],
        "network_slope_length_deg_m": [],
        "path_mean_abs_slope_deg": [],
        "path_max_abs_slope_deg": [],
        "network_steep_ge_8deg_length_m": [],
        "path_steep_ge_8deg_share": [],
    }


def _empty_egress_buffer() -> dict[str, list[object]]:
    return {
        "stop_id": [],
        "facility_id": [],
        "facility_category": [],
        "stop_walk_node_id": [],
        "facility_walk_node_id": [],
        "network_walk_distance_m": [],
        "stop_snap_distance_m": [],
        "facility_snap_distance_m": [],
        "total_walk_distance_m": [],
        "elderly_walk_time_min": [],
        "network_slope_length_deg_m": [],
        "path_mean_abs_slope_deg": [],
        "path_max_abs_slope_deg": [],
        "network_steep_ge_8deg_length_m": [],
        "path_steep_ge_8deg_share": [],
    }


def _flush(
    buffer: dict[str, list[object]],
    writer: pq.ParquetWriter | None,
    output_path: Path,
) -> tuple[pq.ParquetWriter, int]:
    table = pa.Table.from_pydict(buffer)
    if writer is None:
        writer = pq.ParquetWriter(output_path, table.schema, compression="zstd")
    writer.write_table(table)
    rows = table.num_rows
    for values in buffer.values():
        values.clear()
    return writer, rows


def _path_slope_values(
    network_distance: float,
    slope_length_sum: float,
    max_slope: float,
    steep_length: float,
) -> tuple[float, float, float]:
    if network_distance <= 0:
        return 0.0, float(max_slope), 0.0
    return (
        float(slope_length_sum / network_distance),
        float(max_slope),
        float(steep_length / network_distance),
    )


def build_access_connectors(
    grids: pd.DataFrame,
    stops: pd.DataFrame,
    adjacency: list[list[tuple[int, float, float, int]]],
    output_path: Path,
    maximum_distance_m: float,
) -> int:
    grid_rows_by_node: dict[int, list[int]] = {}
    for row_number, node_position in enumerate(grids["node_position"].to_numpy()):
        grid_rows_by_node.setdefault(int(node_position), []).append(row_number)
    stop_rows_by_node: dict[int, list[int]] = {}
    for row_number, node_position in enumerate(stops["node_position"].to_numpy()):
        stop_rows_by_node.setdefault(int(node_position), []).append(row_number)

    buffer = _empty_access_buffer()
    writer: pq.ParquetWriter | None = None
    written = 0
    stop_nodes = list(stop_rows_by_node.items())
    try:
        for source_number, (stop_node_position, stop_rows) in enumerate(stop_nodes, 1):
            distances, slope_sum, max_slope, steep_length = reverse_shortest_walk_paths(
                stop_node_position, adjacency, maximum_distance_m
            )
            for grid_node_position in np.flatnonzero(np.isfinite(distances)):
                grid_rows = grid_rows_by_node.get(int(grid_node_position))
                if not grid_rows:
                    continue
                network_distance = float(distances[grid_node_position])
                mean_slope, path_max_slope, steep_share = _path_slope_values(
                    network_distance,
                    float(slope_sum[grid_node_position]),
                    float(max_slope[grid_node_position]),
                    float(steep_length[grid_node_position]),
                )
                for stop_row in stop_rows:
                    stop_snap = float(stops.at[stop_row, "walk_snap_distance_m"])
                    for grid_row in grid_rows:
                        grid_snap = float(grids.at[grid_row, "walk_snap_distance_m"])
                        total_distance = grid_snap + network_distance + stop_snap
                        if total_distance > maximum_distance_m:
                            continue
                        buffer["GRID_CD"].append(str(grids.at[grid_row, "GRID_CD"]))
                        buffer["stop_id"].append(str(stops.at[stop_row, "stop_id"]))
                        buffer["grid_walk_node_id"].append(
                            int(grids.at[grid_row, "walk_node_id"])
                        )
                        buffer["stop_walk_node_id"].append(
                            int(stops.at[stop_row, "walk_node_id"])
                        )
                        buffer["network_walk_distance_m"].append(network_distance)
                        buffer["grid_snap_distance_m"].append(grid_snap)
                        buffer["stop_snap_distance_m"].append(stop_snap)
                        buffer["total_walk_distance_m"].append(total_distance)
                        buffer["elderly_walk_time_min"].append(
                            total_distance / WALK_SPEED_M_PER_MIN
                        )
                        buffer["network_slope_length_deg_m"].append(
                            float(slope_sum[grid_node_position])
                        )
                        buffer["path_mean_abs_slope_deg"].append(mean_slope)
                        buffer["path_max_abs_slope_deg"].append(path_max_slope)
                        buffer["network_steep_ge_8deg_length_m"].append(
                            float(steep_length[grid_node_position])
                        )
                        buffer["path_steep_ge_8deg_share"].append(steep_share)
                        if len(buffer["GRID_CD"]) >= WRITE_BATCH_ROWS:
                            writer, rows = _flush(buffer, writer, output_path)
                            written += rows
            if source_number % 500 == 0 or source_number == len(stop_nodes):
                print(
                    f"  access stop nodes={source_number:,}/{len(stop_nodes):,}, "
                    f"connectors={written + len(buffer['GRID_CD']):,}",
                    flush=True,
                )
        if buffer["GRID_CD"]:
            writer, rows = _flush(buffer, writer, output_path)
            written += rows
    finally:
        if writer is not None:
            writer.close()
    if writer is None:
        raise RuntimeError("No grid-to-stop access connectors were generated")
    return written


def build_egress_connectors(
    stops: pd.DataFrame,
    facilities: pd.DataFrame,
    adjacency: list[list[tuple[int, float, float, int]]],
    output_path: Path,
    maximum_distance_m: float,
) -> int:
    stop_rows_by_node: dict[int, list[int]] = {}
    for row_number, node_position in enumerate(stops["node_position"].to_numpy()):
        stop_rows_by_node.setdefault(int(node_position), []).append(row_number)

    buffer = _empty_egress_buffer()
    writer: pq.ParquetWriter | None = None
    written = 0
    try:
        for facility_number, facility in enumerate(facilities.itertuples(index=False), 1):
            distances, slope_sum, max_slope, steep_length = reverse_shortest_walk_paths(
                int(facility.node_position), adjacency, maximum_distance_m
            )
            for stop_node_position in np.flatnonzero(np.isfinite(distances)):
                stop_rows = stop_rows_by_node.get(int(stop_node_position))
                if not stop_rows:
                    continue
                network_distance = float(distances[stop_node_position])
                mean_slope, path_max_slope, steep_share = _path_slope_values(
                    network_distance,
                    float(slope_sum[stop_node_position]),
                    float(max_slope[stop_node_position]),
                    float(steep_length[stop_node_position]),
                )
                for stop_row in stop_rows:
                    stop_snap = float(stops.at[stop_row, "walk_snap_distance_m"])
                    facility_snap = float(facility.walk_snap_distance_m)
                    total_distance = stop_snap + network_distance + facility_snap
                    if total_distance > maximum_distance_m:
                        continue
                    buffer["stop_id"].append(str(stops.at[stop_row, "stop_id"]))
                    buffer["facility_id"].append(str(facility.facility_id))
                    buffer["facility_category"].append(str(facility.facility_category))
                    buffer["stop_walk_node_id"].append(
                        int(stops.at[stop_row, "walk_node_id"])
                    )
                    buffer["facility_walk_node_id"].append(int(facility.walk_node_id))
                    buffer["network_walk_distance_m"].append(network_distance)
                    buffer["stop_snap_distance_m"].append(stop_snap)
                    buffer["facility_snap_distance_m"].append(facility_snap)
                    buffer["total_walk_distance_m"].append(total_distance)
                    buffer["elderly_walk_time_min"].append(
                        total_distance / WALK_SPEED_M_PER_MIN
                    )
                    buffer["network_slope_length_deg_m"].append(
                        float(slope_sum[stop_node_position])
                    )
                    buffer["path_mean_abs_slope_deg"].append(mean_slope)
                    buffer["path_max_abs_slope_deg"].append(path_max_slope)
                    buffer["network_steep_ge_8deg_length_m"].append(
                        float(steep_length[stop_node_position])
                    )
                    buffer["path_steep_ge_8deg_share"].append(steep_share)
                    if len(buffer["stop_id"]) >= WRITE_BATCH_ROWS:
                        writer, rows = _flush(buffer, writer, output_path)
                        written += rows
            if facility_number % 25 == 0 or facility_number == len(facilities):
                print(
                    f"  egress facilities={facility_number:,}/{len(facilities):,}, "
                    f"connectors={written + len(buffer['stop_id']):,}",
                    flush=True,
                )
        if buffer["stop_id"]:
            writer, rows = _flush(buffer, writer, output_path)
            written += rows
    finally:
        if writer is not None:
            writer.close()
    if writer is None:
        raise RuntimeError("No stop-to-facility egress connectors were generated")
    return written


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    access_path = args.output_dir / "grid_to_transit_stop_walk_900m.parquet"
    egress_path = args.output_dir / "transit_stop_to_facility_walk_900m.parquet"

    nodes = pd.read_parquet(args.nodes).sort_values("node_id").reset_index(drop=True)
    edges = pd.read_parquet(args.edges)
    grids = pd.read_parquet(args.grid_snap).reset_index(drop=True)
    stops = pd.read_parquet(args.stop_snap).reset_index(drop=True)
    facilities = pd.read_parquet(args.facility_snap).reset_index(drop=True)
    if "취약노인수" not in grids.columns:
        raise RuntimeError("Grid snap input is missing 취약노인수")
    if (pd.to_numeric(grids["취약노인수"], errors="raise") <= 0).any():
        raise RuntimeError("Grid snap input contains grids with 취약노인수 <= 0")

    adjacency, node_to_position = build_reverse_adjacency(nodes, edges)
    grids["node_position"] = grids["walk_node_id"].map(node_to_position)
    stops["node_position"] = stops["walk_node_id"].map(node_to_position)
    facilities["node_position"] = facilities["walk_node_id"].map(node_to_position)
    if (
        grids["node_position"].isna().any()
        or stops["node_position"].isna().any()
        or facilities["node_position"].isna().any()
    ):
        raise RuntimeError("A snapped record refers to a walk node outside the terrain graph")
    grids["node_position"] = grids["node_position"].astype(int)
    stops["node_position"] = stops["node_position"].astype(int)
    facilities["node_position"] = facilities["node_position"].astype(int)

    eligible_stops = stops.loc[
        stops["within_1km_of_walk_network"].fillna(False)
        & (stops["walk_snap_distance_m"] <= args.maximum_distance_m)
    ].reset_index(drop=True)

    print("Building directed grid-to-stop access connectors", flush=True)
    access_count = build_access_connectors(
        grids, eligible_stops, adjacency, access_path, args.maximum_distance_m
    )
    print("Building directed stop-to-facility egress connectors", flush=True)
    egress_count = build_egress_connectors(
        eligible_stops,
        facilities,
        adjacency,
        egress_path,
        args.maximum_distance_m,
    )

    access = pd.read_parquet(access_path, columns=["GRID_CD", "stop_id"])
    egress = pd.read_parquet(egress_path, columns=["stop_id", "facility_id"])
    summary = {
        "maximum_connector_distance_m": float(args.maximum_distance_m),
        "maximum_connector_walk_time_min": float(
            args.maximum_distance_m / WALK_SPEED_M_PER_MIN
        ),
        "walk_speed_m_per_min": WALK_SPEED_M_PER_MIN,
        "directionality": {
            "access": "grid_to_stop_on_directed_walk_network",
            "egress": "stop_to_facility_on_directed_walk_network",
        },
        "slope_scope": "walk_network_edges_only; snap segments excluded from slope",
        "all_gtfs_stops": int(len(stops)),
        "eligible_stops": int(len(eligible_stops)),
        "eligible_stop_walk_nodes": int(eligible_stops["walk_node_id"].nunique()),
        "grid_to_stop_connectors": int(access_count),
        "grids_with_access_connector": int(access["GRID_CD"].nunique()),
        "stops_with_access_connector": int(access["stop_id"].nunique()),
        "stop_to_facility_connectors": int(egress_count),
        "stops_with_egress_connector": int(egress["stop_id"].nunique()),
        "facilities_with_egress_connector": int(egress["facility_id"].nunique()),
        "access_pair_duplicates": int(access.duplicated().sum()),
        "egress_pair_duplicates": int(egress.duplicated().sum()),
    }
    (args.output_dir / "transit_walk_connector_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pd.DataFrame([summary]).to_csv(
        args.output_dir / "transit_walk_connector_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

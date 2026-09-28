#!/usr/bin/env python3
"""Build grid-to-facility walking paths and fixed walking burden metrics."""

from __future__ import annotations

import argparse
import heapq
import json
import math
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
DEFAULT_FACILITY_SNAP = Path(
    "outputs/fixed_accessibility_inputs/network_snap/facility_walk_node_snap.parquet"
)
DEFAULT_OUTPUT = Path("outputs/fixed_accessibility_inputs/walk_paths")

MAX_WALK_DISTANCE_M = 3_600.0
DISTANCE_BURDEN_FULL_M = 600.0
WRITE_BATCH_ROWS = 100_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", type=Path, default=DEFAULT_NODES)
    parser.add_argument("--edges", type=Path, default=DEFAULT_EDGES)
    parser.add_argument("--grid-snap", type=Path, default=DEFAULT_GRID_SNAP)
    parser.add_argument("--facility-snap", type=Path, default=DEFAULT_FACILITY_SNAP)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-walk-distance-m", type=float, default=MAX_WALK_DISTANCE_M)
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
        raise ValueError(f"Edges reference {missing_edges} nodes absent from the node table")
    return reverse_adjacency, node_to_position


def shortest_paths_from_facility(
    source_position: int,
    adjacency: list[list[tuple[int, float, float, int]]],
    cutoff_m: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    node_count = len(adjacency)
    distances = np.full(node_count, np.inf, dtype=np.float64)
    slope_length_sum = np.zeros(node_count, dtype=np.float64)
    max_slope = np.zeros(node_count, dtype=np.float32)
    steep_length = np.zeros(node_count, dtype=np.float64)
    distances[source_position] = 0.0
    queue: list[tuple[float, int]] = [(0.0, source_position)]

    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances[node]:
            continue
        if distance > cutoff_m:
            break
        for neighbor, edge_length, edge_slope, edge_is_steep in adjacency[node]:
            candidate = distance + edge_length
            if candidate > cutoff_m or candidate >= distances[neighbor]:
                continue
            distances[neighbor] = candidate
            slope_length_sum[neighbor] = (
                slope_length_sum[node] + edge_length * edge_slope
            )
            max_slope[neighbor] = max(max_slope[node], edge_slope)
            steep_length[neighbor] = steep_length[node] + (
                edge_length if edge_is_steep else 0.0
            )
            heapq.heappush(queue, (candidate, neighbor))
    return distances, slope_length_sum, max_slope, steep_length


def empty_buffer() -> dict[str, list[object]]:
    return {
        "GRID_CD": [],
        "facility_id": [],
        "facility_category": [],
        "network_distance_m": [],
        "grid_snap_distance_m": [],
        "facility_snap_distance_m": [],
        "total_walk_distance_m": [],
        "elderly_walk_time_min": [],
        "path_mean_abs_slope_deg": [],
        "slope_burden": [],
        "path_max_abs_slope_deg": [],
        "path_steep_ge_8deg_share": [],
        "distance_burden": [],
        "transport_burden": [],
        "generalized_cost_walk": [],
    }


def flush_buffer(
    buffer: dict[str, list[object]],
    writer: pq.ParquetWriter | None,
    output_path: Path,
) -> tuple[pq.ParquetWriter, int]:
    table = pa.Table.from_pydict(buffer)
    if writer is None:
        writer = pq.ParquetWriter(output_path, table.schema, compression="zstd")
    writer.write_table(table)
    row_count = table.num_rows
    for values in buffer.values():
        values.clear()
    return writer, row_count


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path_output = args.output_dir / "walk_grid_facility_paths_60min.parquet"
    summary_output = args.output_dir / "walk_grid_category_summary.csv"
    build_summary_output = args.output_dir / "walk_path_build_summary.json"

    nodes = pd.read_parquet(args.nodes).sort_values("node_id").reset_index(drop=True)
    edges = pd.read_parquet(args.edges)
    grids = pd.read_parquet(args.grid_snap)
    facilities = pd.read_parquet(args.facility_snap)

    adjacency, node_to_position = build_reverse_adjacency(nodes, edges)
    grids = grids.reset_index(drop=True)
    facilities = facilities.reset_index(drop=True)
    grids["node_position"] = grids["walk_node_id"].map(node_to_position)
    facilities["node_position"] = facilities["walk_node_id"].map(node_to_position)
    if grids["node_position"].isna().any() or facilities["node_position"].isna().any():
        raise ValueError("A snapped node is absent from the terrain node table")
    grids["node_position"] = grids["node_position"].astype(int)
    facilities["node_position"] = facilities["node_position"].astype(int)

    grid_rows_by_node: dict[int, list[int]] = {}
    for row_number, node_position in enumerate(grids["node_position"].to_numpy()):
        grid_rows_by_node.setdefault(int(node_position), []).append(row_number)

    categories = facilities["facility_category"].drop_duplicates().astype(str).tolist()
    category_stats: dict[str, dict[str, np.ndarray]] = {}
    grid_count = len(grids)
    for category in categories:
        category_stats[category] = {
            "count_30": np.zeros(grid_count, dtype=np.int32),
            "count_60": np.zeros(grid_count, dtype=np.int32),
            "nearest_time": np.full(grid_count, np.inf, dtype=np.float64),
            "nearest_cost": np.full(grid_count, np.inf, dtype=np.float64),
            "nearest_facility": np.full(grid_count, "", dtype=object),
        }

    buffer = empty_buffer()
    writer: pq.ParquetWriter | None = None
    written_rows = 0
    facilities_with_no_pairs = 0

    try:
        for facility_number, facility in enumerate(facilities.itertuples(index=False), start=1):
            distances, slope_length_sum, max_slope, steep_length = (
                shortest_paths_from_facility(
                    int(facility.node_position), adjacency, args.max_walk_distance_m
                )
            )
            reachable_positions = np.flatnonzero(np.isfinite(distances))
            facility_pair_count = 0
            category = str(facility.facility_category)
            stats = category_stats[category]
            for node_position in reachable_positions:
                grid_rows = grid_rows_by_node.get(int(node_position))
                if not grid_rows:
                    continue
                network_distance = float(distances[node_position])
                if network_distance > 0:
                    mean_slope = float(slope_length_sum[node_position] / network_distance)
                    steep_share = float(steep_length[node_position] / network_distance)
                else:
                    mean_slope = 0.0
                    steep_share = 0.0
                slope_burden = float(np.clip((mean_slope - 2.0) / 6.0, 0.0, 1.0))
                path_max_slope = float(max_slope[node_position])

                for grid_row in grid_rows:
                    grid_snap_distance = float(
                        grids.at[grid_row, "walk_snap_distance_m"]
                    )
                    facility_snap_distance = float(facility.walk_snap_distance_m)
                    total_distance = (
                        network_distance + grid_snap_distance + facility_snap_distance
                    )
                    if total_distance > args.max_walk_distance_m:
                        continue
                    walk_time = total_distance / 60.0
                    distance_burden = min(total_distance / DISTANCE_BURDEN_FULL_M, 1.0)
                    generalized_cost = (distance_burden + slope_burden) / 3.0

                    buffer["GRID_CD"].append(str(grids.at[grid_row, "GRID_CD"]))
                    buffer["facility_id"].append(str(facility.facility_id))
                    buffer["facility_category"].append(category)
                    buffer["network_distance_m"].append(network_distance)
                    buffer["grid_snap_distance_m"].append(grid_snap_distance)
                    buffer["facility_snap_distance_m"].append(facility_snap_distance)
                    buffer["total_walk_distance_m"].append(total_distance)
                    buffer["elderly_walk_time_min"].append(walk_time)
                    buffer["path_mean_abs_slope_deg"].append(mean_slope)
                    buffer["slope_burden"].append(slope_burden)
                    buffer["path_max_abs_slope_deg"].append(path_max_slope)
                    buffer["path_steep_ge_8deg_share"].append(steep_share)
                    buffer["distance_burden"].append(distance_burden)
                    buffer["transport_burden"].append(0.0)
                    buffer["generalized_cost_walk"].append(generalized_cost)
                    facility_pair_count += 1

                    stats["count_60"][grid_row] += 1
                    if walk_time <= 30.0:
                        stats["count_30"][grid_row] += 1
                    if walk_time < stats["nearest_time"][grid_row]:
                        stats["nearest_time"][grid_row] = walk_time
                        stats["nearest_facility"][grid_row] = str(facility.facility_id)
                    if generalized_cost < stats["nearest_cost"][grid_row]:
                        stats["nearest_cost"][grid_row] = generalized_cost

                    if len(buffer["GRID_CD"]) >= WRITE_BATCH_ROWS:
                        writer, added = flush_buffer(buffer, writer, path_output)
                        written_rows += added

            if facility_pair_count == 0:
                facilities_with_no_pairs += 1
            if facility_number % 25 == 0 or facility_number == len(facilities):
                print(
                    f"facilities={facility_number}/{len(facilities)}, "
                    f"path_rows={written_rows + len(buffer['GRID_CD']):,}",
                    flush=True,
                )
        if buffer["GRID_CD"]:
            writer, added = flush_buffer(buffer, writer, path_output)
            written_rows += added
    finally:
        if writer is not None:
            writer.close()

    if writer is None:
        raise RuntimeError("No walking paths were generated")

    summaries: list[pd.DataFrame] = []
    base = grids[["GRID_CD", "행정동코드", "시군구", "행정동"]].copy()
    for category in categories:
        stats = category_stats[category]
        frame = base.copy()
        frame["facility_category"] = category
        frame["walk_facility_count_30min"] = stats["count_30"]
        frame["walk_facility_count_60min"] = stats["count_60"]
        frame["nearest_walk_time_min"] = np.where(
            np.isfinite(stats["nearest_time"]), stats["nearest_time"], np.nan
        )
        frame["nearest_walk_generalized_cost"] = np.where(
            np.isfinite(stats["nearest_cost"]), stats["nearest_cost"], np.nan
        )
        frame["nearest_walk_facility_id"] = pd.Series(
            stats["nearest_facility"], dtype="string"
        ).replace("", pd.NA)
        summaries.append(frame)
    grid_summary = pd.concat(summaries, ignore_index=True)
    grid_summary.to_csv(summary_output, index=False, encoding="utf-8-sig")

    path_file = pq.ParquetFile(path_output)
    if path_file.metadata.num_rows != written_rows:
        raise AssertionError("Parquet row count does not match generated row count")
    build_summary = {
        "grid_rows": int(len(grids)),
        "facility_rows": int(len(facilities)),
        "facilities_by_category": {
            str(key): int(value)
            for key, value in facilities["facility_category"].value_counts().items()
        },
        "maximum_walk_distance_m": float(args.max_walk_distance_m),
        "maximum_walk_time_elderly_min": float(args.max_walk_distance_m / 60.0),
        "distance_burden": "min(total_walk_distance_m / 600, 1)",
        "slope_burden": "clip((path_mean_abs_slope_deg - 2) / 6, 0, 1)",
        "transport_burden_for_walk": 0.0,
        "generalized_cost_walk": "(distance_burden + slope_burden + 0) / 3",
        "slope_metric_scope": "network_edges_only; off-network snap segments have no DEM slope",
        "path_rows": int(written_rows),
        "facilities_with_no_grid_within_60min": int(facilities_with_no_pairs),
        "grid_category_summary_rows": int(len(grid_summary)),
    }
    build_summary_output.write_text(
        json.dumps(build_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(build_summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

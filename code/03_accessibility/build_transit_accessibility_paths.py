"""Build grid-to-facility transit paths and fixed burden metrics.

Each route minimizes expected journey time over the static all-day-median
network: access walk + initial wait + ride + transfer walk/wait + egress walk.
The selected route is then evaluated with the fixed distance, slope, and
transport burden definitions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra


DEFAULT_NETWORK_DIR = Path("outputs/fixed_accessibility_inputs/transit_network")
DEFAULT_CONNECTOR_DIR = Path("outputs/fixed_accessibility_inputs/transit_connectors")
DEFAULT_GRID_SNAP = Path(
    "outputs/fixed_accessibility_inputs/network_snap/grid_walk_node_snap.parquet"
)
DEFAULT_FACILITY_SNAP = Path(
    "outputs/fixed_accessibility_inputs/network_snap/facility_walk_node_snap.parquet"
)
DEFAULT_OUTPUT_DIR = Path("outputs/fixed_accessibility_inputs/transit_paths")

MAX_JOURNEY_TIME_MIN = 60.0
DISTANCE_BURDEN_FULL_MIN = 10.0
WAIT_BURDEN_FULL_MIN = 20.0
TRANSFER_BURDEN_FULL_COUNT = 3.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--network-dir", type=Path, default=DEFAULT_NETWORK_DIR)
    parser.add_argument("--connector-dir", type=Path, default=DEFAULT_CONNECTOR_DIR)
    parser.add_argument("--grid-snap", type=Path, default=DEFAULT_GRID_SNAP)
    parser.add_argument("--facility-snap", type=Path, default=DEFAULT_FACILITY_SNAP)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--maximum-journey-time-min", type=float, default=MAX_JOURNEY_TIME_MIN
    )
    parser.add_argument(
        "--facility-limit",
        type=int,
        default=None,
        help="Optional deterministic prefix of facilities for a development run.",
    )
    return parser.parse_args()


def _map_state_ids(values: pd.Series, state_to_index: pd.Series) -> np.ndarray:
    mapped = values.astype(str).map(state_to_index)
    if mapped.isna().any():
        raise RuntimeError("A transit edge refers to an unknown state ID")
    return mapped.to_numpy(dtype=np.int32)


def _load_transfer_edge_parts(
    path: Path,
    state_to_index: pd.Series,
) -> tuple[list[np.ndarray], list[np.ndarray], list[np.ndarray], list[np.ndarray]]:
    reverse_rows: list[np.ndarray] = []
    reverse_cols: list[np.ndarray] = []
    costs: list[np.ndarray] = []
    waits: list[np.ndarray] = []
    parquet_file = pq.ParquetFile(path)
    columns = [
        "from_state_id",
        "to_state_id",
        "min_transfer_time_min",
        "expected_wait_min",
        "edge_time_min",
    ]
    for batch_number, batch in enumerate(
        parquet_file.iter_batches(batch_size=250_000, columns=columns), 1
    ):
        frame = batch.to_pandas()
        source = _map_state_ids(frame["from_state_id"], state_to_index)
        target = _map_state_ids(frame["to_state_id"], state_to_index)
        reverse_rows.append(target)
        reverse_cols.append(source)
        costs.append(frame["edge_time_min"].to_numpy(dtype=np.float64))
        waits.append(frame["expected_wait_min"].to_numpy(dtype=np.float64))
        if batch_number % 4 == 0:
            print(f"  loaded transfer batches={batch_number:,}", flush=True)
    return reverse_rows, reverse_cols, costs, waits


def build_reverse_transit_graph(
    network_dir: Path,
    states: pd.DataFrame,
    facilities: pd.DataFrame,
    egress: pd.DataFrame,
) -> tuple[csr_matrix, csr_matrix, dict[str, np.ndarray | int]]:
    state_count = len(states)
    facility_count = len(facilities)
    total_node_count = state_count + facility_count
    state_to_index = pd.Series(
        np.arange(state_count, dtype=np.int32), index=states["state_id"].astype(str)
    )
    facility_to_index = pd.Series(
        np.arange(facility_count, dtype=np.int32),
        index=facilities["facility_id"].astype(str),
    )

    ride = pd.read_parquet(
        network_dir / "transit_pattern_ride_edges.parquet",
        columns=["from_state_id", "to_state_id", "in_vehicle_time_min"],
    )
    ride_source = _map_state_ids(ride["from_state_id"], state_to_index)
    ride_target = _map_state_ids(ride["to_state_id"], state_to_index)
    ride_time = ride["in_vehicle_time_min"].to_numpy(dtype=np.float64)

    transfer_rows, transfer_cols, transfer_costs, transfer_waits = (
        _load_transfer_edge_parts(
            network_dir / "transit_pattern_state_transfer_edges.parquet",
            state_to_index,
        )
    )
    transfer_rows_array = np.concatenate(transfer_rows)
    transfer_cols_array = np.concatenate(transfer_cols)
    transfer_cost_array = np.concatenate(transfer_costs)
    transfer_wait_array = np.concatenate(transfer_waits)
    transfer_min_time_array = transfer_cost_array - transfer_wait_array

    egress_work = egress.reset_index(names="egress_connector_index").merge(
        states[["stop_id", "state_index"]], on="stop_id", how="inner", validate="many_to_many"
    )
    egress_facility_code = egress_work["facility_id"].astype(str).map(facility_to_index)
    if egress_facility_code.isna().any():
        raise RuntimeError("An egress connector refers to an unknown facility")
    egress_reverse_rows = (
        state_count + egress_facility_code.to_numpy(dtype=np.int32)
    )
    egress_reverse_cols = egress_work["state_index"].to_numpy(dtype=np.int32)
    egress_cost = egress_work["elderly_walk_time_min"].to_numpy(dtype=np.float64)

    reverse_rows = np.concatenate(
        [ride_target, transfer_rows_array, egress_reverse_rows]
    ).astype(np.int32, copy=False)
    reverse_cols = np.concatenate(
        [ride_source, transfer_cols_array, egress_reverse_cols]
    ).astype(np.int32, copy=False)
    edge_cost = np.concatenate([ride_time, transfer_cost_array, egress_cost])
    if np.any(edge_cost < 0):
        raise RuntimeError("Transit routing graph contains a negative edge cost")

    ride_count = len(ride_time)
    transfer_count = len(transfer_cost_array)
    egress_edge_count = len(egress_cost)
    edge_count = len(edge_cost)

    edge_wait = np.zeros(edge_count, dtype=np.float64)
    edge_wait[ride_count : ride_count + transfer_count] = transfer_wait_array
    edge_transfer_increment = np.zeros(edge_count, dtype=np.int16)
    edge_transfer_increment[ride_count : ride_count + transfer_count] = 1
    edge_movement_time = edge_cost - edge_wait
    edge_in_vehicle_time = np.zeros(edge_count, dtype=np.float64)
    edge_in_vehicle_time[:ride_count] = ride_time
    edge_transfer_min_time = np.zeros(edge_count, dtype=np.float64)
    edge_transfer_min_time[ride_count : ride_count + transfer_count] = (
        transfer_min_time_array
    )
    edge_egress_connector = np.full(edge_count, -1, dtype=np.int32)
    edge_egress_connector[ride_count + transfer_count :] = egress_work[
        "egress_connector_index"
    ].to_numpy(dtype=np.int32)

    graph = csr_matrix(
        (edge_cost, (reverse_rows, reverse_cols)),
        shape=(total_node_count, total_node_count),
    )
    reverse_edge_ids = csr_matrix(
        (
            np.arange(1, edge_count + 1, dtype=np.int32),
            (reverse_rows, reverse_cols),
        ),
        shape=(total_node_count, total_node_count),
    )
    if graph.nnz != edge_count or reverse_edge_ids.nnz != edge_count:
        raise RuntimeError(
            "Parallel reverse graph edges were found; edge attributes would be ambiguous"
        )
    metrics: dict[str, np.ndarray | int] = {
        "state_count": state_count,
        "facility_count": facility_count,
        "ride_edge_count": ride_count,
        "transfer_edge_count": transfer_count,
        "egress_edge_count": egress_edge_count,
        "edge_wait": edge_wait,
        "edge_transfer_increment": edge_transfer_increment,
        "edge_movement_time": edge_movement_time,
        "edge_in_vehicle_time": edge_in_vehicle_time,
        "edge_transfer_min_time": edge_transfer_min_time,
        "edge_egress_connector": edge_egress_connector,
    }
    return graph, reverse_edge_ids, metrics


def _first_choice_by_code(
    codes: np.ndarray,
    candidate_indices: np.ndarray,
    values: np.ndarray,
    output_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    minimum = np.full(output_size, np.inf, dtype=np.float64)
    np.minimum.at(minimum, codes, values)
    is_best = np.isfinite(values) & np.isclose(
        values, minimum[codes], rtol=0.0, atol=1e-10
    )
    best_candidates = candidate_indices[is_best]
    best_codes = codes[is_best]
    unique_codes, first_positions = np.unique(best_codes, return_index=True)
    selected = np.full(output_size, -1, dtype=np.int32)
    selected[unique_codes] = best_candidates[first_positions].astype(np.int32)
    return minimum, selected


def _selected_path_attributes(
    selected_states: np.ndarray,
    source_node: int,
    distances: np.ndarray,
    predecessors: np.ndarray,
    reverse_edge_ids: csr_matrix,
    metrics: dict[str, np.ndarray | int],
) -> dict[str, np.ndarray]:
    node_count = len(distances)
    reachable_states = np.flatnonzero(
        np.isfinite(distances[: int(metrics["state_count"])])
    ).astype(np.int32)
    parents = predecessors[reachable_states].astype(np.int32, copy=False)
    if np.any(parents < 0):
        raise RuntimeError("A reachable state has no reverse shortest-path predecessor")
    selected_edge_values = np.asarray(
        reverse_edge_ids[parents, reachable_states]
    ).reshape(-1)
    if np.any(selected_edge_values <= 0):
        raise RuntimeError("A shortest-path predecessor has no matching edge ID")
    edge_for_node = np.full(node_count, -1, dtype=np.int32)
    edge_for_node[reachable_states] = selected_edge_values.astype(np.int32) - 1

    cumulative_wait = np.zeros(node_count, dtype=np.float64)
    cumulative_transfer_count = np.zeros(node_count, dtype=np.int16)
    cumulative_movement = np.zeros(node_count, dtype=np.float64)
    cumulative_in_vehicle = np.zeros(node_count, dtype=np.float64)
    cumulative_transfer_min = np.zeros(node_count, dtype=np.float64)
    egress_connector = np.full(node_count, -1, dtype=np.int32)
    computed = np.zeros(node_count, dtype=bool)
    computed[source_node] = True

    edge_wait = metrics["edge_wait"]
    edge_transfer_increment = metrics["edge_transfer_increment"]
    edge_movement_time = metrics["edge_movement_time"]
    edge_in_vehicle_time = metrics["edge_in_vehicle_time"]
    edge_transfer_min_time = metrics["edge_transfer_min_time"]
    edge_egress_connector = metrics["edge_egress_connector"]
    assert isinstance(edge_wait, np.ndarray)
    assert isinstance(edge_transfer_increment, np.ndarray)
    assert isinstance(edge_movement_time, np.ndarray)
    assert isinstance(edge_in_vehicle_time, np.ndarray)
    assert isinstance(edge_transfer_min_time, np.ndarray)
    assert isinstance(edge_egress_connector, np.ndarray)

    for selected_state in np.unique(selected_states):
        node = int(selected_state)
        stack: list[int] = []
        while not computed[node]:
            if node < 0 or predecessors[node] < 0:
                raise RuntimeError("Selected transit path did not reach its facility source")
            stack.append(node)
            node = int(predecessors[node])
        while stack:
            child = stack.pop()
            parent = int(predecessors[child])
            edge_id = int(edge_for_node[child])
            if edge_id < 0:
                raise RuntimeError("Selected transit path is missing an edge attribute")
            cumulative_wait[child] = cumulative_wait[parent] + edge_wait[edge_id]
            cumulative_transfer_count[child] = (
                cumulative_transfer_count[parent]
                + edge_transfer_increment[edge_id]
            )
            cumulative_movement[child] = (
                cumulative_movement[parent] + edge_movement_time[edge_id]
            )
            cumulative_in_vehicle[child] = (
                cumulative_in_vehicle[parent] + edge_in_vehicle_time[edge_id]
            )
            cumulative_transfer_min[child] = (
                cumulative_transfer_min[parent] + edge_transfer_min_time[edge_id]
            )
            connector_index = int(edge_egress_connector[edge_id])
            egress_connector[child] = (
                connector_index if connector_index >= 0 else egress_connector[parent]
            )
            computed[child] = True

    return {
        "transfer_wait_min": cumulative_wait[selected_states],
        "transfer_count": cumulative_transfer_count[selected_states],
        "movement_time_min": cumulative_movement[selected_states],
        "in_vehicle_time_min": cumulative_in_vehicle[selected_states],
        "transfer_min_time_min": cumulative_transfer_min[selected_states],
        "egress_connector_index": egress_connector[selected_states],
    }


def _write_frame(
    frame: pd.DataFrame,
    writer: pq.ParquetWriter | None,
    output_path: Path,
) -> pq.ParquetWriter:
    table = pa.Table.from_pandas(frame, preserve_index=False)
    if writer is None:
        writer = pq.ParquetWriter(output_path, table.schema, compression="zstd")
    writer.write_table(table)
    return writer


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "transit_grid_facility_paths_60min.parquet"
    summary_path = args.output_dir / "transit_grid_category_summary.csv"

    states = pd.read_parquet(
        args.network_dir / "transit_pattern_stop_states.parquet"
    ).reset_index(drop=True)
    states["state_index"] = np.arange(len(states), dtype=np.int32)
    access = pd.read_parquet(
        args.connector_dir / "grid_to_transit_stop_walk_600m.parquet"
    ).reset_index(drop=True)
    egress = pd.read_parquet(
        args.connector_dir / "transit_stop_to_facility_walk_600m.parquet"
    ).reset_index(drop=True)
    grids = pd.read_parquet(args.grid_snap).reset_index(drop=True)
    facilities = pd.read_parquet(args.facility_snap).reset_index(drop=True)
    if args.facility_limit is not None:
        facilities = facilities.head(args.facility_limit).reset_index(drop=True)
        egress = egress.loc[
            egress["facility_id"].astype(str).isin(
                set(facilities["facility_id"].astype(str))
            )
        ].reset_index(drop=True)

    print("Building sparse reverse transit graph", flush=True)
    graph, reverse_edge_ids, metrics = build_reverse_transit_graph(
        args.network_dir, states, facilities, egress
    )
    state_count = int(metrics["state_count"])

    grid_to_code = pd.Series(
        np.arange(len(grids), dtype=np.int32), index=grids["GRID_CD"].astype(str)
    )
    facility_to_code = pd.Series(
        np.arange(len(facilities), dtype=np.int32),
        index=facilities["facility_id"].astype(str),
    )
    access["grid_code"] = access["GRID_CD"].astype(str).map(grid_to_code)
    if access["grid_code"].isna().any():
        raise RuntimeError("An access connector refers to an unknown grid")
    access["grid_code"] = access["grid_code"].astype(np.int32)

    access_stop_ids = pd.Index(access["stop_id"].astype(str).unique())
    stop_to_code = pd.Series(
        np.arange(len(access_stop_ids), dtype=np.int32), index=access_stop_ids
    )
    access["stop_code"] = access["stop_id"].astype(str).map(stop_to_code).astype(np.int32)
    states["access_stop_code"] = states["stop_id"].astype(str).map(stop_to_code)
    board_states = states.loc[states["access_stop_code"].notna()].copy()
    board_state_indices = board_states["state_index"].to_numpy(dtype=np.int32)
    board_stop_codes = board_states["access_stop_code"].to_numpy(dtype=np.int32)
    board_initial_wait = board_states["expected_initial_wait_min"].to_numpy(
        dtype=np.float64
    )

    access_grid_codes = access["grid_code"].to_numpy(dtype=np.int32)
    access_stop_codes = access["stop_code"].to_numpy(dtype=np.int32)
    access_time = access["elderly_walk_time_min"].to_numpy(dtype=np.float64)
    access_row_indices = np.arange(len(access), dtype=np.int32)

    categories = facilities["facility_category"].drop_duplicates().astype(str).tolist()
    category_stats: dict[str, dict[str, np.ndarray]] = {}
    for category in categories:
        category_stats[category] = {
            "count_30": np.zeros(len(grids), dtype=np.int32),
            "count_60": np.zeros(len(grids), dtype=np.int32),
            "nearest_time": np.full(len(grids), np.inf, dtype=np.float64),
            "nearest_cost": np.full(len(grids), np.inf, dtype=np.float64),
            "nearest_facility": np.full(len(grids), "", dtype=object),
        }

    writer: pq.ParquetWriter | None = None
    written_rows = 0
    facilities_without_path = 0
    component_time_error_max = 0.0
    try:
        for facility_number, facility in enumerate(facilities.itertuples(index=False), 1):
            facility_code = int(facility_to_code[str(facility.facility_id)])
            source_node = state_count + facility_code
            distances, predecessors = dijkstra(
                graph,
                directed=True,
                indices=source_node,
                return_predecessors=True,
                limit=args.maximum_journey_time_min,
            )
            board_values = distances[board_state_indices] + board_initial_wait
            stop_cost, best_state_by_stop = _first_choice_by_code(
                board_stop_codes,
                board_state_indices,
                board_values,
                len(access_stop_ids),
            )
            access_values = access_time + stop_cost[access_stop_codes]
            grid_minimum, best_access_by_grid = _first_choice_by_code(
                access_grid_codes,
                access_row_indices,
                access_values,
                len(grids),
            )
            reachable_grid_codes = np.flatnonzero(
                np.isfinite(grid_minimum)
                & (grid_minimum <= args.maximum_journey_time_min)
                & (best_access_by_grid >= 0)
            ).astype(np.int32)
            if not len(reachable_grid_codes):
                facilities_without_path += 1
                print(
                    f"  facilities={facility_number:,}/{len(facilities):,}, "
                    f"facility={facility.facility_id}, paths=0",
                    flush=True,
                )
                continue

            selected_access_rows = best_access_by_grid[reachable_grid_codes]
            selected_access = access.iloc[selected_access_rows].reset_index(drop=True)
            selected_stop_codes = access_stop_codes[selected_access_rows]
            selected_states = best_state_by_stop[selected_stop_codes]
            if np.any(selected_states < 0):
                raise RuntimeError("A selected access stop has no boarding state")

            path_attributes = _selected_path_attributes(
                selected_states,
                source_node,
                distances,
                predecessors,
                reverse_edge_ids,
                metrics,
            )
            egress_indices = path_attributes["egress_connector_index"]
            if np.any(egress_indices < 0):
                raise RuntimeError("A selected transit path has no egress connector")
            selected_egress = egress.iloc[egress_indices].reset_index(drop=True)

            initial_wait = states.loc[
                selected_states, "expected_initial_wait_min"
            ].to_numpy(dtype=np.float64)
            transfer_wait = path_attributes["transfer_wait_min"].astype(np.float64)
            total_wait = initial_wait + transfer_wait
            transfer_count = path_attributes["transfer_count"].astype(np.int16)
            in_vehicle = path_attributes["in_vehicle_time_min"].astype(np.float64)
            transfer_min_time = path_attributes["transfer_min_time_min"].astype(
                np.float64
            )
            access_walk_time = selected_access["elderly_walk_time_min"].to_numpy(
                dtype=np.float64
            )
            egress_walk_time = selected_egress["elderly_walk_time_min"].to_numpy(
                dtype=np.float64
            )
            movement_time = access_walk_time + path_attributes["movement_time_min"]
            journey_time = grid_minimum[reachable_grid_codes]
            component_time = movement_time + total_wait
            component_time_error = np.abs(component_time - journey_time)
            component_time_error_max = max(
                component_time_error_max, float(component_time_error.max(initial=0.0))
            )
            if np.any(component_time_error > 1e-7):
                raise RuntimeError("Transit path time does not reconcile to its components")

            network_walk_distance = (
                selected_access["network_walk_distance_m"].to_numpy(dtype=np.float64)
                + selected_egress["network_walk_distance_m"].to_numpy(dtype=np.float64)
            )
            slope_length = (
                selected_access["network_slope_length_deg_m"].to_numpy(dtype=np.float64)
                + selected_egress["network_slope_length_deg_m"].to_numpy(dtype=np.float64)
            )
            mean_slope = np.divide(
                slope_length,
                network_walk_distance,
                out=np.zeros_like(slope_length),
                where=network_walk_distance > 0,
            )
            max_slope = np.maximum(
                selected_access["path_max_abs_slope_deg"].to_numpy(dtype=np.float64),
                selected_egress["path_max_abs_slope_deg"].to_numpy(dtype=np.float64),
            )
            steep_length = (
                selected_access["network_steep_ge_8deg_length_m"].to_numpy(
                    dtype=np.float64
                )
                + selected_egress["network_steep_ge_8deg_length_m"].to_numpy(
                    dtype=np.float64
                )
            )
            steep_share = np.divide(
                steep_length,
                network_walk_distance,
                out=np.zeros_like(steep_length),
                where=network_walk_distance > 0,
            )

            distance_burden = np.clip(
                movement_time / DISTANCE_BURDEN_FULL_MIN, 0.0, 1.0
            )
            slope_burden = np.clip((mean_slope - 2.0) / 6.0, 0.0, 1.0)
            transfer_burden = np.clip(
                transfer_count.astype(np.float64) / TRANSFER_BURDEN_FULL_COUNT,
                0.0,
                1.0,
            )
            wait_burden = np.clip(total_wait / WAIT_BURDEN_FULL_MIN, 0.0, 1.0)
            transport_burden = 0.5 * transfer_burden + 0.5 * wait_burden
            generalized_cost = (
                distance_burden + slope_burden + transport_burden
            ) / 3.0
            known_walk_distance = (
                selected_access["total_walk_distance_m"].to_numpy(dtype=np.float64)
                + selected_egress["total_walk_distance_m"].to_numpy(dtype=np.float64)
            )

            output = pd.DataFrame(
                {
                    "GRID_CD": selected_access["GRID_CD"].astype(str).to_numpy(),
                    "facility_id": str(facility.facility_id),
                    "facility_category": str(facility.facility_category),
                    "route_selection": "minimum_expected_journey_time",
                    "access_stop_id": selected_access["stop_id"].astype(str).to_numpy(),
                    "first_pattern_state_id": states.loc[
                        selected_states, "state_id"
                    ].astype(str).to_numpy(),
                    "first_pattern_id": states.loc[
                        selected_states, "pattern_id"
                    ].astype(str).to_numpy(),
                    "egress_stop_id": selected_egress["stop_id"].astype(str).to_numpy(),
                    "access_walk_distance_m": selected_access[
                        "total_walk_distance_m"
                    ].to_numpy(dtype=np.float64),
                    "egress_walk_distance_m": selected_egress[
                        "total_walk_distance_m"
                    ].to_numpy(dtype=np.float64),
                    "known_total_walk_distance_m": known_walk_distance,
                    "access_walk_time_min": access_walk_time,
                    "egress_walk_time_min": egress_walk_time,
                    "transfer_min_time_min": transfer_min_time,
                    "in_vehicle_time_min": in_vehicle,
                    "movement_time_excluding_wait_min": movement_time,
                    "initial_expected_wait_min": initial_wait,
                    "transfer_expected_wait_min": transfer_wait,
                    "total_expected_wait_min": total_wait,
                    "journey_time_with_wait_min": journey_time,
                    "transfer_count": transfer_count,
                    "boarding_count": transfer_count + 1,
                    "network_walk_distance_for_slope_m": network_walk_distance,
                    "network_walk_slope_length_deg_m": slope_length,
                    "path_mean_abs_slope_deg": mean_slope,
                    "slope_burden": slope_burden,
                    "path_max_abs_slope_deg": max_slope,
                    "network_walk_steep_ge_8deg_length_m": steep_length,
                    "path_steep_ge_8deg_share": steep_share,
                    "distance_burden": distance_burden,
                    "transfer_burden": transfer_burden,
                    "wait_burden": wait_burden,
                    "transport_burden": transport_burden,
                    "generalized_cost_transit": generalized_cost,
                }
            )
            writer = _write_frame(output, writer, output_path)
            written_rows += len(output)

            stats = category_stats[str(facility.facility_category)]
            stats["count_60"][reachable_grid_codes] += 1
            within_30 = journey_time <= 30.0
            stats["count_30"][reachable_grid_codes[within_30]] += 1
            faster = journey_time < stats["nearest_time"][reachable_grid_codes]
            if np.any(faster):
                target_codes = reachable_grid_codes[faster]
                stats["nearest_time"][target_codes] = journey_time[faster]
                stats["nearest_facility"][target_codes] = str(facility.facility_id)
            lower_cost = generalized_cost < stats["nearest_cost"][reachable_grid_codes]
            if np.any(lower_cost):
                target_codes = reachable_grid_codes[lower_cost]
                stats["nearest_cost"][target_codes] = generalized_cost[lower_cost]

            if facility_number % 10 == 0 or facility_number == len(facilities):
                print(
                    f"  facilities={facility_number:,}/{len(facilities):,}, "
                    f"path_rows={written_rows:,}",
                    flush=True,
                )
    finally:
        if writer is not None:
            writer.close()
    if writer is None:
        raise RuntimeError("No grid-to-facility transit paths were generated")

    summary_frames: list[pd.DataFrame] = []
    grid_base = grids[["GRID_CD", "행정동코드", "시군구", "행정동"]].copy()
    for category in categories:
        stats = category_stats[category]
        frame = grid_base.copy()
        frame["facility_category"] = category
        frame["transit_facility_count_30min"] = stats["count_30"]
        frame["transit_facility_count_60min"] = stats["count_60"]
        frame["nearest_transit_journey_time_min"] = np.where(
            np.isfinite(stats["nearest_time"]), stats["nearest_time"], np.nan
        )
        frame["nearest_transit_generalized_cost"] = np.where(
            np.isfinite(stats["nearest_cost"]), stats["nearest_cost"], np.nan
        )
        frame["nearest_transit_facility_id"] = pd.Series(
            stats["nearest_facility"], dtype="string"
        ).replace("", pd.NA)
        summary_frames.append(frame)
    grid_summary = pd.concat(summary_frames, ignore_index=True)
    grid_summary.to_csv(summary_path, index=False, encoding="utf-8-sig")

    output_metadata = pq.ParquetFile(output_path).metadata
    if output_metadata.num_rows != written_rows:
        raise RuntimeError("Transit path Parquet row count does not match generated rows")
    build_summary = {
        "route_selection": "minimum_expected_journey_time",
        "headway_basis": "full_service_day_median",
        "time_band_condition": "none",
        "maximum_access_walk_m": 600.0,
        "maximum_egress_walk_m": 600.0,
        "maximum_journey_time_with_wait_min": float(args.maximum_journey_time_min),
        "distance_burden": "min(movement_time_excluding_wait_min / 10, 1)",
        "distance_burden_interpretation": (
            "10-minute movement reference, equivalent to 600 m at 60 m/min; "
            "expected wait excluded"
        ),
        "slope_burden": "clip((path_mean_abs_slope_deg - 2) / 6, 0, 1)",
        "slope_scope": (
            "known access and egress walk-network edges only; snap segments and "
            "GTFS transfer connectors have no DEM slope"
        ),
        "transfer_burden": "min(transfer_count / 3, 1)",
        "wait_burden": "min(total_expected_wait_min / 20, 1)",
        "transport_burden": "0.5 * transfer_burden + 0.5 * wait_burden",
        "generalized_cost_transit": (
            "(distance_burden + slope_burden + transport_burden) / 3"
        ),
        "state_nodes": state_count,
        "facility_nodes": int(metrics["facility_count"]),
        "reverse_graph_edges": int(graph.nnz),
        "ride_edges": int(metrics["ride_edge_count"]),
        "transfer_edges": int(metrics["transfer_edge_count"]),
        "egress_state_edges": int(metrics["egress_edge_count"]),
        "grid_rows": int(len(grids)),
        "facility_rows": int(len(facilities)),
        "facilities_by_category": {
            str(key): int(value)
            for key, value in facilities["facility_category"].value_counts().items()
        },
        "facilities_without_transit_path": int(facilities_without_path),
        "path_rows": int(written_rows),
        "grid_category_summary_rows": int(len(grid_summary)),
        "maximum_component_time_reconciliation_error_min": float(
            component_time_error_max
        ),
    }
    (args.output_dir / "transit_path_build_summary.json").write_text(
        json.dumps(build_summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(build_summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

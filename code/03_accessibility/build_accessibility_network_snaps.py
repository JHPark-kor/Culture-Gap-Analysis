#!/usr/bin/env python3
"""Snap Seoul grids, culture facilities, and GTFS stops to the walk network."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GRID = (
    PROJECT_ROOT
    / "data/processed/accessibility/network_snap/grid_walk_node_snap.csv"
)
DEFAULT_POPULATION_GRID = (
    PROJECT_ROOT
    / "data/processed/accessibility/population/grid_senior_population_score.csv"
)
DEFAULT_NODES = Path(
    "outputs/fixed_accessibility_inputs/terrain/seoul_walk_nodes_elevation.parquet"
)
DEFAULT_PERFORMANCE = Path(
    PROJECT_ROOT
    / "data/processed/accessibility/facilities/seoul_show_facilities_deduplicated.csv"
)
DEFAULT_SPORTS = Path(
    PROJECT_ROOT
    / "data/raw/spatial/accessibility/facilities/대형_스포츠관람시설_위경도.csv"
)
DEFAULT_STOPS = Path(
    "outputs/fixed_accessibility_inputs/transit/seoul_gtfs_analysis_subset/stops.txt"
)
DEFAULT_OUTPUT = Path("outputs/fixed_accessibility_inputs/network_snap")

POPULATION_COLUMN = "취약노인수"


def normalize_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value))).strip()


def stable_id(prefix: str, *values: object) -> str:
    key = "|".join(normalize_text(value) for value in values)
    return f"{prefix}_{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12].upper()}"


def snap_points(
    frame: pd.DataFrame,
    x_column: str,
    y_column: str,
    nodes: pd.DataFrame,
    tree: cKDTree,
) -> pd.DataFrame:
    coordinates = frame[[x_column, y_column]].to_numpy(dtype=float)
    distances, positions = tree.query(coordinates, k=1, workers=-1)
    nearest = nodes.iloc[np.asarray(positions, dtype=np.int64)].reset_index(drop=True)
    result = frame.reset_index(drop=True).copy()
    result["walk_node_id"] = nearest["node_id"].astype("int64")
    result["walk_snap_distance_m"] = np.asarray(distances, dtype=float)
    result["walk_node_x_epsg5179"] = nearest["x_epsg5179"].to_numpy(dtype=float)
    result["walk_node_y_epsg5179"] = nearest["y_epsg5179"].to_numpy(dtype=float)
    result["walk_node_elevation_m"] = nearest["elevation_m"].to_numpy(dtype=float)
    result["walk_snap_time_elderly_min"] = result["walk_snap_distance_m"] / 60.0
    return result


def build_facility_master(performance_path: Path, sports_path: Path) -> pd.DataFrame:
    performance = pd.read_csv(performance_path, encoding="utf-8-sig")
    performance_master = pd.DataFrame(
        {
            "facility_id": performance["facility_cluster_id"].astype(str),
            "facility_category": "공연",
            "facility_name": performance["공연시설명"].astype(str),
            "address": performance["주소_원문"].astype(str),
            "latitude": performance["위도"].astype(float),
            "longitude": performance["경도"].astype(float),
            "source_record_count": performance["원본시설수"].astype(int),
            "source_dataset": "seoul_show_facilities_deduplicated.csv",
        }
    )

    sports = pd.read_csv(sports_path, encoding="utf-8-sig")
    sports_ids = [
        stable_id("SFCL", name, address, latitude, longitude)
        for name, address, latitude, longitude in sports[
            ["시설명", "주소_원본", "위도", "경도"]
        ].itertuples(index=False, name=None)
    ]
    sports_master = pd.DataFrame(
        {
            "facility_id": sports_ids,
            "facility_category": "스포츠관람",
            "facility_name": sports["시설명"].astype(str),
            "address": sports["주소_원본"].astype(str),
            "latitude": sports["위도"].astype(float),
            "longitude": sports["경도"].astype(float),
            "source_record_count": 1,
            "source_dataset": "대형_스포츠관람시설_위경도.csv",
        }
    )
    master = pd.concat([performance_master, sports_master], ignore_index=True)
    if master["facility_id"].duplicated().any():
        raise ValueError("facility_id is not unique")
    if master[["latitude", "longitude"]].isna().any().any():
        raise ValueError("Facility coordinates contain missing values")
    return master


def distance_summary(values: pd.Series) -> dict[str, float | int]:
    numeric = values.astype(float).to_numpy()
    return {
        "count": int(len(numeric)),
        "median_m": float(np.median(numeric)),
        "p95_m": float(np.percentile(numeric, 95)),
        "p99_m": float(np.percentile(numeric, 99)),
        "max_m": float(np.max(numeric)),
        "count_gt_100m": int(np.sum(numeric > 100)),
        "count_gt_250m": int(np.sum(numeric > 250)),
        "count_gt_500m": int(np.sum(numeric > 500)),
        "count_gt_1000m": int(np.sum(numeric > 1000)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--grid", type=Path, default=DEFAULT_GRID)
    parser.add_argument(
        "--population-grid", type=Path, default=DEFAULT_POPULATION_GRID
    )
    parser.add_argument("--nodes", type=Path, default=DEFAULT_NODES)
    parser.add_argument("--performance", type=Path, default=DEFAULT_PERFORMANCE)
    parser.add_argument("--sports", type=Path, default=DEFAULT_SPORTS)
    parser.add_argument("--stops", type=Path, default=DEFAULT_STOPS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    nodes = pd.read_parquet(args.nodes).sort_values("node_id").reset_index(drop=True)
    required_node_columns = {
        "node_id",
        "x_epsg5179",
        "y_epsg5179",
        "elevation_m",
    }
    missing_node_columns = required_node_columns.difference(nodes.columns)
    if missing_node_columns:
        raise ValueError(f"Missing node columns: {sorted(missing_node_columns)}")
    tree = cKDTree(nodes[["x_epsg5179", "y_epsg5179"]].to_numpy(dtype=float))

    grid_source = pd.read_csv(
        args.grid,
        encoding="utf-8-sig",
        usecols=[
            "GRID_CD",
            "행정동코드",
            "시군구",
            "행정동",
            "중심점_x",
            "중심점_y",
        ],
        dtype={"GRID_CD": "string", "행정동코드": "string"},
    )
    population = pd.read_csv(
        args.population_grid,
        encoding="utf-8-sig",
        usecols=["GRID_CD", POPULATION_COLUMN],
        dtype={"GRID_CD": "string"},
    )
    if grid_source["GRID_CD"].duplicated().any():
        raise ValueError("GRID_CD is not unique in the coordinate grid")
    if population["GRID_CD"].duplicated().any():
        raise ValueError("GRID_CD is not unique in the population grid")
    population[POPULATION_COLUMN] = pd.to_numeric(
        population[POPULATION_COLUMN], errors="raise"
    )
    source_grid_rows = len(grid_source)
    grid_source = grid_source.merge(
        population, on="GRID_CD", how="left", validate="one_to_one"
    )
    if grid_source[POPULATION_COLUMN].isna().any():
        missing = int(grid_source[POPULATION_COLUMN].isna().sum())
        raise ValueError(f"{missing} coordinate grids have no {POPULATION_COLUMN}")
    grid = grid_source.loc[grid_source[POPULATION_COLUMN] > 0].reset_index(drop=True)
    excluded_zero_population_rows = source_grid_rows - len(grid)
    if grid.empty:
        raise ValueError(f"No grids have {POPULATION_COLUMN} > 0")
    grid_snap = snap_points(grid, "중심점_x", "중심점_y", nodes, tree)
    to_wgs84 = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)
    grid_lon, grid_lat = to_wgs84.transform(
        grid_snap["중심점_x"].to_numpy(), grid_snap["중심점_y"].to_numpy()
    )
    grid_snap["center_longitude"] = grid_lon
    grid_snap["center_latitude"] = grid_lat

    facilities = build_facility_master(args.performance, args.sports)
    to_5179 = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
    facility_x, facility_y = to_5179.transform(
        facilities["longitude"].to_numpy(), facilities["latitude"].to_numpy()
    )
    facilities["x_epsg5179"] = facility_x
    facilities["y_epsg5179"] = facility_y
    facility_snap = snap_points(facilities, "x_epsg5179", "y_epsg5179", nodes, tree)

    stops = pd.read_csv(
        args.stops,
        dtype={"stop_id": "string", "stop_name": "string"},
    )
    stops["stop_lat"] = pd.to_numeric(stops["stop_lat"], errors="raise")
    stops["stop_lon"] = pd.to_numeric(stops["stop_lon"], errors="raise")
    stop_x, stop_y = to_5179.transform(
        stops["stop_lon"].to_numpy(), stops["stop_lat"].to_numpy()
    )
    stops["x_epsg5179"] = stop_x
    stops["y_epsg5179"] = stop_y
    stop_snap = snap_points(stops, "x_epsg5179", "y_epsg5179", nodes, tree)
    stop_snap["within_1km_of_walk_network"] = stop_snap["walk_snap_distance_m"].le(1000)

    master_path = args.output_dir / "facility_master_available.csv"
    grid_csv_path = args.output_dir / "grid_walk_node_snap.csv"
    grid_parquet_path = args.output_dir / "grid_walk_node_snap.parquet"
    facility_csv_path = args.output_dir / "facility_walk_node_snap.csv"
    facility_parquet_path = args.output_dir / "facility_walk_node_snap.parquet"
    stop_parquet_path = args.output_dir / "transit_stop_walk_node_snap.parquet"
    summary_path = args.output_dir / "network_snap_summary.json"

    facilities.to_csv(master_path, index=False, encoding="utf-8-sig")
    grid_snap.to_csv(grid_csv_path, index=False, encoding="utf-8-sig")
    grid_snap.to_parquet(grid_parquet_path, index=False)
    facility_snap.to_csv(facility_csv_path, index=False, encoding="utf-8-sig")
    facility_snap.to_parquet(facility_parquet_path, index=False)
    stop_snap.to_parquet(stop_parquet_path, index=False)

    summary = {
        "source_grid": str(args.grid),
        "source_population_grid": str(args.population_grid),
        "source_walk_nodes": str(args.nodes),
        "walk_graph_nodes": int(len(nodes)),
        "source_grid_rows": int(source_grid_rows),
        "grid_rows": int(len(grid_snap)),
        "population_filter": f"{POPULATION_COLUMN} > 0",
        "population_estimate_provenance": (
            "provided grid_senior_population_score.csv; not re-estimated by "
            "accessibility pipeline"
        ),
        "excluded_zero_population_grid_rows": int(excluded_zero_population_rows),
        "facility_rows": int(len(facility_snap)),
        "facilities_by_category": {
            str(key): int(value)
            for key, value in facilities["facility_category"].value_counts().items()
        },
        "exhibition_facilities_status": "not_provided",
        "transit_stop_rows": int(len(stop_snap)),
        "transit_stops_within_1km_of_walk_network": int(
            stop_snap["within_1km_of_walk_network"].sum()
        ),
        "elderly_walk_speed_m_min": 60.0,
        "grid_snap": distance_summary(grid_snap["walk_snap_distance_m"]),
        "facility_snap": distance_summary(facility_snap["walk_snap_distance_m"]),
        "transit_stop_snap": distance_summary(stop_snap["walk_snap_distance_m"]),
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

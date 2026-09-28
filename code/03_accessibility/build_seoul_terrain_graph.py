"""Build a Seoul-wide walking graph with DEM elevation and slope attributes."""

from __future__ import annotations

import argparse
import json
import math
from contextlib import ExitStack
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import osmnx as ox
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.io import MemoryFile
from rasterio.merge import merge


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GRAPH = (
    PROJECT_ROOT / "data/raw/spatial/accessibility/network/seoul_walk.graphml"
)
DEFAULT_DEM_DIR = PROJECT_ROOT / "data/raw/spatial/accessibility/dem"
DEFAULT_OUTPUT_DIR = Path("outputs/fixed_accessibility_inputs/terrain")


def _open_dem_sources(paths: list[Path], stack: ExitStack):
    datasets = []
    for path in paths:
        archive = stack.enter_context(ZipFile(path))
        image_names = [name for name in archive.namelist() if name.lower().endswith(".img")]
        if len(image_names) != 1:
            raise ValueError(f"Expected one .img in {path}, found {image_names}")
        memory = stack.enter_context(MemoryFile(archive.read(image_names[0])))
        datasets.append(stack.enter_context(memory.open()))
    return datasets


def _bilinear_sample(
    raster: np.ndarray,
    transform,
    xs: np.ndarray,
    ys: np.ndarray,
) -> np.ndarray:
    """Sample a north-up raster at point coordinates using bilinear interpolation."""

    cols = (xs - transform.c) / transform.a - 0.5
    rows = (ys - transform.f) / transform.e - 0.5
    c0 = np.floor(cols).astype(np.int64)
    r0 = np.floor(rows).astype(np.int64)
    c1 = c0 + 1
    r1 = r0 + 1
    output = np.full(xs.shape, np.nan, dtype=np.float64)

    valid = (
        (r0 >= 0)
        & (c0 >= 0)
        & (r1 < raster.shape[0])
        & (c1 < raster.shape[1])
    )
    if valid.any():
        rv0, rv1 = r0[valid], r1[valid]
        cv0, cv1 = c0[valid], c1[valid]
        q00 = raster[rv0, cv0]
        q10 = raster[rv0, cv1]
        q01 = raster[rv1, cv0]
        q11 = raster[rv1, cv1]
        dx = cols[valid] - cv0
        dy = rows[valid] - rv0
        values = (
            q00 * (1 - dx) * (1 - dy)
            + q10 * dx * (1 - dy)
            + q01 * (1 - dx) * dy
            + q11 * dx * dy
        )
        finite = np.isfinite(q00) & np.isfinite(q10) & np.isfinite(q01) & np.isfinite(q11)
        selected = np.flatnonzero(valid)
        output[selected[finite]] = values[finite]

    # Nearest-neighbour fallback for DEM edge cells or isolated missing neighbours.
    missing = ~np.isfinite(output)
    if missing.any():
        cn = np.rint(cols[missing]).astype(np.int64)
        rn = np.rint(rows[missing]).astype(np.int64)
        inside = (
            (rn >= 0)
            & (cn >= 0)
            & (rn < raster.shape[0])
            & (cn < raster.shape[1])
        )
        selected = np.flatnonzero(missing)
        output[selected[inside]] = raster[rn[inside], cn[inside]]
    return output


def build_terrain_graph(graph_path: Path, dem_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    dem_paths = sorted(dem_dir.glob("seoul_dem_*_2025.zip"))
    if len(dem_paths) != 4:
        raise FileNotFoundError(f"Expected four Seoul DEM ZIPs in {dem_dir}, found {len(dem_paths)}")

    print(f"Loading walking graph: {graph_path}", flush=True)
    graph = ox.load_graphml(graph_path)
    print(f"Loaded {len(graph.nodes):,} nodes and {len(graph.edges):,} edges", flush=True)

    with ExitStack() as stack:
        datasets = _open_dem_sources(dem_paths, stack)
        source_crs = datasets[0].crs
        for dataset in datasets[1:]:
            if dataset.crs != source_crs:
                raise ValueError("DEM tiles do not share a CRS")
        mosaic_masked, mosaic_transform = merge(datasets, masked=True)
        mosaic = mosaic_masked[0].filled(np.nan).astype(np.float32)

    mosaic_path = output_dir / "seoul_dem_4tiles_2025_epsg5179.tif"
    profile = {
        "driver": "GTiff",
        "height": mosaic.shape[0],
        "width": mosaic.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": source_crs,
        "transform": mosaic_transform,
        "nodata": np.nan,
        "compress": "deflate",
        "tiled": True,
    }
    with rasterio.open(mosaic_path, "w", **profile) as target:
        target.write(mosaic, 1)
    print(f"Wrote DEM mosaic: {mosaic_path}", flush=True)

    node_ids = list(graph.nodes)
    longitudes = np.fromiter((float(graph.nodes[n]["x"]) for n in node_ids), dtype=float)
    latitudes = np.fromiter((float(graph.nodes[n]["y"]) for n in node_ids), dtype=float)
    transformer = Transformer.from_crs(graph.graph["crs"], source_crs, always_xy=True)
    xs, ys = transformer.transform(longitudes, latitudes)
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    elevations = _bilinear_sample(mosaic, mosaic_transform, xs, ys)

    for node_id, elevation in zip(node_ids, elevations):
        if np.isfinite(elevation):
            graph.nodes[node_id]["elevation"] = float(elevation)

    missing_node_elevation = int((~np.isfinite(elevations)).sum())
    print(f"Node elevations assigned; missing={missing_node_elevation:,}", flush=True)

    edge_rows: list[tuple[object, object, object, float, float, float, float, float, float, int]] = []
    missing_edge_slope = 0
    for u, v, key, data in graph.edges(keys=True, data=True):
        length_m = float(data.get("length", math.nan))
        elevation_u = graph.nodes[u].get("elevation")
        elevation_v = graph.nodes[v].get("elevation")
        if (
            elevation_u is None
            or elevation_v is None
            or not math.isfinite(length_m)
            or length_m <= 0
        ):
            missing_edge_slope += 1
            continue
        elevation_change_m = float(elevation_v) - float(elevation_u)
        grade = elevation_change_m / length_m
        slope_deg = math.degrees(math.atan(grade))
        slope_abs_deg = abs(slope_deg)
        elderly_walk_time_min = length_m / 60.0
        data["elevation_change_m"] = elevation_change_m
        data["grade"] = grade
        data["grade_abs"] = abs(grade)
        data["slope_deg"] = slope_deg
        data["slope_abs_deg"] = slope_abs_deg
        data["elderly_walk_time_min"] = elderly_walk_time_min
        data["steep_ge_8deg"] = int(slope_abs_deg >= 8.0)
        edge_rows.append(
            (
                u,
                v,
                key,
                length_m,
                elevation_change_m,
                grade,
                slope_deg,
                slope_abs_deg,
                elderly_walk_time_min,
                int(slope_abs_deg >= 8.0),
            )
        )

    graph.graph["dem_source"] = ";".join(path.name for path in dem_paths)
    graph.graph["dem_resolution_m"] = 90.0
    graph.graph["dem_crs"] = str(source_crs)
    graph.graph["elderly_walk_speed_m_min"] = 60.0
    graph.graph["slope_method"] = "atan((elevation_v-elevation_u)/edge_length_m)"

    graph_path_out = output_dir / "seoul_walk_dem4_slope_elderly.graphml"
    print(f"Saving enriched graph: {graph_path_out}", flush=True)
    ox.save_graphml(graph, graph_path_out)

    nodes = pd.DataFrame(
        {
            "node_id": node_ids,
            "longitude": longitudes,
            "latitude": latitudes,
            "x_epsg5179": xs,
            "y_epsg5179": ys,
            "elevation_m": elevations,
        }
    )
    nodes.to_parquet(output_dir / "seoul_walk_nodes_elevation.parquet", index=False)

    edge_columns = [
        "u",
        "v",
        "key",
        "length_m",
        "elevation_change_m",
        "grade",
        "slope_deg",
        "slope_abs_deg",
        "elderly_walk_time_min",
        "steep_ge_8deg",
    ]
    edges = pd.DataFrame.from_records(edge_rows, columns=edge_columns)
    edges.to_parquet(output_dir / "seoul_walk_edges_terrain.parquet", index=False)

    slope = edges["slope_abs_deg"]
    summary = {
        "source_graph": str(graph_path),
        "dem_tiles": [path.name for path in dem_paths],
        "dem_crs": str(source_crs),
        "dem_resolution_m": 90.0,
        "dem_bounds": [
            float(mosaic_transform.c),
            float(mosaic_transform.f + mosaic_transform.e * mosaic.shape[0]),
            float(mosaic_transform.c + mosaic_transform.a * mosaic.shape[1]),
            float(mosaic_transform.f),
        ],
        "graph_nodes": len(graph.nodes),
        "graph_edges": len(graph.edges),
        "nodes_with_elevation": int(np.isfinite(elevations).sum()),
        "nodes_missing_elevation": missing_node_elevation,
        "edges_with_slope": len(edges),
        "edges_missing_slope": missing_edge_slope,
        "elevation_min_m": float(np.nanmin(elevations)),
        "elevation_median_m": float(np.nanmedian(elevations)),
        "elevation_max_m": float(np.nanmax(elevations)),
        "slope_abs_median_deg": float(slope.median()),
        "slope_abs_p95_deg": float(slope.quantile(0.95)),
        "slope_abs_max_deg": float(slope.max()),
        "edge_share_ge_2deg": float(slope.ge(2.0).mean()),
        "edge_share_ge_5deg": float(slope.ge(5.0).mean()),
        "edge_share_ge_8deg": float(slope.ge(8.0).mean()),
        "elderly_walk_speed_m_min": 60.0,
    }
    (output_dir / "terrain_build_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pd.DataFrame([summary]).to_csv(
        output_dir / "terrain_build_summary.csv", index=False, encoding="utf-8-sig"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--dem-dir", type=Path, default=DEFAULT_DEM_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    build_terrain_graph(arguments.graph, arguments.dem_dir, arguments.output_dir)

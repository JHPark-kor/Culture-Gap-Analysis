#!/usr/bin/env python3
"""Calculate the fixed transit burden for one routed itinerary.

The itinerary is expressed as the ordered route-pattern IDs written by
``build_seoul_gtfs_cost_inputs.py``. Consecutive duplicate IDs are collapsed,
because remaining aboard the same pattern is not a transfer.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import pandas as pd


DEFAULT_HEADWAYS = Path(
    "outputs/fixed_accessibility_inputs/transit/route_pattern_headways_all_day.csv"
)


def collapse_consecutive(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        value = str(value).strip()
        if value and (not result or result[-1] != value):
            result.append(value)
    return result


def calculate_transit_burden(
    pattern_ids: Iterable[str], headway_by_pattern: dict[str, float]
) -> dict[str, object]:
    boarded = collapse_consecutive(pattern_ids)
    if not boarded:
        return {
            "boarded_pattern_ids": [],
            "boardings": 0,
            "transfer_count": 0,
            "expected_wait_by_boarding_min": [],
            "total_expected_wait_min": 0.0,
            "transfer_burden": 0.0,
            "wait_burden": 0.0,
            "transport_burden": 0.0,
        }

    missing = [pattern_id for pattern_id in boarded if pattern_id not in headway_by_pattern]
    if missing:
        raise KeyError("Unknown route-pattern IDs: " + ", ".join(missing))

    waits = [float(headway_by_pattern[pattern_id]) / 2.0 for pattern_id in boarded]
    transfer_count = max(len(boarded) - 1, 0)
    total_wait = float(sum(waits))
    transfer_burden = min(transfer_count / 3.0, 1.0)
    wait_burden = min(total_wait / 20.0, 1.0)
    transport_burden = 0.5 * transfer_burden + 0.5 * wait_burden

    return {
        "boarded_pattern_ids": boarded,
        "boardings": len(boarded),
        "transfer_count": transfer_count,
        "expected_wait_by_boarding_min": waits,
        "total_expected_wait_min": total_wait,
        "transfer_burden": transfer_burden,
        "wait_burden": wait_burden,
        "transport_burden": transport_burden,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calculate transfer/wait burden for an ordered transit itinerary."
    )
    parser.add_argument(
        "--patterns",
        nargs="*",
        default=[],
        help="Ordered route-pattern IDs. Empty means a walk-only itinerary.",
    )
    parser.add_argument("--headways", type=Path, default=DEFAULT_HEADWAYS)
    parser.add_argument("--output", type=Path, help="Optional JSON output path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    headways = pd.read_csv(
        args.headways,
        usecols=["pattern_id", "median_headway_min"],
        dtype={"pattern_id": "string"},
    )
    headway_by_pattern = dict(
        zip(headways["pattern_id"].astype(str), headways["median_headway_min"].astype(float))
    )
    result = calculate_transit_burden(args.patterns, headway_by_pattern)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()

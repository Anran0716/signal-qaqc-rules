"""Nearest-road v1 baseline, retained to demonstrate its intersection failures.

All geometries use local EPSG:26914 meters. A second road less than 10 m farther
away makes an otherwise automatic match ambiguous. This heuristic flags genuine
crossings and can accept points far from their claimed crossing along one road.
The next version must check the named intersection rather than only a road.
"""

from dataclasses import dataclass
from math import inf, isnan

from shapely.geometry import LineString, MultiLineString, Point

AUTO_MAX_M = 15.0
FLAG_MAX_M = 50.0
AMBIGUITY_GAP_M = 10.0


@dataclass(frozen=True)
class RoadMatch:
    """Nearest candidate and evidence; only tier auto permits association."""

    road_id: str | None
    distance_m: float
    second_distance_m: float
    tier: str


def classify_distance(distance_m: float) -> str:
    """Shared distance-only tiers: <=15 m auto, <=50 m flag, otherwise manual."""
    if isnan(distance_m) or distance_m < 0:
        raise ValueError("distance must be nonnegative and not NaN")
    if distance_m <= AUTO_MAX_M:
        return "auto"
    return "flag" if distance_m <= FLAG_MAX_M else "manual"


def _nearest_tier(distance_m: float, second_distance_m: float) -> str:
    tier = classify_distance(distance_m)
    if tier == "auto" and second_distance_m - distance_m < AMBIGUITY_GAP_M:
        return "flag"
    return tier


def nearest_v1(
    point: Point, roads: dict[str, LineString | MultiLineString]
) -> RoadMatch:
    """Tier the nearest road, without checking which intersection the name claims.

    The road_id is a candidate even for flag/manual results. An empty road set
    returns manual; invalid geometries raise ValueError rather than being ignored.
    """
    if not isinstance(point, Point) or point.is_empty or not point.is_valid:
        raise ValueError("Signal point must be non-empty and have finite coordinates")
    if not roads:
        return RoadMatch(None, inf, inf, "manual")
    distances = []
    for road_id, geometry in roads.items():
        if (
            not isinstance(geometry, (LineString, MultiLineString))
            or geometry.is_empty
            or not geometry.is_valid
        ):
            raise ValueError(f"Invalid road geometry for {road_id!r}")
        distances.append((road_id, point.distance(geometry)))
    distances.sort(key=lambda candidate: candidate[1])
    nearest_id, nearest_distance = distances[0]
    second_distance = distances[1][1] if len(distances) > 1 else inf
    return RoadMatch(
        nearest_id,
        nearest_distance,
        second_distance,
        _nearest_tier(nearest_distance, second_distance),
    )

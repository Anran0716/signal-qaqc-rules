"""Compare the nearest-road baseline with named-intersection association.

All geometries use local EPSG:26914 meters. V1 retains its known ambiguity and
wrong-crossing failures. Associate measures the nearest point on the named roads'
actual intersection, including multiple crossings and overlapping line segments.
Candidate pairs are tried in route/alias priority order, skipping identical keys.
Unverified fallbacks are capped at flag and marked fallback_v1. NOT_INTERSECTION
records use nearest_v1 directly because their location is defined along a road.
Build the road index once with build_road_index before processing signal records.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from itertools import product
from math import inf, isnan

from shapely.geometry import LineString, MultiLineString, Point
from shapely.ops import nearest_points, unary_union

from signal_qaqc.naming import (
    check_intersection_name,
    make_key,
    normalize_road,
    parse_side,
)

AUTO_MAX_M = 15.0
FLAG_MAX_M = 50.0
APPROACH_MAX_M = 60.0
AMBIGUITY_GAP_M = 10.0


@dataclass(frozen=True)
class RoadMatch:
    """Candidate evidence; only auto permits association.

    road_id holds a road key for v1 or an intersection key for named_crossing.
    method is named_crossing, fallback_v1, or nearest_v1 for direct road checks.
    """

    road_id: str | None
    distance_m: float
    second_distance_m: float
    tier: str
    method: str = "nearest_v1"


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


def _validate_point(point):
    if not isinstance(point, Point) or point.is_empty or not point.is_valid:
        raise ValueError("Signal point must be non-empty and have finite coordinates")


def _validate_road(geometry, road_id):
    if (
        not isinstance(geometry, (LineString, MultiLineString))
        or geometry.is_empty
        or not geometry.is_valid
    ):
        raise ValueError(f"Invalid road geometry for {road_id!r}")


def nearest_v1(
    point: Point, roads: dict[str, LineString | MultiLineString]
) -> RoadMatch:
    """Tier the nearest road, without checking which intersection the name claims.

    The road_id is a candidate even for flag/manual results. An empty road set
    returns manual; invalid geometries raise ValueError rather than being ignored.
    """
    _validate_point(point)
    if not roads:
        return RoadMatch(None, inf, inf, "manual")
    distances = []
    for road_id, geometry in roads.items():
        _validate_road(geometry, road_id)
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


def build_road_index(roads) -> dict[str, LineString | MultiLineString]:
    """Index a mapping or iterable of (name, geometry) pairs by canonical name.

    Normalize each road token before make_key, so I-10 and IH 10 share a key.
    Repeated feature names are merged instead of overwriting earlier segments.
    """
    items = roads.items() if isinstance(roads, Mapping) else roads
    segments = {}
    for name, geometry in items:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("A non-empty road name is required")
        _validate_road(geometry, name)
        key = make_key(normalize_road(name))
        segments.setdefault(key, []).append(geometry)
    return {
        key: pieces[0] if len(pieces) == 1 else unary_union(pieces)
        for key, pieces in segments.items()
    }


def crossing_point(
    road_a: LineString | MultiLineString,
    road_b: LineString | MultiLineString,
    point: Point,
) -> Point | None:
    """Return the nearest point on the actual intersection, never its centroid."""
    _validate_point(point)
    _validate_road(road_a, "first")
    _validate_road(road_b, "second")
    intersection = road_a.intersection(road_b)
    if intersection.is_empty:
        return None
    return nearest_points(point, intersection)[1]


def _side_keys(text) -> list[str]:
    """List the ranked route first, then each alias, preserving distinct qualifiers."""
    side = parse_side(text)
    if side.reason:
        return []
    keys = []
    for name in (side.route, *side.text.split("/")):
        if name is not None:
            key = make_key(normalize_road(name))
            if key not in keys:
                keys.append(key)
    return keys


def _fallback_v1(point, road_index) -> RoadMatch:
    """Retain nearest-road evidence without automatically passing an unverified name."""
    result = nearest_v1(point, road_index)
    return replace(
        result,
        tier="flag" if result.tier == "auto" else result.tier,
        method="fallback_v1",
    )


def associate(
    point: Point,
    location_std: str | None,
    road_index: dict[str, LineString | MultiLineString],
) -> RoadMatch:
    """Check the named crossing using a road index from build_road_index.

    Try each side's route and aliases in priority order. Return the first pair
    of distinct keys that intersects; exhaust available pairs before manual.
    Missing/unparseable names or unavailable roads use fallback_v1, capped at flag.
    NOT_INTERSECTION records use nearest_v1 directly and may retain auto.
    MultiPoint and line intersections use their actual nearest point to the signal.
    """
    _validate_point(point)
    name = check_intersection_name(location_std)
    if name.status == "NOT_INTERSECTION":
        return nearest_v1(point, road_index)
    if name.proposed is None:
        return _fallback_v1(point, road_index)
    sides = name.proposed.split(" @ ")
    candidates = [
        [key for key in _side_keys(side) if key in road_index] for side in sides
    ]
    if len(candidates) != 2 or any(not keys for keys in candidates):
        return _fallback_v1(point, road_index)
    for first, second in product(*candidates):
        if first == second:
            continue
        crossing = crossing_point(road_index[first], road_index[second], point)
        if crossing is None:
            continue
        distance = point.distance(crossing)
        return RoadMatch(
            make_key(name.proposed),
            distance,
            inf,
            classify_distance(distance),
            method="named_crossing",
        )
    return RoadMatch(None, inf, inf, "manual", method="named_crossing")

from math import inf

import pytest
from shapely.geometry import LineString, MultiLineString, Point

from signal_qaqc.spatial import (
    AUTO_MAX_M,
    FLAG_MAX_M,
    _nearest_tier,
    classify_distance,
    nearest_v1,
)


@pytest.fixture
def roads():
    # Fictional road network translated to UTM-scale coordinates.
    x, y = 500_000.0, 3_300_000.0
    return {
        "Fictional Main": LineString([(x, y), (x + 1000, y)]),
        "Fictional Cross": LineString([(x + 500, y - 500), (x + 500, y + 500)]),
        "Fictional Parallel": LineString([(x, y + 100), (x + 1000, y + 100)]),
    }


@pytest.mark.parametrize(
    ("distance", "expected"),
    [
        (0.0, "auto"),
        (15.0, "auto"),
        (15.1, "flag"),
        (50.0, "flag"),
        (50.1, "manual"),
        (inf, "manual"),
    ],
)
def test_distance_tiers_have_exact_numeric_boundaries(distance, expected):
    assert classify_distance(distance) == expected


def test_distance_limits_match_the_shared_project_thresholds():
    assert AUTO_MAX_M == 15.0
    assert FLAG_MAX_M == 50.0


@pytest.mark.parametrize("distance", [-0.1, float("nan")])
def test_distance_tiering_rejects_invalid_distances(distance):
    with pytest.raises(ValueError, match="distance"):
        classify_distance(distance)


@pytest.mark.parametrize(
    ("nearest", "second", "expected"),
    [
        (5.0, 14.9, "flag"),
        (5.0, 15.0, "auto"),
        (5.0, 15.1, "auto"),
        (5.0, inf, "auto"),
        (20.0, 21.0, "flag"),
        (60.0, 62.0, "manual"),
    ],
)
def test_nearest_v1_ambiguity_gap_is_tested_without_geometry(nearest, second, expected):
    assert _nearest_tier(nearest, second) == expected


@pytest.mark.parametrize(
    ("dy", "road_id", "distance", "second", "tier"),
    [
        (5.0, "Fictional Main", 5.0, 95.0, "auto"),
        (30.0, "Fictional Main", 30.0, 70.0, "flag"),
        (250.0, "Fictional Parallel", 150.0, 250.0, "manual"),
    ],
)
def test_nearest_v1_measures_road_distances_in_meters(
    roads, dy, road_id, distance, second, tier
):
    result = nearest_v1(Point(500_100.0, 3_300_000.0 + dy), roads)

    assert result.road_id == road_id
    assert result.distance_m == pytest.approx(distance)
    assert result.second_distance_m == pytest.approx(second)
    assert result.tier == tier


def test_no_roads_requires_manual_review():
    result = nearest_v1(Point(500_100.0, 3_300_005.0), {})

    assert result.road_id is None
    assert result.distance_m == result.second_distance_m == inf
    assert result.tier == "manual"


def test_single_road_has_an_infinite_second_distance(roads):
    result = nearest_v1(
        Point(500_100.0, 3_300_005.0), {"Fictional Main": roads["Fictional Main"]}
    )

    assert result.tier == "auto"
    assert result.second_distance_m == inf


def test_multiline_roads_are_supported():
    road = MultiLineString(
        [
            [(500_000.0, 3_300_000.0), (500_200.0, 3_300_000.0)],
            [(500_300.0, 3_300_000.0), (500_500.0, 3_300_000.0)],
        ]
    )

    result = nearest_v1(Point(500_100.0, 3_300_005.0), {"Fictional Road": road})

    assert result.distance_m == pytest.approx(5.0)
    assert result.tier == "auto"


@pytest.mark.parametrize("point", [Point(), Point(inf, 3_300_000.0), None])
def test_invalid_signal_points_are_rejected(point):
    with pytest.raises(ValueError, match="point"):
        nearest_v1(point, {})


@pytest.mark.parametrize("road", [None, LineString(), Point(500_000.0, 3_300_000.0)])
def test_invalid_road_geometries_are_rejected(road):
    with pytest.raises(ValueError, match="road"):
        nearest_v1(Point(500_100.0, 3_300_005.0), {"Fictional Road": road})


def test_nearest_v1_exposes_why_a_true_intersection_is_flagged(roads):
    point = Point(500_500.0, 3_300_000.0)

    result = nearest_v1(point, roads)

    assert result.distance_m == result.second_distance_m == 0.0
    assert result.tier == "flag"


def test_nearest_v1_exposes_why_a_wrong_intersection_is_accepted(roads):
    point = Point(500_700.0, 3_300_008.0)
    claimed_crossing = roads["Fictional Main"].intersection(roads["Fictional Cross"])

    result = nearest_v1(point, roads)

    assert point.distance(claimed_crossing) > FLAG_MAX_M
    assert result.distance_m == pytest.approx(8.0)
    assert result.second_distance_m == pytest.approx(92.0)
    assert result.tier == "auto"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Known v1 flaw: both roads meet at a valid crossing, so the gap is zero",
)
def test_known_v1_failure_true_intersection_should_be_automatic(roads):
    point = Point(500_500.0, 3_300_000.0)
    assert roads["Fictional Main"].intersection(roads["Fictional Cross"]).equals(point)

    result = nearest_v1(point, roads)

    assert result.tier == "auto"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Known v1 flaw: nearest-road distance does not check the claimed crossing",
)
def test_known_v1_failure_point_far_from_claimed_intersection_should_be_manual(roads):
    point = Point(500_700.0, 3_300_008.0)
    claimed_crossing = roads["Fictional Main"].intersection(roads["Fictional Cross"])
    assert point.distance(claimed_crossing) > FLAG_MAX_M

    result = nearest_v1(point, roads)

    assert result.distance_m == pytest.approx(8.0)
    assert result.tier == "manual"

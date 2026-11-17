from math import inf

import pytest
from shapely.geometry import LineString, MultiLineString, Point

from signal_qaqc.naming import make_key
from signal_qaqc.spatial import (
    AUTO_MAX_M,
    FLAG_MAX_M,
    GAP_MAX_M,
    _gap_tier,
    _nearest_tier,
    _side_keys,
    associate,
    build_road_index,
    classify_distance,
    crossing_point,
    nearest_v1,
)


@pytest.fixture
def roads():
    # Fictional road network translated to UTM-scale coordinates.
    x, y = 500_000.0, 3_300_000.0
    return build_road_index(
        {
            "IH 10": LineString([(x, y), (x + 1000, y)]),
            "FM 1560": LineString([(x + 500, y - 500), (x + 500, y + 500)]),
            "US 90": LineString([(x, y + 100), (x + 1000, y + 100)]),
        }
    )


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
        (5.0, "IH10", 5.0, 95.0, "auto"),
        (30.0, "IH10", 30.0, 70.0, "flag"),
        (250.0, "US90", 150.0, 250.0, "manual"),
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
    result = nearest_v1(Point(500_100.0, 3_300_005.0), {"IH10": roads["IH10"]})

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
    claimed_crossing = roads["IH10"].intersection(roads["FM1560"])

    result = nearest_v1(point, roads)

    assert point.distance(claimed_crossing) > FLAG_MAX_M
    assert result.distance_m == pytest.approx(8.0)
    assert result.second_distance_m == pytest.approx(92.0)
    assert result.tier == "auto"


def v1_tier(point, location_std, roads):
    return nearest_v1(point, roads).tier


def v2_tier(point, location_std, roads):
    return associate(point, location_std, roads).tier


@pytest.mark.parametrize(
    "locate",
    [
        pytest.param(
            v1_tier,
            id="nearest_v1",
            marks=pytest.mark.xfail(
                strict=True,
                raises=AssertionError,
                reason="Known v1 flaw: nearest road does not validate a crossing",
            ),
        ),
        pytest.param(v2_tier, id="associate_v2"),
    ],
)
@pytest.mark.parametrize(
    ("xy", "expected"),
    [
        pytest.param((500_500.0, 3_300_000.0), "auto", id="true_crossing"),
        pytest.param(
            (500_570.0, 3_300_000.0), "manual", id="seventy_meters_along_road"
        ),
        pytest.param((500_700.0, 3_300_008.0), "manual", id="wrong_location_near_road"),
    ],
)
def test_known_v1_failure_scenarios_compare_both_versions(roads, locate, xy, expected):
    assert locate(Point(*xy), "I-10 at FM1560", roads) == expected


def test_road_index_normalizes_keys_and_merges_repeated_feature_names():
    pieces = [
        ("I-10", LineString([(500_000, 3_300_000), (500_500, 3_300_000)])),
        ("IH 10", LineString([(500_500, 3_300_000), (501_000, 3_300_000)])),
    ]

    index = build_road_index(pieces)

    assert list(index) == ["IH10"]
    assert index["IH10"].covers(Point(500_100, 3_300_000))
    assert index["IH10"].covers(Point(500_900, 3_300_000))
    assert index["IH10"].length == pytest.approx(1000.0)


@pytest.mark.parametrize(
    "name", ["I-10 at FM1560", "FM 1560 @ IH10", "IH 10 @ FM 1560"]
)
def test_associate_resolves_spelling_order_and_separator_variants(roads, name):
    result = associate(Point(500_503, 3_300_004), name, roads)

    assert result.tier == "auto"
    assert result.distance_m == pytest.approx(5.0)
    assert result.road_id == make_key("IH 10 @ FM 1560")
    assert result.method == "named_crossing"


@pytest.mark.parametrize(
    ("offset", "expected"), [(5, "auto"), (30, "flag"), (70, "manual")]
)
def test_associate_uses_the_shared_distance_tiers(roads, offset, expected):
    result = associate(Point(500_500 + offset, 3_300_000), "IH 10 @ FM 1560", roads)

    assert result.distance_m == pytest.approx(offset)
    assert result.tier == expected


def test_widely_separated_known_roads_require_manual_review_without_v1_fallback(roads):
    result = associate(Point(500_100, 3_300_005), "IH 10 @ US 90", roads)

    assert result.tier == "manual"
    assert result.distance_m == inf
    assert result.method == "road_gap"
    assert result.gap_m == pytest.approx(100)
    assert result.road_keys == ("IH10", "US90")
    assert result.reason == "named_roads_far_apart"


@pytest.mark.parametrize(
    "name",
    [
        "IH 10 @ FM 999",
        "UA 100 @ FM 200",
        "(NOT IN USE) IH 10 @ FM 1560",
        "IH 10 @ FICTIONAL TURNAROUND",
        None,
    ],
)
def test_unverified_crossing_fallback_cannot_automatically_pass(roads, name):
    point = Point(500_100, 3_300_005)

    original = nearest_v1(point, roads)
    result = associate(point, name, roads)

    assert original.tier == "auto"
    assert original.method == "nearest_v1"
    assert result.tier == "flag"
    assert result.method == "fallback_v1"
    assert result.road_id == original.road_id
    assert result.distance_m == original.distance_m
    assert result.second_distance_m == original.second_distance_m


@pytest.mark.parametrize(("offset", "tier"), [(30.0, "flag"), (250.0, "manual")])
def test_unverified_fallback_preserves_existing_flag_and_manual_tiers(
    roads, offset, tier
):
    result = associate(Point(500_100, 3_300_000 + offset), "IH 10 @ FM 999", roads)

    assert result.tier == tier
    assert result.method == "fallback_v1"


@pytest.mark.parametrize("name", ["1220 FM 100", "TURNAROUND"])
@pytest.mark.parametrize(
    ("offset", "tier"), [(5.0, "auto"), (30.0, "flag"), (250.0, "manual")]
)
def test_non_intersection_records_keep_the_direct_nearest_road_result(
    roads, name, offset, tier
):
    point = Point(500_100, 3_300_000 + offset)

    result = associate(point, name, roads)

    assert result == nearest_v1(point, roads)
    assert result.tier == tier
    assert result.method == "nearest_v1"


def test_associate_with_no_road_layer_is_manual():
    result = associate(Point(500_500, 3_300_000), "IH 10 @ FM 1560", {})

    assert result.tier == "manual"
    assert result.road_id is None
    assert result.method == "fallback_v1"


def test_alias_sides_use_their_ranked_route_for_lookup(roads):
    result = associate(
        Point(500_503, 3_300_004), "IH10 / Fictional Main @ FM1560", roads
    )

    assert result.tier == "auto"
    assert result.distance_m == pytest.approx(5.0)


@pytest.mark.parametrize(
    ("side", "keys"),
    [
        ("OAK RD / FM100", ["FM100", "OAKRD"]),
        ("FM100 / FM 100 / OAK RD", ["FM100", "OAKRD"]),
        ("LP 200 NORTH", ["SL200", "SL200NORTH"]),
        ("LP 200 ACCESS RD", ["SL200", "SL200ACCESSRD"]),
    ],
)
def test_side_candidates_keep_route_priority_aliases_and_distinct_qualifiers(
    side, keys
):
    assert _side_keys(side) == keys


@pytest.mark.parametrize("include_route", [True, False])
def test_shared_route_uses_the_intersecting_local_aliases(include_route):
    x, y = 500_000.0, 3_300_000.0
    roads = {
        "OAK RD": LineString([(x - 100, y), (x + 100, y)]),
        "ELM RD": LineString([(x, y - 100), (x, y + 100)]),
    }
    if include_route:
        roads["FM 100"] = LineString([(x, y + 500), (x + 100, y + 500)])

    result = associate(
        Point(x, y), "FM 100 / OAK RD @ FM 100 / ELM RD", build_road_index(roads)
    )

    assert result.tier == "auto"
    assert result.distance_m == pytest.approx(0.0)
    assert result.method == "named_crossing"


@pytest.mark.parametrize("direction", ["NORTH", "SOUTH"])
def test_shared_loop_route_tries_mainline_and_access_road_qualifiers(direction):
    x, y = 500_000.0, 3_300_000.0
    index = build_road_index(
        {
            "LP 200": LineString([(x, y + 500), (x + 100, y + 500)]),
            f"LP 200 {direction}": LineString([(x, y - 100), (x, y + 100)]),
            "LP 200 ACCESS RD": LineString([(x - 100, y), (x + 100, y)]),
        }
    )

    result = associate(Point(x, y), f"LP 200 {direction} @ LP 200 ACCESS RD", index)

    assert result.tier == "auto"
    assert result.distance_m == pytest.approx(0.0)
    assert result.method == "named_crossing"


def test_all_alias_pairs_are_compared_before_reporting_the_smallest_road_gap():
    x, y = 500_000.0, 3_300_000.0
    index = build_road_index(
        {
            "FM 100": LineString([(x - 100, y + 500), (x + 100, y + 500)]),
            "OAK RD": LineString([(x - 100, y), (x + 100, y)]),
            "ELM RD": LineString([(x - 100, y + 20), (x + 100, y + 20)]),
        }
    )

    result = associate(Point(x, y), "FM 100 / OAK RD @ FM 100 / ELM RD", index)

    assert result.tier == "flag"
    assert result.method == "road_gap"
    assert result.distance_m == inf
    assert result.gap_m == pytest.approx(20)
    assert result.road_keys == ("OAKRD", "ELMRD")


def test_nearest_alias_pair_wins_over_a_farther_route_pair():
    x, y = 500_000.0, 3_300_000.0
    index = build_road_index(
        {
            "IH 100": LineString([(x + 900, y + 1000), (x + 1100, y + 1000)]),
            "FM 200": LineString([(x + 1000, y + 900), (x + 1000, y + 1100)]),
            "OAK RD": LineString([(x - 100, y), (x + 100, y)]),
            "ELM RD": LineString([(x, y - 100), (x, y + 100)]),
        }
    )

    result = associate(Point(x, y), "IH 100 / OAK RD @ FM 200 / ELM RD", index)

    assert result.distance_m == pytest.approx(0.0)
    assert result.tier == "auto"
    assert result.method == "named_crossing"
    assert result.road_keys == ("OAKRD", "ELMRD")


def test_repeated_street_name_keeps_only_its_local_piece():
    x, y = 500_000.0, 3_300_000.0
    index = build_road_index(
        [
            ("Fictional Oak Rd", LineString([(x, y - 100), (x, y + 100)])),
            (
                "Fictional Oak Rd",
                LineString([(x + 20_000, y - 100), (x + 20_000, y + 100)]),
            ),
            ("FM 100", LineString([(x - 100, y), (x + 20_100, y)])),
        ]
    )

    result = associate(Point(x, y), "FM 100 @ Fictional Oak Rd", index)

    assert result.tier == "auto"
    assert result.distance_m == pytest.approx(0)
    assert result.road_keys == ("FM100", "FICTIONALOAKRD")


def test_named_roads_outside_search_radius_do_not_supply_a_remote_crossing():
    x, y = 500_000.0, 3_300_000.0
    index = build_road_index(
        {
            "IH 100": LineString([(x + 20_000, y), (x + 20_200, y)]),
            "FM 200": LineString([(x + 20_100, y - 100), (x + 20_100, y + 100)]),
        }
    )

    result = associate(Point(x, y), "IH 100 @ FM 200", index)

    assert result.tier == "manual"
    assert result.method == "fallback_v1"
    assert result.distance_m == pytest.approx(20_000)
    assert result.road_keys is None
    assert result.reason == "named_road_outside_search_radius"
    assert result.gap_m is None


@pytest.mark.parametrize(
    ("offset", "finite"), [(-0.1, True), (0.0, True), (0.1, False)]
)
def test_search_radius_includes_boundary_contacts(offset, finite):
    x, y, radius = 500_000.0, 3_300_000.0, 2_000.0
    crossing_x = x + radius + offset
    index = build_road_index(
        {
            "IH 100": LineString([(crossing_x - 20, y), (crossing_x + 20, y)]),
            "FM 200": LineString([(crossing_x, y - 20), (crossing_x, y + 20)]),
        }
    )

    result = associate(Point(x, y), "IH 100 @ FM 200", index)

    if finite:
        assert result.distance_m == pytest.approx(radius + offset)
        assert result.road_keys == ("IH100", "FM200")
    else:
        assert result.method == "fallback_v1"
        assert result.road_keys is None
        assert result.gap_m is None
        assert result.reason == "named_road_outside_search_radius"


def test_equal_distance_candidates_keep_route_priority_for_a_stable_tie():
    x, y = 500_000.0, 3_300_000.0
    index = build_road_index(
        {
            "IH 100": LineString([(x - 100, y), (x + 100, y)]),
            "FM 200": LineString([(x, y - 100), (x, y + 100)]),
            "OAK RD": LineString([(x - 100, y), (x + 100, y)]),
            "ELM RD": LineString([(x, y - 100), (x, y + 100)]),
        }
    )

    result = associate(Point(x, y), "IH 100 / OAK RD @ FM 200 / ELM RD", index)

    assert result.road_keys == ("IH100", "FM200")


def test_two_crossings_choose_the_actual_nearest_point_instead_of_the_centroid():
    x, y = 500_000.0, 3_300_000.0
    loop = LineString(
        [
            (x - 100, y - 100),
            (x + 100, y - 100),
            (x + 100, y + 100),
            (x - 100, y + 100),
            (x - 100, y - 100),
        ]
    )
    road = LineString([(x - 200, y), (x + 200, y)])
    point = Point(x + 98, y + 3)
    index = build_road_index({"LP 200": loop, "FM 100": road})
    assert loop.intersection(road).geom_type == "MultiPoint"
    assert point.distance(loop.intersection(road).centroid) > FLAG_MAX_M

    crossing = crossing_point(loop, road, point)
    result = associate(point, "LP200 @ FM100", index)

    assert crossing.equals(Point(x + 100, y))
    assert result.distance_m == pytest.approx(13**0.5)
    assert result.tier == "auto"


def test_overlapping_roads_use_the_nearest_point_on_the_overlap():
    first = LineString([(500_000, 3_300_000), (500_100, 3_300_000)])
    second = LineString([(500_020, 3_300_000), (500_080, 3_300_000)])
    point = Point(500_023, 3_300_003)

    crossing = crossing_point(first, second, point)
    result = associate(
        point, "IH 100 @ FM 200", build_road_index({"IH 100": first, "FM 200": second})
    )

    assert crossing.equals(Point(500_023, 3_300_000))
    assert result.distance_m == pytest.approx(3.0)
    assert result.tier == "auto"


def test_a_road_cannot_be_its_own_claimed_intersection(roads):
    result = associate(Point(500_100, 3_300_000), "IH 10 @ I-10", roads)

    assert result.tier == "flag"
    assert result.method == "fallback_v1"
    assert result.reason == "same_road_on_both_sides"
    assert result.gap_m is None


@pytest.mark.parametrize(
    ("gap", "point_offset", "tier", "reason"),
    [
        pytest.param(
            30, 0, "flag", "no_crossing_in_search_radius", id="small_local_gap"
        ),
        pytest.param(500, 0, "manual", "named_roads_far_apart", id="large_gap"),
        pytest.param(
            30, -300, "manual", "named_roads_far_apart", id="point_far_from_small_gap"
        ),
    ],
)
def test_disjoint_roads_require_a_small_gap_and_proximity_to_both_roads(
    gap, point_offset, tier, reason
):
    x, y = 500_000.0, 3_300_000.0
    index = build_road_index(
        {
            "IH 100": LineString([(x - 100, y), (x + 100, y)]),
            "FM 200": LineString([(x - 100, y + gap), (x + 100, y + gap)]),
        }
    )

    result = associate(Point(x, y + point_offset), "IH 100 @ FM 200", index)

    assert result.method == "road_gap"
    assert result.tier == tier
    assert result.reason == reason
    assert result.gap_m == pytest.approx(gap)
    assert result.distance_m == inf
    assert result.road_distances_m == pytest.approx(
        (abs(point_offset), abs(gap - point_offset))
    )


@pytest.mark.parametrize(
    ("gap", "first", "second", "tier"),
    [
        (50.0, 50.0, 50.0, "flag"),
        (50.0001, 0.0, 50.0, "manual"),
        (30.0, 50.0001, 20.0, "manual"),
        (30.0, 20.0, 50.0001, "manual"),
    ],
)
def test_gap_and_both_point_distances_have_exact_numeric_limits(
    gap, first, second, tier
):
    assert GAP_MAX_M == 50.0
    assert _gap_tier(gap, (first, second)) == tier


def test_a_small_nearby_alias_gap_wins_over_a_smaller_distant_route_gap():
    x, y = 500_000.0, 3_300_000.0
    index = build_road_index(
        {
            "IH 100": LineString([(x - 100, y + 300), (x + 100, y + 300)]),
            "FM 200": LineString([(x - 100, y + 305), (x + 100, y + 305)]),
            "OAK RD": LineString([(x - 100, y), (x + 100, y)]),
            "ELM RD": LineString([(x - 100, y + 30), (x + 100, y + 30)]),
        }
    )

    result = associate(Point(x, y), "IH 100 / OAK RD @ FM 200 / ELM RD", index)

    assert result.tier == "flag"
    assert result.road_keys == ("OAKRD", "ELMRD")
    assert result.gap_m == pytest.approx(30)
    assert result.road_distances_m == pytest.approx((0, 30))


def test_digitizing_break_is_a_road_gap():
    x, y = 500_000.0, 3_300_000.0
    index = build_road_index(
        {
            "IH 100": LineString([(x - 100, y), (x - 5, y)]),
            "FM 200": LineString([(x, y), (x, y + 100)]),
        }
    )

    result = associate(Point(x, y), "IH 100 @ FM 200", index)

    assert result.method == "road_gap"
    assert result.tier == "flag"
    assert result.gap_m == pytest.approx(5)


def test_one_road_missing_locally_uses_capped_nearest_road_fallback():
    x, y = 500_000.0, 3_300_000.0
    index = build_road_index(
        {
            "IH 100": LineString([(x - 100, y), (x + 100, y)]),
            "FM 200": LineString([(x + 3_000, y - 100), (x + 3_000, y + 100)]),
        }
    )

    result = associate(Point(x, y), "IH 100 @ FM 200", index)

    assert result.method == "fallback_v1"
    assert result.tier == "flag"
    assert result.distance_m == pytest.approx(0)
    assert result.gap_m is None


def test_a_real_crossing_takes_precedence_over_a_nearer_gap_pair():
    x, y = 500_000.0, 3_300_000.0
    index = build_road_index(
        {
            "IH 100": LineString([(x - 100, y + 100), (x + 100, y + 100)]),
            "FM 200": LineString([(x, y + 90), (x, y + 110)]),
            "OAK RD": LineString([(x - 10, y), (x + 10, y)]),
            "ELM RD": LineString([(x - 10, y + 5), (x + 10, y + 5)]),
        }
    )

    result = associate(Point(x, y), "IH 100 / OAK RD @ FM 200 / ELM RD", index)

    assert result.method == "named_crossing"
    assert result.tier == "manual"
    assert result.distance_m == pytest.approx(100)
    assert result.gap_m is None


def test_shared_route_alone_does_not_count_as_two_nearby_roads():
    x, y = 500_000.0, 3_300_000.0
    index = build_road_index(
        {
            "FM 100": LineString([(x - 100, y), (x + 100, y)]),
            "OAK RD": LineString([(x + 3_000, y - 100), (x + 3_000, y + 100)]),
            "ELM RD": LineString([(x + 4_000, y - 100), (x + 4_000, y + 100)]),
        }
    )

    result = associate(Point(x, y), "FM 100 / OAK RD @ FM 100 / ELM RD", index)

    assert result.method == "fallback_v1"
    assert result.tier == "flag"
    assert result.reason == "named_road_outside_search_radius"
    assert result.gap_m is None


@pytest.mark.parametrize("name", ["", None])
def test_road_index_rejects_missing_road_names(name):
    with pytest.raises(ValueError, match="road name"):
        build_road_index(
            [(name, LineString([(500_000, 3_300_000), (501_000, 3_300_000)]))]
        )

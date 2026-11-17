"""Intersection means and direction safeguards on a fictional UTM road grid."""

import pandas as pd
import pytest
from shapely.geometry import LineString

from signal_qaqc.geo import to_lonlat
from signal_qaqc.pipeline import run

X, Y = 500_000.0, 3_300_000.0


@pytest.fixture
def roads():
    return {
        "IH 10": LineString([(X - 500, Y), (X + 500, Y)]),
        "FM 100": LineString([(X, Y - 500), (X, Y + 500)]),
    }


def signals_at(offsets, location="IH 10 @ FM 100"):
    rows = []
    for n, (dx, dy) in enumerate(offsets):
        lon, lat = to_lonlat(X + dx, Y + dy)
        rows.append(
            {
                "signal_gid": f"fictional-{n + 1}",
                "location": location,
                "travel_direction": ["NB", "SB", "EB", "WB"][n],
                "city": "Fictional City",
                "preemption": "None",
                "SignalID": "",
                "x": X + dx,
                "y": Y + dy,
                "lon": lon,
                "lat": lat,
                "head_1_type": "3-Light",
            }
        )
    return pd.DataFrame(rows)


def test_balanced_directions_share_one_auto_intersection_decision(roads):
    source = signals_at([(-12, 0), (12, 0), (0, -20), (0, 20)])
    before = source.copy(deep=True)

    cleaned, _, issues = run(source, roads)

    assert cleaned["match_tier"].tolist() == ["auto"] * 4
    assert cleaned["match_scope"].tolist() == ["intersection"] * 4
    assert cleaned["intersection_x"].tolist() == pytest.approx([X] * 4)
    assert cleaned["intersection_y"].tolist() == pytest.approx([Y] * 4)
    assert cleaned["approach_distance_m"].tolist() == pytest.approx([12, 12, 20, 20])
    assert not issues["rule"].isin(["road_association", "approach_distance"]).any()
    pd.testing.assert_frame_equal(source, before)
    pd.testing.assert_frame_equal(cleaned[["x", "y"]], source[["x", "y"]])


def test_direction_outlier_is_flagged_even_when_the_mean_is_only_flag(roads):
    source = signals_at([(0, 0), (4, 0), (-4, 0), (200, 0)])

    cleaned, _, issues = run(source, roads)

    assert cleaned["match_tier"].tolist() == ["flag"] * 4
    assert cleaned["match_distance_m"].tolist() == pytest.approx([50] * 4)
    directional = issues.loc[issues["rule"].eq("approach_distance")]
    assert directional["record_id"].tolist() == ["fictional-4"]
    assert directional["severity"].tolist() == ["warning"]
    assert directional["action"].tolist() == ["review"]
    assert cleaned.loc[3, "approach_distance_m"] == pytest.approx(150)


def test_single_record_is_its_own_mean_without_a_direction_check(roads):
    cleaned, _, issues = run(signals_at([(3, 0)]), roads)

    assert cleaned.loc[0, "intersection_x"] == pytest.approx(X + 3)
    assert cleaned.loc[0, "match_distance_m"] == pytest.approx(3)
    assert pd.isna(cleaned.loc[0, "approach_distance_m"])
    assert not issues["rule"].eq("approach_distance").any()


def test_drop_rows_do_not_shift_the_mean_or_receive_direction_checks(roads):
    source = signals_at([(-12, 0), (12, 0), (0, -20), (0, 20)])
    shadow = source.iloc[0].to_dict()
    shadow.update(signal_gid="fictional-drop", x=shadow["x"] + 0.5)
    shadow["lon"], shadow["lat"] = to_lonlat(shadow["x"], shadow["y"])
    source = pd.concat([source, pd.DataFrame([shadow])], ignore_index=True)

    cleaned, _, issues = run(source, roads)

    assert cleaned.loc[4, "dup_role"] == "drop"
    assert cleaned.loc[:3, "intersection_x"].tolist() == pytest.approx([X] * 4)
    assert pd.isna(cleaned.loc[4, "match_tier"])
    assert pd.isna(cleaned.loc[4, "approach_distance_m"])
    assert not (
        (issues["record_id"] == "fictional-drop")
        & (issues["rule"] == "approach_distance")
    ).any()


def test_beacons_are_checked_individually_without_intersection_aggregation(roads):
    source = signals_at([(100, 0), (0, 100)], "1234 FM 100")

    cleaned, _, issues = run(source, roads)

    assert cleaned["match_scope"].tolist() == ["record"] * 2
    assert cleaned["match_method"].tolist() == ["nearest_v1"] * 2
    assert cleaned["match_tier"].tolist() == ["auto"] * 2
    assert cleaned["intersection_x"].isna().all()
    assert cleaned["approach_distance_m"].isna().all()
    assert not issues["rule"].eq("approach_distance").any()


def test_fallbacks_keep_individual_road_evidence(roads):
    source = signals_at([(100, 0), (0, 100)], "IH 10 @ FM 999")

    cleaned, _, issues = run(source, roads)

    assert cleaned["match_scope"].tolist() == ["record"] * 2
    assert cleaned["match_method"].tolist() == ["fallback_v1"] * 2
    assert cleaned["match_tier"].tolist() == ["flag"] * 2
    assert cleaned["match_distance_m"].tolist() == pytest.approx([0, 0])
    assert not issues["rule"].eq("approach_distance").any()


@pytest.mark.parametrize("location", [None, "  "])
def test_blank_names_are_not_combined_into_one_intersection(roads, location):
    source = signals_at([(100, 0), (0, 100)], location)

    cleaned, _, issues = run(source, roads)

    assert cleaned["match_scope"].tolist() == ["record"] * 2
    assert cleaned["match_distance_m"].tolist() == pytest.approx([0, 0])
    assert cleaned["intersection_x"].isna().all()
    assert not issues["rule"].eq("approach_distance").any()


def test_invalid_points_do_not_enter_the_intersection_mean(roads):
    source = signals_at([(-12, 0), (12, 0), (0, -20), (0, 20)])
    invalid = source.iloc[0].to_dict()
    invalid.update(signal_gid="fictional-invalid", x="")
    source = pd.concat([source, pd.DataFrame([invalid])], ignore_index=True)

    cleaned, _, issues = run(source, roads)

    assert cleaned.loc[:3, "intersection_x"].tolist() == pytest.approx([X] * 4)
    assert cleaned.loc[4, "match_method"] == "invalid_point"
    assert pd.isna(cleaned.loc[4, "match_scope"])
    assert not (
        (issues["record_id"] == "fictional-invalid")
        & (issues["rule"] == "approach_distance")
    ).any()


def test_repeated_pipeline_run_does_not_compare_derived_intersection_fields(roads):
    source = signals_at([(-12, 0), (12, 0), (0, -20), (0, 20)])
    first, _, _ = run(source, roads)

    second, _, _ = run(first, roads)

    assert first["SignalID"].tolist() == second["SignalID"].tolist()
    assert first["dup_class"].tolist() == second["dup_class"].tolist()
    pd.testing.assert_frame_equal(
        first[["intersection_x", "intersection_y", "approach_distance_m"]],
        second[["intersection_x", "intersection_y", "approach_distance_m"]],
    )


def test_distinct_devices_are_individual_and_do_not_shift_intersection_mean(roads):
    source = signals_at([(-12, 0), (12, 0), (0, -20), (0, 20)])
    distinct = source.iloc[0].to_dict()
    distinct.update(
        signal_gid="fictional-distinct", city="Another Fictional City", x=X + 200
    )
    distinct["lon"], distinct["lat"] = to_lonlat(distinct["x"], distinct["y"])
    source = pd.concat([source, pd.DataFrame([distinct])], ignore_index=True)

    cleaned, _, issues = run(source, roads)

    assert cleaned.loc[[0, 4], "dup_class"].tolist() == ["D_distinct"] * 2
    assert cleaned.loc[[0, 4], "match_scope"].tolist() == ["record"] * 2
    assert cleaned.loc[[0, 4], "match_distance_m"].tolist() == pytest.approx([12, 200])
    assert cleaned.loc[[0, 4], "intersection_x"].isna().all()
    assert cleaned.loc[[0, 4], "approach_distance_m"].isna().all()
    assert cleaned.loc[1:3, "intersection_x"].tolist() == pytest.approx([X + 4] * 3)
    assert not issues["rule"].eq("approach_distance").any()


def test_selected_alias_pair_is_visible_in_rows_and_exception_details():
    source = signals_at([(30, 0)], "IH 100 / OAK RD @ FM 200 / ELM RD")
    roads = {
        "IH 100": LineString([(X + 900, Y + 1000), (X + 1100, Y + 1000)]),
        "FM 200": LineString([(X + 1000, Y + 900), (X + 1000, Y + 1100)]),
        "OAK RD": LineString([(X - 100, Y), (X + 100, Y)]),
        "ELM RD": LineString([(X, Y - 100), (X, Y + 100)]),
    }

    cleaned, _, issues = run(source, roads)

    assert cleaned.loc[0, "match_road_a"] == "OAKRD"
    assert cleaned.loc[0, "match_road_b"] == "ELMRD"
    assert cleaned.loc[0, "match_tier"] == "flag"
    detail = issues.loc[issues["rule"].eq("road_association"), "detail"].iloc[0]
    assert "OAKRD" in detail and "ELMRD" in detail


@pytest.mark.parametrize(
    ("gap", "point_offset", "tier", "reason", "severity", "action"),
    [
        (30, 0, "flag", "no_crossing_in_search_radius", "warning", "review_in_office"),
        (500, 0, "manual", "named_roads_far_apart", "error", "send_to_field"),
        (30, -300, "manual", "named_roads_far_apart", "error", "send_to_field"),
    ],
)
def test_road_gap_exception_uses_tier_and_exposes_all_three_distances(
    gap, point_offset, tier, reason, severity, action
):
    roads = {
        "IH 10": LineString([(X - 500, Y), (X + 500, Y)]),
        "FM 100": LineString([(X - 500, Y + gap), (X + 500, Y + gap)]),
    }

    cleaned, _, issues = run(signals_at([(0, point_offset)]), roads)

    assert cleaned.loc[0, "match_method"] == "road_gap"
    assert cleaned.loc[0, "match_tier"] == tier
    assert cleaned.loc[0, "match_scope"] == "intersection"
    assert cleaned.loc[0, "match_gap_m"] == pytest.approx(gap)
    assert cleaned.loc[0, "match_reason"] == reason
    assert cleaned.loc[0, "match_road_a_distance_m"] == pytest.approx(abs(point_offset))
    assert cleaned.loc[0, "match_road_b_distance_m"] == pytest.approx(
        abs(gap - point_offset)
    )
    assert issues["rule"].tolist() == ["road_gap"]
    assert issues["severity"].tolist() == [severity]
    assert issues["action"].tolist() == [action]
    detail = issues.loc[0, "detail"]
    assert f"road gap={gap:.3f} m" in detail
    assert f"point-to-road A={abs(point_offset):.3f} m" in detail
    assert f"point-to-road B={abs(gap - point_offset):.3f} m" in detail
    assert "IH10" in detail and "FM100" in detail
    assert reason in detail


def test_road_gap_groups_retain_direction_outlier_checks():
    roads = {
        "IH 10": LineString([(X - 500, Y), (X + 500, Y)]),
        "FM 100": LineString([(X - 500, Y + 30), (X + 500, Y + 30)]),
    }

    cleaned, _, issues = run(signals_at([(0, 0), (4, 0), (-4, 0), (200, 0)]), roads)

    assert cleaned["match_method"].tolist() == ["road_gap"] * 4
    assert cleaned["match_scope"].tolist() == ["intersection"] * 4
    assert issues.loc[issues["rule"].eq("approach_distance"), "record_id"].tolist() == [
        "fictional-4"
    ]


def test_same_key_sides_request_name_review_without_claiming_a_road_gap(roads):
    cleaned, _, issues = run(signals_at([(0, 0)], "IH 10 @ IH 10"), roads)

    assert cleaned.loc[0, "match_method"] == "fallback_v1"
    assert cleaned.loc[0, "match_tier"] == "flag"
    assert cleaned.loc[0, "match_reason"] == "same_road_on_both_sides"
    assert pd.isna(cleaned.loc[0, "match_gap_m"])
    assert issues["rule"].tolist() == ["intersection_name"]
    assert issues["action"].tolist() == ["review_in_office"]
    assert issues["severity"].tolist() == ["warning"]

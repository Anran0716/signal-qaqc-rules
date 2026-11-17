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

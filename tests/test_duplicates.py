import pandas as pd
import pytest

from signal_qaqc.duplicates import _classify_group, mark_duplicates


@pytest.fixture
def signals():
    return pd.DataFrame(
        {
            "signal_gid": ["fictional-1", "fictional-2"],
            "location": ["SH100@Elm Rd", "SH 100 @ ELM RD"],
            "location_std": ["SH100@Elm Rd", "SH 100 @ ELM RD"],
            "name_status": ["FIX_FORMAT", "OK"],
            "travel_direction": ["NB", "NB"],
            "x": [500_000.0, 500_000.5],
            "y": [3_300_000.0, 3_300_000.0],
            "edit_date": ["2026-01-01", "2026-02-01"],
            "city": ["Fictional City", "Fictional City"],
            "preemption": ["None", "None"],
            "SignalID": ["", ""],
        }
    )


@pytest.mark.parametrize(
    ("same", "distance", "expected"),
    [
        (True, 1.0, "A_exact"),
        (True, 1.0001, "B_near"),
        (True, 15.0, "B_near"),
        (True, 15.0001, "B_far"),
        (False, 0.5, "C_conflict"),
        (False, 15.0, "C_conflict"),
        (False, 15.0001, "D_distinct"),
    ],
)
def test_duplicate_class_boundaries_use_pure_numeric_distances(
    same, distance, expected
):
    assert _classify_group(same, distance) == expected


@pytest.mark.parametrize(
    ("same", "distance", "category", "roles"),
    [
        (True, 0.5, "A_exact", ["drop", "keep"]),
        (True, 3.0, "B_near", ["review", "keep"]),
        (True, 20.0, "B_far", ["review", "review"]),
        (False, 3.0, "C_conflict", ["review", "keep"]),
        (False, 20.0, "D_distinct", ["review", "review"]),
    ],
)
def test_mark_duplicates_classifies_all_five_cases_without_deleting_rows(
    signals, same, distance, category, roles
):
    signals.loc[1, "x"] = signals.loc[0, "x"] + distance
    if not same:
        signals.loc[1, "city"] = "Another Fictional City"
    before = signals.copy(deep=True)

    result = mark_duplicates(signals)

    pd.testing.assert_frame_equal(signals, before)
    pd.testing.assert_frame_equal(result[signals.columns], before)
    assert set(result.columns) - set(signals.columns) == {
        "dup_group",
        "dup_class",
        "dup_role",
    }
    assert result["dup_group"].tolist() == ["DUP-00001", "DUP-00001"]
    assert result["dup_class"].tolist() == [category, category]
    assert result["dup_role"].tolist() == roles


def test_different_travel_directions_are_distinct_records(signals):
    signals.loc[1, "travel_direction"] = "SB"

    result = mark_duplicates(signals)

    assert result["dup_group"].tolist() == ["", ""]
    assert result["dup_role"].tolist() == ["keep", "keep"]


def test_missing_direction_groups_with_an_empty_string_in_pandas_three(signals):
    signals["travel_direction"] = pd.Series([None, ""], dtype=object)

    result = mark_duplicates(signals)

    assert result["dup_class"].tolist() == ["A_exact", "A_exact"]


def test_coordinate_metadata_and_spelling_do_not_create_attribute_conflicts(signals):
    signals["GlobalID"] = ["different-guid-1", "different-guid-2"]
    signals["latitude"] = [1.0, 2.0]
    signals["longitude"] = [3.0, 4.0]
    signals["geometry_wkt"] = ["POINT (500000 3300000)", "POINT (500000.5 3300000)"]

    result = mark_duplicates(signals)

    assert result["dup_class"].tolist() == ["A_exact", "A_exact"]


def test_completeness_beats_a_newer_edit_date_and_literal_none_counts_as_filled(
    signals,
):
    signals["preemption"] = pd.Series([None, "None"], dtype=object)
    signals["edit_date"] = ["2026-03-01", "2026-01-01"]

    result = mark_duplicates(signals)

    assert result["dup_class"].tolist() == ["C_conflict", "C_conflict"]
    assert result["dup_role"].tolist() == ["review", "keep"]


def test_file_order_breaks_ties_instead_of_dataframe_index_order(signals):
    signals["edit_date"] = "2026-01-01"
    signals.index = [90, 2]

    result = mark_duplicates(signals)

    assert result.index.tolist() == [90, 2]
    assert result["dup_role"].tolist() == ["keep", "drop"]


def test_group_distance_uses_the_farthest_pair_not_a_chain_of_near_pairs(signals):
    signals = pd.concat([signals.iloc[[0]]] * 3, ignore_index=True)
    signals["signal_gid"] = ["fictional-1", "fictional-2", "fictional-3"]
    signals["x"] = [500_000.0, 500_010.0, 500_020.0]

    result = mark_duplicates(signals)

    assert result["dup_class"].tolist() == ["B_far"] * 3
    assert result["dup_role"].tolist() == ["review"] * 3


def test_missing_names_are_not_merged_into_a_duplicate_group(signals):
    signals["location_std"] = pd.Series([None, ""], dtype=object)

    result = mark_duplicates(signals)

    assert result["dup_group"].tolist() == ["", ""]
    assert result["dup_role"].tolist() == ["review", "review"]


def test_duplicate_marking_is_idempotent(signals):
    first = mark_duplicates(signals)

    pd.testing.assert_frame_equal(mark_duplicates(first), first)


def test_duplicate_marking_handles_tables_with_no_business_attribute_columns(signals):
    signals = signals.drop(columns=["city", "preemption", "SignalID"])

    result = mark_duplicates(signals)

    assert result["dup_class"].tolist() == ["A_exact", "A_exact"]
    assert result["dup_role"].tolist() == ["drop", "keep"]


def test_comparison_can_be_limited_to_explicit_business_columns(signals):
    signals.loc[1, "city"] = "Another Fictional City"

    result = mark_duplicates(signals, attribute_cols=["preemption"])

    assert result["dup_class"].tolist() == ["A_exact", "A_exact"]


def test_duplicate_marking_accepts_an_empty_table(signals):
    result = mark_duplicates(signals.iloc[:0])

    assert result.empty
    assert result.columns.tolist() == [
        *signals.columns,
        "dup_group",
        "dup_class",
        "dup_role",
    ]


def test_invalid_coordinates_in_a_duplicate_group_require_repair_first(signals):
    signals["x"] = pd.Series([500_000.0, None], dtype=object)

    with pytest.raises(ValueError, match="coordinates"):
        mark_duplicates(signals)

import pandas as pd
import pytest

from signal_qaqc.duplicates import mark_duplicates
from signal_qaqc.ids import assign_signal_ids


def records(ids, roles=None):
    return pd.DataFrame(
        {
            "signal_gid": [f"fictional-{i}" for i in range(len(ids))],
            "SignalID": pd.array(ids, dtype=object),
            "dup_role": roles if roles is not None else ["keep"] * len(ids),
        }
    )


def test_valid_ids_are_preserved_and_new_ids_follow_the_largest_existing_number():
    source = records(["", "SIG-00007"])

    result, issues, last_number = assign_signal_ids(source)

    assert result["SignalID"].tolist() == ["SIG-00008", "SIG-00007"]
    assert issues == []
    assert last_number == 8
    assert source["SignalID"].tolist() == ["", "SIG-00007"]


@pytest.mark.parametrize(
    "invalid", ["7", "SIG-7", "sig-00007", "SIG-000007", "SIG-0000A", 7, 7.0]
)
def test_invalid_id_formats_are_replaced_with_one_exception(invalid):
    result, issues, last_number = assign_signal_ids(records(["SIG-00010", invalid]))

    assert result["SignalID"].tolist() == ["SIG-00010", "SIG-00011"]
    assert last_number == 11
    assert len(issues) == 1
    assert issues[0]["record_id"] == "fictional-1"
    assert issues[0]["rule"] == "invalid_signal_id"
    assert issues[0]["field"] == "SignalID"
    assert issues[0]["action"] == "auto_fixed"
    assert "SIG-00011" in issues[0]["detail"]


def test_duplicate_ids_keep_the_first_active_copy_and_replace_the_rest():
    result, issues, last_number = assign_signal_ids(
        records(["SIG-00007", "SIG-00007", ""])
    )

    assert result["SignalID"].tolist() == ["SIG-00007", "SIG-00008", "SIG-00009"]
    assert [issue["rule"] for issue in issues] == ["duplicate_signal_id"]
    assert last_number == 9


def test_only_drop_rows_are_skipped_and_their_existing_numbers_are_reserved():
    source = records(["SIG-00040", "", None, ""], ["drop", "drop", "keep", "review"])

    result, issues, last_number = assign_signal_ids(source)

    assert result["SignalID"].tolist() == ["SIG-00040", "", "SIG-00041", "SIG-00042"]
    assert issues == []
    assert last_number == 42


def test_a_drop_copy_does_not_force_replacement_of_a_valid_active_id():
    source = records(["SIG-00007", "SIG-00007"], ["drop", "keep"])

    result, issues, last_number = assign_signal_ids(source)

    assert result["SignalID"].tolist() == ["SIG-00007", "SIG-00007"]
    assert issues == []
    assert last_number == 7


def test_assigning_ids_twice_does_not_change_them():
    first, _, last_number = assign_signal_ids(records(["SIG-00007", "SIG-7", None]))

    second, issues, second_last_number = assign_signal_ids(
        first, last_number=last_number
    )

    pd.testing.assert_frame_equal(second, first)
    assert issues == []
    assert second_last_number == last_number


def test_saved_last_number_prevents_reusing_ids_absent_from_a_later_batch():
    _, _, saved_number = assign_signal_ids(records(["SIG-00040", ""]))

    result, issues, last_number = assign_signal_ids(
        records([""]), last_number=saved_number
    )

    assert result["SignalID"].tolist() == ["SIG-00042"]
    assert issues == []
    assert last_number == 42


def test_ids_belong_to_records_and_directions_not_to_the_shared_intersection():
    source = records(["", ""])
    source["location_std"] = "SH 100 @ Fictional Rd"
    source["travel_direction"] = ["NB", "SB"]
    source.index = [5, 5]

    result, issues, _ = assign_signal_ids(source)

    assert result["SignalID"].tolist() == ["SIG-00001", "SIG-00002"]
    assert result.index.tolist() == [5, 5]
    assert issues == []


def test_id_assignment_requires_duplicate_roles_first():
    with pytest.raises(ValueError, match="dup_role"):
        assign_signal_ids(records([""]).drop(columns="dup_role"))


def test_unknown_duplicate_roles_are_rejected():
    with pytest.raises(ValueError, match="dup_role"):
        assign_signal_ids(records([""], ["delete"]))


def test_five_digit_space_exhaustion_does_not_modify_the_input():
    source = records([""])
    before = source.copy(deep=True)

    with pytest.raises(ValueError, match="exhausted"):
        assign_signal_ids(source, last_number=99_999)

    pd.testing.assert_frame_equal(source, before)


@pytest.mark.parametrize("last_number", [-1, 100_000, 1.5, True])
def test_invalid_saved_last_numbers_are_rejected(last_number):
    with pytest.raises(ValueError, match="last_number"):
        assign_signal_ids(records([""]), last_number=last_number)


def test_empty_table_returns_the_saved_number_without_allocations():
    result, issues, last_number = assign_signal_ids(records([]), last_number=42)

    assert result.empty
    assert issues == []
    assert last_number == 42


def test_mark_then_assign_is_idempotent_and_never_assigns_an_exact_duplicate_drop():
    source = pd.DataFrame(
        {
            "signal_gid": ["fictional-1", "fictional-2", "fictional-3"],
            "location_std": ["SH100@Elm Rd", "SH 100 @ ELM RD", "SH 100 @ ELM RD"],
            "travel_direction": ["NB", "NB", "SB"],
            "x": [500_000.0, 500_000.5, 500_000.0],
            "y": [3_300_000.0] * 3,
            "city": ["Fictional City"] * 3,
            "preemption": ["None"] * 3,
        }
    )
    marked = mark_duplicates(source)

    first, _, saved = assign_signal_ids(marked)
    second, issues, saved_again = assign_signal_ids(
        mark_duplicates(first), last_number=saved
    )

    assert first["SignalID"].tolist() == ["SIG-00001", "", "SIG-00002"]
    pd.testing.assert_frame_equal(second, first)
    assert issues == []
    assert saved_again == saved

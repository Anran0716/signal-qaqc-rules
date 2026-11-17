import pandas as pd
import pytest

from signal_qaqc.heads import detect_positions, explode_heads, reconcile


def table(rows):
    return pd.DataFrame(
        rows, columns=["signal_gid", "position", "head_type", "left_turn", "right_turn"]
    )


def test_detect_positions_uses_all_head_components_and_numeric_order():
    assert detect_positions(
        ["signal_gid", "head_10_right", "head_4_type", "HEAD_2_LEFT", "head_count"]
    ) == [2, 4, 10]


def test_explode_keeps_the_fourth_head_and_preserves_the_master():
    source = pd.DataFrame(
        [
            {
                "signal_gid": "fictional-1",
                "preemption": "None",
                **{f"head_{n}_type": "3-Light" for n in range(1, 5)},
            }
        ]
    )
    before = source.copy(deep=True)

    heads, issues = explode_heads(source)

    assert heads.columns.tolist() == [
        "signal_gid",
        "position",
        "head_type",
        "left_turn",
        "right_turn",
    ]
    assert heads["position"].tolist() == [1, 2, 3, 4]
    assert heads["signal_gid"].tolist() == ["fictional-1"] * 4
    assert issues == []
    pd.testing.assert_frame_equal(source, before)


@pytest.mark.parametrize(
    "blank", ["NA", "null", "N/A", "None", None, float("nan"), pd.NA, " \t"]
)
def test_head_placeholders_do_not_create_a_head(blank):
    source = pd.DataFrame([{"signal_gid": "fictional-1", "head_1_type": blank}])

    heads, issues = explode_heads(source)

    assert heads.empty
    assert issues == []


@pytest.mark.parametrize("blank", ["NA", "null", None])
def test_turn_placeholders_and_old_protected_wording_are_normalized(blank):
    source = pd.DataFrame(
        [
            {
                "signal_gid": "fictional-1",
                "head_1_type": "3-Light",
                "head_1_left": "Protected Left Turn (Green Arrow)",
                "head_1_right": blank,
            }
        ]
    )

    heads, issues = explode_heads(source)

    assert heads.loc[0, "left_turn"] == "Protected"
    assert heads.loc[0, "right_turn"] is None
    assert issues == []


def test_turn_values_without_a_type_column_are_detected_and_reported():
    source = pd.DataFrame([{"signal_gid": "fictional-1", "head_4_left": "Protected"}])

    heads, issues = explode_heads(source)

    assert heads.empty
    assert [issue["rule"] for issue in issues] == ["head_turn_without_type"]
    assert issues[0]["severity"] == "warning"
    assert "4" in issues[0]["detail"]


def test_position_gaps_warn_without_discarding_later_heads():
    source = pd.DataFrame(
        [
            {
                "signal_gid": "fictional-1",
                "head_1_type": "3-Light",
                "head_2_type": "NA",
                "head_3_type": "3-Light",
                "head_4_type": "4-Light",
            }
        ]
    )

    heads, issues = explode_heads(source)

    assert heads["position"].tolist() == [1, 3, 4]
    assert [issue["rule"] for issue in issues] == ["head_position_gap"] * 2
    assert all(issue["severity"] == "warning" for issue in issues)


def test_sparse_schema_still_derives_the_later_position():
    source = pd.DataFrame([{"signal_gid": "fictional-1", "HEAD_4_TYPE": "3-Light"}])

    heads, issues = explode_heads(source)

    assert heads["position"].tolist() == [4]
    assert issues[0]["rule"] == "head_position_gap"


def test_drop_parents_are_excluded_from_rederived_heads():
    source = pd.DataFrame(
        {
            "signal_gid": ["fictional-drop", "fictional-keep"],
            "dup_role": ["drop", "keep"],
            "head_1_type": ["3-Light", "4-Light"],
        }
    )

    heads, issues = explode_heads(source)

    assert heads["signal_gid"].tolist() == ["fictional-keep"]
    assert issues == []


@pytest.mark.parametrize("role", [None, pd.NA, float("nan")])
def test_empty_duplicate_role_does_not_exclude_an_active_parent(role):
    source = pd.DataFrame(
        [{"signal_gid": "fictional-1", "dup_role": role, "head_1_type": "3-Light"}]
    )

    heads, issues = explode_heads(source)

    assert heads["signal_gid"].tolist() == ["fictional-1"]
    assert issues == []


def test_duplicate_active_parent_ids_are_rejected():
    source = pd.DataFrame(
        {"signal_gid": ["fictional-1"] * 2, "head_1_type": ["3-Light"] * 2}
    )

    with pytest.raises(ValueError, match="signal_gid"):
        explode_heads(source)


def test_missing_head_schema_is_rejected_instead_of_silently_producing_no_heads():
    with pytest.raises(ValueError, match="head"):
        explode_heads(pd.DataFrame({"signal_gid": ["fictional-1"]}))


def test_empty_master_retains_the_long_table_schema():
    heads, issues = explode_heads(pd.DataFrame(columns=["signal_gid", "head_1_type"]))

    assert heads.empty
    assert heads.columns.tolist() == table([]).columns.tolist()
    assert issues == []


@pytest.mark.parametrize("columns", [["head_0_type"], ["head_1_type", "HEAD_1_TYPE"]])
def test_ambiguous_or_invalid_position_schema_is_rejected(columns):
    with pytest.raises(ValueError, match="head"):
        detect_positions(columns)


def test_reconcile_reports_all_six_difference_types_in_one_batch():
    expected = table(
        [
            ("duplicate", 1, "3-Light", None, None),
            ("missing", 4, "4-Light", None, None),
            ("changed", 1, "3-Light", "Protected", None),
        ]
    )
    delivered = table(
        [
            ("duplicate", 1, "3-Light", None, None),
            ("duplicate", 1, "3-Light", None, None),
            ("stale", 2, "3-Light", None, None),
            ("orphan", 1, "3-Light", None, None),
            ("dropped", 1, "3-Light", None, None),
            ("changed", 1, "3-Light", "Permissive", None),
        ]
    )
    before_expected, before_delivered = (
        expected.copy(deep=True),
        delivered.copy(deep=True),
    )

    issues = reconcile(
        expected, delivered, {"duplicate", "missing", "stale", "changed"}, {"dropped"}
    )

    assert len(issues) == 6
    assert {issue["rule"]: issue["severity"] for issue in issues} == {
        "head_duplicate": "error",
        "head_missing": "error",
        "head_stale": "warning",
        "head_orphan": "warning",
        "head_dropped": "info",
        "head_changed": "warning",
    }
    missing = next(issue for issue in issues if issue["rule"] == "head_missing")
    assert "4" in missing["detail"]
    pd.testing.assert_frame_equal(expected, before_expected)
    pd.testing.assert_frame_equal(delivered, before_delivered)


@pytest.mark.parametrize(
    ("legacy", "canonical"),
    [
        ("Protected Left Turn (Green Arrow)", "Protected"),
        ("Permissive Left Turn (Green Circle)", "Permissive"),
        ("Turn Not Allow", "Turn Not Allowed"),
    ],
)
def test_reconcile_normalizes_legacy_turn_values_before_comparing(legacy, canonical):
    expected = table([("fictional-1", 1, "3-Light", canonical, None)])
    delivered = table([("fictional-1", 1, "3-Light", legacy, "NA")])

    assert reconcile(expected, delivered, {"fictional-1"}, set()) == []


def test_identical_head_tables_produce_no_exceptions():
    expected = table([("fictional-1", 1, "3-Light", "Protected", None)])

    assert reconcile(expected, expected.copy(), {"fictional-1"}, set()) == []


def test_parent_display_names_are_not_used_as_reconciliation_keys():
    expected = table([("fictional-1", 1, "3-Light", None, None)])
    delivered = expected.copy(deep=True)
    expected["location_std"] = "SH 100 @ New Fictional Rd"
    delivered["location_std"] = "SH100 @ Old Fictional Rd"

    assert reconcile(expected, delivered, {"fictional-1"}, set()) == []


def test_duplicate_delivery_cannot_hide_a_conflicting_second_copy():
    expected = table([("fictional-1", 1, "3-Light", "Protected", None)])
    delivered = table(
        [
            ("fictional-1", 1, "3-Light", "Protected", None),
            ("fictional-1", 1, "3-Light", "Permissive", None),
        ]
    )

    issues = reconcile(expected, delivered, {"fictional-1"}, set())

    assert [issue["rule"] for issue in issues] == ["head_duplicate", "head_changed"]


def test_duplicate_stale_rows_generate_one_presence_issue_per_key():
    delivered = table([("fictional-1", 2, "3-Light", None, None)] * 3)

    issues = reconcile(table([]), delivered, {"fictional-1"}, set())

    assert [issue["rule"] for issue in issues] == ["head_duplicate", "head_stale"]


def test_empty_tables_reconcile_without_exceptions():
    assert reconcile(table([]), table([]), set(), set()) == []


def test_duplicate_expected_keys_are_rejected_before_the_merge():
    expected = table([("fictional-1", 1, "3-Light", None, None)] * 2)

    with pytest.raises(ValueError, match="expected"):
        reconcile(expected, table([]), {"fictional-1"}, set())


def test_csv_position_strings_match_integer_positions():
    expected = table([("fictional-1", 4, "3-Light", None, None)])
    delivered = table([("fictional-1", "4.0", "3-Light", None, None)])

    assert reconcile(expected, delivered, {"fictional-1"}, set()) == []


@pytest.mark.parametrize("position", [0, -1, 1.5, "NA", pd.NA, True, float("inf")])
def test_invalid_delivered_positions_are_rejected(position):
    delivered = table([("fictional-1", position, "3-Light", None, None)])

    with pytest.raises(ValueError, match="position"):
        reconcile(table([]), delivered, {"fictional-1"}, set())


@pytest.mark.parametrize("label", ["expected", "delivered"])
def test_reconciliation_reports_missing_schema_columns(label):
    tables = {"expected": table([]), "delivered": table([])}
    tables[label] = tables[label].drop(columns="left_turn")

    with pytest.raises(ValueError, match=rf"{label}.*left_turn"):
        reconcile(**tables, master_ids=set(), dropped_ids=set())


@pytest.mark.parametrize("blank", [None, pd.NA, "  "])
def test_blank_active_parent_ids_are_rejected(blank):
    source = pd.DataFrame([{"signal_gid": blank, "head_1_type": "3-Light"}])

    with pytest.raises(ValueError, match="signal_gid"):
        explode_heads(source)


@pytest.mark.parametrize("master_ids", [set(), {"fictional-drop"}])
def test_expected_heads_must_belong_to_an_active_master_parent(master_ids):
    expected = table([("fictional-drop", 1, "3-Light", None, None)])

    with pytest.raises(ValueError, match="active master"):
        reconcile(expected, table([]), master_ids, {"fictional-drop"})


def test_rederivation_detects_changes_since_the_delivered_heads_were_created():
    source = pd.DataFrame(
        [
            {
                "signal_gid": "fictional-1",
                "head_1_type": "3-Light",
                "head_1_left": "Protected",
                "head_2_type": "3-Light",
                "head_3_type": "3-Light",
                "head_4_type": "NA",
            }
        ]
    )
    delivered, _ = explode_heads(source)
    source.loc[0, "head_1_left"] = "Permissive"
    source.loc[0, "head_2_type"] = "NA"
    source.loc[0, "head_4_type"] = "4-Light"

    expected, _ = explode_heads(source)
    issues = reconcile(expected, delivered, {"fictional-1"}, set())

    assert {issue["rule"] for issue in issues} == {
        "head_missing",
        "head_stale",
        "head_changed",
    }
    assert "4" in next(i["detail"] for i in issues if i["rule"] == "head_missing")

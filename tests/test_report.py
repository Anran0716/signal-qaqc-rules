import pytest

from signal_qaqc.report import exception, to_frame


@pytest.mark.parametrize("severity", ["error", "warning", "info"])
@pytest.mark.parametrize("action", ["auto_fixed", "review", "send_to_field", "none"])
def test_exception_returns_standard_fields_for_valid_values(severity, action):
    row = exception(
        "signal-1", "required_field", "preemption", severity, action, "Missing answer"
    )

    assert row == {
        "record_id": "signal-1",
        "rule": "required_field",
        "field": "preemption",
        "severity": severity,
        "action": action,
        "detail": "Missing answer",
    }


@pytest.mark.parametrize("severity", ["critical", "ERROR", "", None])
def test_exception_rejects_unknown_severity(severity):
    with pytest.raises(ValueError, match="severity"):
        exception("signal-1", "required_field", "preemption", severity, "review", "")


@pytest.mark.parametrize("action", ["delete", "auto_fix", "None", "", None])
def test_exception_rejects_unknown_action(action):
    with pytest.raises(ValueError, match="action"):
        exception("signal-1", "required_field", "preemption", "error", action, "")


def test_to_frame_keeps_standard_columns_when_no_issues_are_found():
    table = to_frame([])

    assert table.empty
    assert table.columns.tolist() == [
        "record_id",
        "rule",
        "field",
        "severity",
        "action",
        "detail",
    ]


def test_to_frame_orders_severity_and_preserves_order_within_each_severity():
    rows = [
        exception(record_id, "name", "intersection_name", severity, "review", record_id)
        for record_id, severity in [
            ("info-1", "info"),
            ("warning-1", "warning"),
            ("error-1", "error"),
            ("warning-2", "warning"),
            ("error-2", "error"),
            ("info-2", "info"),
        ]
    ]

    table = to_frame(rows)

    assert table.to_dict("records") == [
        rows[2],
        rows[4],
        rows[1],
        rows[3],
        rows[0],
        rows[5],
    ]
    assert table.index.tolist() == list(range(len(rows)))


def test_to_frame_preserves_multiple_issues_for_the_same_record():
    rows = [
        exception(
            "signal-1",
            "required_field",
            "preemption",
            "error",
            "send_to_field",
            "Missing answer",
        ),
        exception(
            "signal-1",
            "xy_consistency",
            "latitude",
            "error",
            "review",
            "Coordinates disagree",
        ),
    ]

    table = to_frame(rows)

    assert table.to_dict("records") == rows

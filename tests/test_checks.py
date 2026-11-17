import pandas as pd
import pytest

from signal_qaqc.checks import _classify, check_required, check_xy
from signal_qaqc.geo import to_local, to_lonlat


@pytest.mark.parametrize("value", [None, float("nan"), pd.NA, "", " \t\n"])
def test_check_required_flags_missing_and_whitespace_values(value):
    signals = pd.DataFrame({"signal_gid": ["fictional-1"], "preemption": [value]})

    issues = check_required(signals, ["preemption"])

    assert len(issues) == 1
    assert issues[0]["record_id"] == "fictional-1"
    assert issues[0]["rule"] == "required_field"
    assert issues[0]["field"] == "preemption"
    assert issues[0]["severity"] == "error"
    assert issues[0]["action"] == "send_to_field"
    assert "preemption" in issues[0]["detail"]


@pytest.mark.parametrize("value", ["None", 0, False])
def test_check_required_preserves_literal_none_zero_and_false(value):
    signals = pd.DataFrame({"signal_gid": ["fictional-1"], "preemption": [value]})

    assert check_required(signals, ["preemption"]) == []


@pytest.mark.parametrize("missing", ["signal_gid", "preemption"])
def test_check_required_rejects_missing_columns(missing):
    signals = pd.DataFrame({"signal_gid": ["fictional-1"], "preemption": ["None"]})

    with pytest.raises(ValueError, match=missing):
        check_required(signals.drop(columns=missing), ["preemption"])


def test_check_required_keeps_one_issue_per_missing_field():
    signals = pd.DataFrame(
        {"signal_gid": ["fictional-1"], "preemption": [" "], "city": [None]}
    )

    issues = check_required(signals, ["preemption", "city"])

    assert [(issue["record_id"], issue["field"]) for issue in issues] == [
        ("fictional-1", "preemption"),
        ("fictional-1", "city"),
    ]


def test_check_required_supports_a_custom_id_column():
    signals = pd.DataFrame({"asset_id": ["fictional-1"], "preemption": [""]})

    issues = check_required(signals, ["preemption"], id_col="asset_id")

    assert issues[0]["record_id"] == "fictional-1"


def test_check_required_accepts_an_empty_table_with_the_required_columns():
    signals = pd.DataFrame(columns=["signal_gid", "preemption"])

    assert check_required(signals, ["preemption"]) == []


@pytest.fixture
def xy_signals():
    # A fictional UTM anchor, independent of any real road or intersection.
    lon, lat = to_lonlat(500_000.0, 3_300_000.0)
    x, y = to_local(lon, lat)
    return pd.DataFrame(
        {"signal_gid": ["fictional-1"], "lon": [lon], "lat": [lat], "x": [x], "y": [y]}
    ).astype(object)


@pytest.mark.parametrize(
    ("distance", "expected"),
    [
        pytest.param(0.0, None, id="zero"),
        pytest.param(0.5, None, id="below_one_meter"),
        pytest.param(1.0, None, id="exactly_one_meter"),
        pytest.param(1.0001, ("warning", "review"), id="above_one_meter"),
        pytest.param(15.0, ("warning", "review"), id="exactly_fifteen_meters"),
        pytest.param(15.0001, ("error", "send_to_field"), id="above_fifteen_meters"),
        pytest.param(20.0, ("error", "send_to_field"), id="well_above_fifteen_meters"),
    ],
)
def test_classify_uses_exact_numeric_thresholds(distance, expected):
    assert _classify(distance) == expected


@pytest.mark.parametrize(
    ("dx", "dy", "severity", "action"),
    [
        pytest.param(0.0, 0.0, None, None, id="matching_coordinates"),
        pytest.param(0.5, 0.0, None, None, id="half_a_meter"),
        pytest.param(3.0, 0.0, "warning", "review", id="three_meters"),
        pytest.param(20.0, 0.0, "error", "send_to_field", id="twenty_meters"),
        pytest.param(0.0, -20.0, "error", "send_to_field", id="negative_y_offset"),
        pytest.param(
            12.0,
            12.0,
            "error",
            "send_to_field",
            id="diagonal_well_above_fifteen_meters",
        ),
    ],
)
def test_check_xy_projects_and_measures_offsets_away_from_thresholds(
    xy_signals, dx, dy, severity, action
):
    xy_signals.loc[0, "x"] += dx
    xy_signals.loc[0, "y"] += dy
    before = xy_signals.copy(deep=True)

    issues = check_xy(xy_signals)

    pd.testing.assert_frame_equal(xy_signals, before)
    if severity is None:
        assert issues == []
    else:
        assert len(issues) == 1
        assert issues[0]["record_id"] == "fictional-1"
        assert issues[0]["rule"] == "xy_consistency"
        assert issues[0]["field"] == "x,y"
        assert issues[0]["severity"] == severity
        assert issues[0]["action"] == action
        assert "m" in issues[0]["detail"]


@pytest.mark.parametrize("missing", ["signal_gid", "lon", "lat", "x", "y"])
def test_check_xy_rejects_missing_columns(xy_signals, missing):
    with pytest.raises(ValueError, match=missing):
        check_xy(xy_signals.drop(columns=missing))


@pytest.mark.parametrize("value", [None, float("nan"), pd.NA, "", " \t\n"])
@pytest.mark.parametrize("field", ["lon", "lat", "x", "y"])
def test_missing_coordinates_are_reported_only_by_check_required(
    xy_signals, field, value
):
    xy_signals.loc[0, field] = value

    required_issues = check_required(xy_signals, ["lon", "lat", "x", "y"])
    xy_issues = check_xy(xy_signals)

    assert xy_issues == []
    assert [
        (issue["rule"], issue["field"]) for issue in required_issues + xy_issues
    ] == [("required_field", field)]


def test_check_xy_keeps_checking_other_records_after_missing_coordinates(xy_signals):
    other_signal = xy_signals.copy(deep=True)
    other_signal.loc[0, "signal_gid"] = "fictional-2"
    other_signal.loc[0, "x"] += 20.0
    xy_signals.loc[0, "lat"] = None
    signals = pd.concat([xy_signals, other_signal], ignore_index=True)

    issues = check_xy(signals)

    assert len(issues) == 1
    assert issues[0]["record_id"] == "fictional-2"
    assert issues[0]["severity"] == "error"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("x", "not a number"),
        ("y", float("inf")),
        ("lat", 91.0),
        ("lon", 181.0),
    ],
)
def test_check_xy_reports_invalid_coordinates_as_errors(xy_signals, field, value):
    xy_signals.loc[0, field] = value

    issues = check_xy(xy_signals)

    assert len(issues) == 1
    assert issues[0]["record_id"] == "fictional-1"
    assert issues[0]["rule"] == "xy_consistency"
    assert issues[0]["severity"] == "error"
    assert issues[0]["action"] == "send_to_field"
    assert issues[0]["detail"]


def test_check_xy_accepts_numeric_coordinate_strings(xy_signals):
    for field in ["lon", "lat", "x", "y"]:
        xy_signals[field] = xy_signals[field].astype(str)

    assert check_xy(xy_signals) == []


def test_check_xy_supports_custom_column_names(xy_signals):
    xy_signals.loc[0, "x"] += 2.0
    signals = xy_signals.rename(
        columns={
            "signal_gid": "asset_id",
            "lon": "Longitude",
            "lat": "Latitude",
            "x": "Easting",
            "y": "Northing",
        }
    )

    issues = check_xy(
        signals,
        id_col="asset_id",
        lon_col="Longitude",
        lat_col="Latitude",
        x_col="Easting",
        y_col="Northing",
    )

    assert issues[0]["record_id"] == "fictional-1"
    assert issues[0]["field"] == "Easting,Northing"
    assert issues[0]["severity"] == "warning"


def test_check_xy_accepts_an_empty_table_with_the_required_columns(xy_signals):
    assert check_xy(xy_signals.iloc[:0]) == []

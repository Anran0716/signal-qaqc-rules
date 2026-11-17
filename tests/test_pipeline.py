import json
import subprocess
import sys

import pandas as pd
import pytest
from shapely.geometry import LineString

from signal_qaqc.geo import to_lonlat
from signal_qaqc.heads import HEAD_COLUMNS
from signal_qaqc.pipeline import main, run
from signal_qaqc.report import ACTIONS, COLUMNS, SEVERITY_ORDER
from signal_qaqc.sample import make_sample, write_sample


@pytest.fixture
def signals():
    lon, lat = to_lonlat(500_000, 3_300_000)
    return pd.DataFrame(
        [
            {
                "signal_gid": "fictional-1",
                "location": "FM100 at IH10",
                "travel_direction": "NB",
                "city": "Fictional City",
                "preemption": "None",
                "lon": lon,
                "lat": lat,
                "x": 500_000.0,
                "y": 3_300_000.0,
                "SignalID": "",
                "head_1_type": "3-Light",
                "head_1_left": "Protected",
                "head_1_right": "NA",
            }
        ]
    )


@pytest.fixture
def roads():
    return {
        "IH 10": LineString([(499_500, 3_300_000), (500_500, 3_300_000)]),
        "FM 100": LineString([(500_000, 3_299_500), (500_000, 3_300_500)]),
    }


def test_pipeline_runs_in_order_without_mutating_input(signals, roads):
    duplicate = signals.iloc[0].to_dict()
    duplicate.update(signal_gid="fictional-2", location="IH 10 @ FM 100")
    source = pd.concat([signals, pd.DataFrame([duplicate])], ignore_index=True)
    before = source.copy(deep=True)

    cleaned, heads, issues = run(source, roads)

    pd.testing.assert_frame_equal(source, before)
    assert len(cleaned) == 2
    assert cleaned["location_std"].tolist() == ["IH 10 @ FM 100"] * 2
    assert cleaned["location_key"].tolist() == ["IH10@FM100"] * 2
    assert cleaned["dup_class"].tolist() == ["A_exact"] * 2
    assert cleaned["dup_role"].tolist() == ["keep", "drop"]
    assert cleaned["SignalID"].tolist() == ["SIG-00001", ""]
    assert heads["signal_gid"].tolist() == ["fictional-1"]
    assert cleaned.loc[0, "match_tier"] == "auto"
    assert pd.isna(cleaned.loc[1, "match_tier"])
    assert not issues["rule"].eq("required_field").any()
    assert issues.columns.tolist() == COLUMNS
    assert issues["severity"].map(SEVERITY_ORDER).is_monotonic_increasing


def test_missing_direction_is_reported_and_does_not_crash_duplicate_grouping(
    signals, roads
):
    duplicate = signals.iloc[0].to_dict()
    duplicate.update(signal_gid="fictional-2", travel_direction="")
    signals.loc[0, "travel_direction"] = pd.NA
    source = pd.concat([signals, pd.DataFrame([duplicate])], ignore_index=True)

    cleaned, _, issues = run(source, roads)

    assert cleaned["dup_class"].tolist() == ["A_exact"] * 2
    missing = issues.loc[issues["rule"].eq("required_field")]
    assert missing["field"].tolist() == ["travel_direction"] * 2
    assert "nan" not in cleaned["location_key"].str.lower().tolist()


def test_missing_coordinates_have_one_owner_and_do_not_break_duplicate_checks(
    signals, roads
):
    duplicate = signals.iloc[0].to_dict()
    duplicate.update(signal_gid="fictional-2", location="IH 10 @ FM 100", x="")
    source = pd.concat([signals, pd.DataFrame([duplicate])], ignore_index=True)

    cleaned, _, issues = run(source, roads)

    row = issues.loc[issues["record_id"].eq("fictional-2")]
    assert row["rule"].tolist() == ["required_field"]
    assert row["field"].tolist() == ["x"]
    assert cleaned.loc[1, "dup_role"] == "review"
    assert cleaned.loc[1, "match_method"] == "invalid_point"


def test_drop_parents_are_passed_to_head_reconciliation(signals, roads):
    other = signals.iloc[0].to_dict()
    other["signal_gid"] = "fictional-drop"
    source = pd.concat([signals, pd.DataFrame([other])], ignore_index=True)
    delivered = pd.DataFrame(
        [
            ("fictional-1", 1, "3-Light", "Protected", None),
            ("fictional-drop", 1, "3-Light", "Protected", None),
        ],
        columns=HEAD_COLUMNS,
    )

    _, _, issues = run(source, roads, delivered)

    heads = issues.loc[issues["rule"].str.startswith("head_")]
    assert heads["rule"].tolist() == ["head_dropped"]
    assert heads["severity"].tolist() == ["info"]


def test_rerunning_cleaned_input_preserves_ids_and_duplicate_decisions(signals, roads):
    signals.loc[0, "SignalID"] = "SIG-00007"
    first, heads, _ = run(signals, roads, last_number=90)

    second, second_heads, issues = run(
        first, roads, last_number=first.attrs["last_number"]
    )

    assert second["SignalID"].tolist() == ["SIG-00007"]
    assert second.attrs["last_number"] == 90
    assert first["dup_role"].tolist() == second["dup_role"].tolist()
    pd.testing.assert_frame_equal(heads, second_heads)
    assert not issues["rule"].isin(["duplicate_signal_id", "invalid_signal_id"]).any()


def test_empty_inputs_have_valid_output_schemas(signals):
    cleaned, heads, issues = run(signals.iloc[:0], {})

    assert cleaned.empty and heads.empty and issues.empty
    assert heads.columns.tolist() == HEAD_COLUMNS
    assert issues.columns.tolist() == COLUMNS


@pytest.mark.parametrize("gids", [["", "fictional-2"], ["same", "same"]])
def test_missing_or_duplicate_parent_identity_is_rejected(signals, gids):
    source = pd.concat([signals, signals], ignore_index=True)
    source["signal_gid"] = gids

    with pytest.raises(ValueError, match="signal_gid"):
        run(source, {})


def test_sample_is_reproducible_and_has_the_requested_network():
    first = make_sample()
    second = make_sample(seed=7)
    for a, b in zip(first, second, strict=True):
        pd.testing.assert_frame_equal(a, b)
    signals, roads, _ = first
    assert len(signals) == 37
    assert len(roads) == 6
    assert "SL 1604" in roads["road_name"].tolist()
    assert "Fictional Cedar Rd" in roads["road_name"].tolist()
    assert signals["location"].str.contains("LP 1604", regex=False).any()
    assert signals["x"].between(499_000, 502_000).all()
    assert signals["y"].between(3_299_000, 3_302_000).all()


def test_sample_pipeline_detects_every_injected_problem():
    signals, roads, delivered = make_sample()
    cleaned, heads, issues = run(signals, roads, delivered)
    rules_by_id = issues.groupby("record_id")["rule"].agg(set).to_dict()
    expected = {
        "sample-002": {"duplicate_signal_id"},
        "sample-003": {"invalid_signal_id"},
        "sample-010": {"head_turn_without_type"},
        "sample-011": {"head_position_gap", "head_stale"},
        "sample-021": {"required_field"},
        "sample-022": {"xy_consistency"},
        "sample-023": {"xy_consistency"},
        "sample-024": {"intersection_name", "road_association"},
        "sample-025": {"intersection_name", "road_association"},
        "sample-026": {"road_association"},
        "sample-027": {"road_association"},
        "sample-028": {"required_field"},
        "sample-033": {"duplicate_record", "head_dropped"},
        "sample-034": {"duplicate_record", "head_dropped"},
        "sample-035": {"duplicate_record"},
        "sample-036": {"duplicate_record"},
        "sample-037": {"duplicate_record", "road_association"},
    }
    for gid, rules in expected.items():
        assert rules <= rules_by_id[gid], (gid, rules_by_id.get(gid))
    by_gid = cleaned.set_index("signal_gid")
    assert by_gid.loc["sample-001", "SignalID"] == "SIG-00007"
    assert by_gid.loc["sample-033", "dup_role"] == "drop"
    assert by_gid.loc["sample-034", "dup_class"] == "A_exact"
    assert by_gid.loc["sample-035", "dup_class"] == "B_near"
    assert by_gid.loc["sample-036", "dup_class"] == "C_conflict"
    assert by_gid.loc["sample-037", "dup_class"] == "D_distinct"
    assert by_gid.loc["sample-026", "match_distance_m"] == pytest.approx(70)
    assert by_gid.loc["sample-027", "match_tier"] == "manual"
    assert ((heads["signal_gid"] == "sample-001") & (heads["position"] == 4)).any()
    assert not (
        (issues["record_id"] == "sample-001") & (issues["rule"] == "head_changed")
    ).any()
    assert not heads["signal_gid"].isin(["sample-033", "sample-034"]).any()
    assert {
        "head_duplicate",
        "head_missing",
        "head_stale",
        "head_orphan",
        "head_dropped",
        "head_changed",
    } <= set(issues["rule"])
    assert signals["preemption"].eq("None").any()
    assert not (
        (issues["rule"] == "required_field") & (issues["field"] == "preemption")
    ).any()
    assert set(issues["action"]) <= ACTIONS
    assert set(issues["severity"]) == {"error", "warning", "info"}
    assert len(cleaned) == 37


def test_cli_writes_tables_and_preserves_the_id_high_water_mark(tmp_path, capsys):
    write_sample(tmp_path)
    output = tmp_path / "results"
    args = [
        str(tmp_path / "signals.csv"),
        str(tmp_path / "roads.csv"),
        "--heads",
        str(tmp_path / "heads_delivered.csv"),
        "--output",
        str(output),
    ]
    main(args)
    assert "37 signals" in capsys.readouterr().out
    assert set(p.name for p in output.iterdir()) == {
        "signals_clean.csv",
        "heads_expected.csv",
        "exceptions.csv",
        "id_state.json",
    }
    assert (
        pd.read_csv(output / "signals_clean.csv", keep_default_na=False)["preemption"]
        .eq("None")
        .any()
    )
    state = json.loads((output / "id_state.json").read_text())
    new_row = (
        pd.read_csv(tmp_path / "signals.csv", keep_default_na=False).iloc[[28]].copy()
    )
    new_row["SignalID"] = ""
    new_row.to_csv(tmp_path / "next.csv", index=False)
    next_args = [
        str(tmp_path / "next.csv"),
        str(tmp_path / "roads.csv"),
        "--output",
        str(output),
    ]
    main(next_args)
    result = pd.read_csv(output / "signals_clean.csv")
    assert result.loc[0, "SignalID"] == f"SIG-{state['last_number'] + 1:05d}"


def test_pipeline_module_runs_from_outside_the_repository(tmp_path):
    write_sample(tmp_path)

    process = subprocess.run(
        [sys.executable, "-m", "signal_qaqc.pipeline", "signals.csv", "roads.csv"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert process.returncode == 0, process.stderr
    assert "37 signals" in process.stdout
    assert (tmp_path / "output/signals_clean.csv").exists()


def test_cli_rejects_corrupt_id_state_without_resetting_it(tmp_path, capsys):
    write_sample(tmp_path)
    state = tmp_path / "state.json"
    state.write_text("not valid JSON", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        main(
            [
                str(tmp_path / "signals.csv"),
                str(tmp_path / "roads.csv"),
                "--id-state",
                str(state),
            ]
        )

    assert error.value.code == 2
    assert "error:" in capsys.readouterr().err
    assert state.read_text(encoding="utf-8") == "not valid JSON"
    assert not (tmp_path / "output").exists()

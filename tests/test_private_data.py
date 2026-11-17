"""This is the delivery snapshot and has not yet been repaired.

Optional local invariants never embed private rows or assert exact counts.
"""

from pathlib import Path

import pandas as pd
import pytest

from signal_qaqc.adapt import adapt
from signal_qaqc.heads import detect_positions
from signal_qaqc.io import read_table
from signal_qaqc.pipeline import run
from signal_qaqc.report import ACTIONS, COLUMNS, SEVERITY_ORDER

SNAPSHOT = (
    Path(__file__).resolve().parents[1] / "data/private/signals_delivered_2025.csv"
)
pytestmark = pytest.mark.skipif(
    not SNAPSHOT.exists(), reason="Private delivery snapshot is local only"
)


def test_private_delivery_snapshot_invariants():
    source = read_table(SNAPSHOT)
    signals = adapt(source)
    # No private road export is required: this test checks stable table invariants.
    cleaned, heads, issues = run(signals, {})

    assert issues.columns.tolist() == COLUMNS
    assert set(issues["severity"]) <= set(SEVERITY_ORDER)
    assert set(issues["action"]) <= ACTIONS
    assert issues["severity"].map(SEVERITY_ORDER).is_monotonic_increasing
    assert not (
        (issues["rule"] == "required_field")
        & (issues["field"] == "preemption")
        & issues["record_id"].isin(
            signals.loc[signals["preemption"].eq("None"), "signal_gid"]
        )
    ).any()
    active = cleaned.loc[cleaned["dup_role"].ne("drop")]
    expected_keys = set()
    for row in active.to_dict(orient="records"):
        for position in detect_positions(signals.columns):
            value = row.get(f"head_{position}_type")
            if pd.notna(value) and str(value).strip().casefold() not in {
                "",
                "na",
                "n/a",
                "null",
                "none",
            }:
                expected_keys.add((row["signal_gid"], position))
    assert (
        set(heads[["signal_gid", "position"]].itertuples(index=False, name=None))
        == expected_keys
    )
    assert not heads.duplicated(["signal_gid", "position"]).any()
    assert active["SignalID"].str.fullmatch(r"SIG-[0-9]{5}").all()
    assert active["SignalID"].is_unique

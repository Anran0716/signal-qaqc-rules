"""Optional checks against an unrepaired local delivery snapshot and road export.

Only invariants are asserted; private coordinates, names, and counts stay local.
"""

from math import isfinite
from pathlib import Path

import pandas as pd
import pytest

from signal_qaqc.adapt import adapt
from signal_qaqc.io import read_table
from signal_qaqc.pipeline import run

PRIVATE = Path(__file__).resolve().parents[1] / "data/private"
SNAPSHOT = PRIVATE / "signals_delivered_2025.csv"
ROADS = PRIVATE / "roads_26914.csv"
pytestmark = pytest.mark.skipif(
    not (SNAPSHOT.exists() and ROADS.exists()),
    reason="Private snapshot and roads are local only",
)


def test_every_participating_record_has_a_match_decision():
    signals, roads = adapt(read_table(SNAPSHOT)), read_table(ROADS)
    cleaned, heads, issues = run(signals, roads)

    coordinates = cleaned[["x", "y"]].apply(pd.to_numeric, errors="coerce")
    valid = coordinates.apply(
        lambda column: column.map(lambda value: pd.notna(value) and isfinite(value))
    ).all(axis=1)
    participating = cleaned.loc[valid & cleaned["dup_role"].ne("drop")]
    assert participating["match_tier"].notna().all()
    assert participating["match_method"].notna().all()
    assert participating["match_tier"].isin(["auto", "flag", "manual"]).all()
    assert (
        participating["match_method"]
        .isin(["named_crossing", "fallback_v1", "nearest_v1"])
        .all()
    )
    fallback = participating.loc[participating["match_method"].eq("fallback_v1")]
    assert not fallback["match_tier"].eq("auto").any()
    assert len(cleaned) == len(signals)
    dropped = set(cleaned.loc[cleaned["dup_role"].eq("drop"), "signal_gid"])
    assert not heads["signal_gid"].isin(dropped).any()
    assert cleaned.loc[cleaned["dup_role"].eq("drop"), "match_tier"].isna().all()
    assert issues["record_id"].isin(cleaned["signal_gid"]).all()

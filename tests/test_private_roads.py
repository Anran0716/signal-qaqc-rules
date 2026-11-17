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
from signal_qaqc.spatial import GAP_MAX_M, SEARCH_RADIUS_M

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
        .isin(["named_crossing", "road_gap", "fallback_v1", "nearest_v1"])
        .all()
    )
    fallback = participating.loc[participating["match_method"].eq("fallback_v1")]
    assert not fallback["match_tier"].eq("auto").any()
    assert len(cleaned) == len(signals)
    dropped = set(cleaned.loc[cleaned["dup_role"].eq("drop"), "signal_gid"])
    assert not heads["signal_gid"].isin(dropped).any()
    assert cleaned.loc[cleaned["dup_role"].eq("drop"), "match_tier"].isna().all()
    assert issues["record_id"].isin(cleaned["signal_gid"]).all()
    distinct = participating.loc[participating["dup_class"].eq("D_distinct")]
    assert distinct["match_scope"].eq("record").all()
    assert distinct["approach_distance_m"].isna().all()
    named = participating.loc[participating["match_method"].eq("named_crossing")]
    distances = pd.to_numeric(named["match_distance_m"], errors="coerce")
    finite = distances.map(lambda value: pd.notna(value) and isfinite(value))
    local = named.loc[finite]
    assert distances.loc[finite].le(SEARCH_RADIUS_M + 1e-6).all()
    assert local[["match_road_a", "match_road_b"]].notna().all().all()
    assert local["match_road_a"].ne(local["match_road_b"]).all()
    assert named.loc[~finite, "match_reason"].notna().all()
    gaps = participating.loc[participating["match_method"].eq("road_gap")]
    evidence_columns = [
        "match_gap_m",
        "match_road_a_distance_m",
        "match_road_b_distance_m",
    ]
    assert gaps[["match_road_a", "match_road_b", *evidence_columns]].notna().all().all()
    assert gaps["match_road_a"].ne(gaps["match_road_b"]).all()
    evidence = gaps[evidence_columns].astype(float)
    assert evidence.apply(lambda column: column.map(isfinite)).all().all()
    assert evidence.ge(0).all().all()
    small_nearby = evidence.le(GAP_MAX_M).all(axis=1)
    assert gaps.loc[small_nearby, "match_tier"].eq("flag").all()
    assert gaps.loc[~small_nearby, "match_tier"].eq("manual").all()
    assert gaps.loc[~small_nearby, "match_reason"].eq("named_roads_far_apart").all()
    gap_issues = issues.loc[issues["rule"].eq("road_gap")]
    assert set(gaps["signal_gid"]) == set(gap_issues["record_id"])
    office = gap_issues.loc[
        gap_issues["record_id"].isin(gaps.loc[small_nearby, "signal_gid"])
    ]
    manual = gap_issues.loc[
        gap_issues["record_id"].isin(gaps.loc[~small_nearby, "signal_gid"])
    ]
    assert office["severity"].eq("warning").all()
    assert office["action"].eq("review_in_office").all()
    assert manual["severity"].eq("error").all()
    assert manual["action"].eq("send_to_field").all()

"""Run required/XY, naming, duplicate, ID, location, and head rules in that order.

Checks inspect original answers before any naming changes. Standardized names
feed duplicate grouping; only then are IDs assigned. All source rows remain in
the cleaned table, but drop rows are excluded from location checks and heads.
Named crossings are checked at the mean of their active, valid direction points.
Direction distances from that mean are checked separately. Address-style records
and unverified fallbacks retain their individual nearest-road evidence.
D_distinct candidates are checked individually and never enter a shared mean.
Persist cleaned.attrs['last_number'] between batches; the CLI stores it in JSON.
"""

import argparse
import json
from math import isfinite
from pathlib import Path

import pandas as pd
from shapely import wkt
from shapely.geometry import Point

from signal_qaqc.checks import check_required, check_xy
from signal_qaqc.duplicates import mark_duplicates
from signal_qaqc.heads import explode_heads, reconcile
from signal_qaqc.ids import assign_signal_ids
from signal_qaqc.io import read_table
from signal_qaqc.naming import check_intersection_name, make_key
from signal_qaqc.report import exception, to_frame
from signal_qaqc.spatial import APPROACH_MAX_M, associate, build_road_index

REQUIRED_FIELDS = [
    "location",
    "travel_direction",
    "city",
    "preemption",
    "lon",
    "lat",
    "x",
    "y",
]
_DUP_COLUMNS = ["dup_group", "dup_class", "dup_role"]
_MATCH_COLUMNS = [
    "match_tier",
    "match_method",
    "match_road_key",
    "match_distance_m",
    "match_second_distance_m",
    "match_scope",
    "intersection_x",
    "intersection_y",
    "approach_distance_m",
    "match_road_a",
    "match_road_b",
    "match_reason",
    "match_gap_m",
]
_DERIVED_COLUMNS = [
    "location_std",
    "location_key",
    "name_status",
    *_DUP_COLUMNS,
    *_MATCH_COLUMNS,
]


def _road_index(roads):
    if isinstance(roads, pd.DataFrame):
        missing = {"road_name", "geometry_wkt"} - set(roads.columns)
        if missing:
            raise ValueError(f"Roads missing columns: {sorted(missing)}")
        roads = [
            (name, wkt.loads(geometry))
            for name, geometry in roads[["road_name", "geometry_wkt"]].itertuples(
                index=False, name=None
            )
        ]
    return build_road_index(roads)


def _duplicates(frame, valid_points, business_columns):
    # Invalid points already have required/XY issues; do not invent a distance or
    # merge them into a duplicate group. Keep those records available for review.
    marked = mark_duplicates(frame.loc[valid_points], attribute_cols=business_columns)
    for column in _DUP_COLUMNS:
        frame[column] = pd.array(
            ["review" if column == "dup_role" else ""] * len(frame), dtype=object
        )
        frame.loc[valid_points, column] = marked[column].to_numpy()
    issues = []
    for row in frame.itertuples(index=False):
        if not row.dup_class or row.dup_role == "keep":
            continue
        if row.dup_class == "A_exact":
            severity, action = "info", "auto_fixed"
        elif row.dup_class == "D_distinct":
            severity, action = "error", "send_to_field"
        else:
            severity, action = "warning", "review"
        issues.append(
            exception(
                row.signal_gid,
                "duplicate_record",
                "location_std,travel_direction",
                severity,
                action,
                f"{row.dup_group}: {row.dup_class}; role={row.dup_role}. "
                "Source row retained; verify candidates before deleting.",
            )
        )
    return issues


def _location_matches(frame, numeric, valid_points, road_index):
    """Share named-crossing decisions, keeping direction-level outliers visible."""
    rows = list(frame.itertuples(index=False))
    points, groups, decisions = {}, {}, {}
    for position, row in enumerate(rows):
        if row.dup_role == "drop" or not valid_points.iloc[position]:
            continue
        point = Point(numeric.iloc[position]["x"], numeric.iloc[position]["y"])
        points[position] = point
        if (
            row.dup_class == "D_distinct"
            or row.name_status == "NOT_INTERSECTION"
            or not row.location_key
        ):
            decisions[position] = (
                associate(point, row.location_std, road_index),
                "record",
                None,
                None,
            )
        else:
            groups.setdefault(row.location_key, []).append(position)

    for positions in groups.values():
        first = positions[0]
        center = Point(
            sum(points[position].x for position in positions) / len(positions),
            sum(points[position].y for position in positions) / len(positions),
        )
        match = associate(center, rows[first].location_std, road_index)
        if match.method not in {"named_crossing", "road_gap"}:
            # Unverified names/roads keep their original per-record evidence.
            for position in positions:
                individual = (
                    match
                    if len(positions) == 1
                    else associate(
                        points[position], rows[position].location_std, road_index
                    )
                )
                decisions[position] = (individual, "record", None, None)
            continue
        for position in positions:
            approach = points[position].distance(center) if len(positions) > 1 else None
            decisions[position] = (match, "intersection", center, approach)

    matches, issues = [], []
    for position, row in enumerate(rows):
        if row.dup_role == "drop":
            matches.append([None] * len(_MATCH_COLUMNS))
            continue
        if not valid_points.iloc[position]:
            invalid = [None] * len(_MATCH_COLUMNS)
            invalid[1] = "invalid_point"
            matches.append(invalid)
            continue
        match, scope, center, approach = decisions[position]
        matches.append(
            [
                match.tier,
                match.method,
                match.road_id,
                match.distance_m,
                match.second_distance_m,
                scope,
                center.x if center is not None else None,
                center.y if center is not None else None,
                approach,
                match.road_keys[0] if match.road_keys else None,
                match.road_keys[1] if match.road_keys else None,
                match.reason,
                match.gap_m,
            ]
        )
        if match.tier != "auto":
            rule, field = "road_association", "x,y"
            severity, action = (
                ("warning", "review")
                if match.tier == "flag"
                else ("error", "send_to_field")
            )
            distance = (
                f"{match.distance_m:.3f} m"
                if isfinite(match.distance_m)
                else "unavailable"
            )
            detail = (
                f"scope={scope}; {match.method}: {match.tier}; distance={distance}; "
                f"candidate={match.road_id}; roads={match.road_keys}; "
                f"reason={match.reason}"
            )
            if match.method == "road_gap":
                rule, field = "road_gap", "location_std"
                severity, action = "warning", "review_in_office"
                detail = (
                    f"scope={scope}; road layer {match.road_keys} does not intersect; "
                    f"nearest road gap={match.gap_m:.3f} m. "
                    "Review the road network in the office."
                )
            elif match.reason == "same_road_on_both_sides":
                rule, field = "intersection_name", "location_std"
                severity, action = "warning", "review_in_office"
                detail = (
                    "Both sides resolve to the same road key; verify aliases or "
                    "frontage-road naming in the office. Nearest-road fallback "
                    f"evidence: distance={distance}; candidate={match.road_id}."
                )
            issues.append(
                exception(
                    row.signal_gid,
                    rule,
                    field,
                    severity,
                    action,
                    detail,
                )
            )
        if approach is not None and approach > APPROACH_MAX_M:
            issues.append(
                exception(
                    row.signal_gid,
                    "approach_distance",
                    "x,y",
                    "warning",
                    "review",
                    f"Direction point is {approach:.3f} m from the intersection mean "
                    f"(limit {APPROACH_MAX_M:g} m); "
                    "confirm travel direction or location.",
                )
            )
    return matches, issues


def run(signals, roads, delivered_heads=None, *, last_number=0):
    """Return (cleaned Signals, freshly derived heads, severity-sorted exceptions).

    Signals use location/travel_direction/city/preemption/lon/lat/x/y plus a
    unique nonblank signal_gid and head_{n}_{type,left,right} columns. SignalID
    and edit_date are optional. Roads are a name-to-geometry mapping, pairs, or
    a CSV-shaped DataFrame with road_name/geometry_wkt in EPSG:26914 meters.
    Empty coordinates belong to required_field; downstream geometric rules skip
    them. A missing direction is reported but remains a safe empty grouping key.
    Named intersections share one mean-point decision. match_scope identifies
    that decision; intersection_x/y and approach_distance_m expose its evidence.
    Single-record intersections have no direction-layer comparison.
    """
    missing = {"signal_gid", *REQUIRED_FIELDS} - set(signals.columns)
    if missing:
        raise ValueError(f"Signals missing columns: {sorted(missing)}")
    gids = signals["signal_gid"].fillna("").astype(str).str.strip()
    if gids.eq("").any() or gids.duplicated().any():
        raise ValueError("signal_gid must be nonblank and unique")
    frame = signals.drop(columns=_DERIVED_COLUMNS, errors="ignore").copy(deep=True)
    frame["signal_gid"] = pd.array(gids, dtype=object)
    business_columns = frame.columns.tolist()
    issues = check_required(frame, REQUIRED_FIELDS) + check_xy(frame)

    names = [check_intersection_name(value) for value in frame["location"]]
    frame["location_std"] = pd.array(
        [
            name.proposed if name.status in {"FIX_FORMAT", "FIX_ORDER"} else original
            for original, name in zip(frame["location"], names, strict=True)
        ],
        dtype=object,
    )
    frame["name_status"] = pd.array([name.status for name in names], dtype=object)
    frame["location_key"] = frame["location_std"].map(make_key)
    for gid, name in zip(frame["signal_gid"], names, strict=True):
        if name.status in {"FIX_FORMAT", "FIX_ORDER", "REVIEW"}:
            fixed = name.status != "REVIEW"
            issues.append(
                exception(
                    gid,
                    "intersection_name",
                    "location",
                    "info" if fixed else "warning",
                    "auto_fixed" if fixed else "review",
                    f"{name.status}: {name.reason}"
                    + (f"; proposed={name.proposed}" if fixed else ""),
                )
            )

    numeric = frame[["x", "y"]].apply(pd.to_numeric, errors="coerce")
    valid_points = numeric.apply(
        lambda column: column.map(lambda value: pd.notna(value) and isfinite(value))
    ).all(axis=1)
    issues.extend(_duplicates(frame, valid_points, business_columns))
    frame, id_issues, last_number = assign_signal_ids(frame, last_number=last_number)
    issues.extend(id_issues)

    road_index = _road_index(roads)
    matches, location_issues = _location_matches(
        frame, numeric, valid_points, road_index
    )
    issues.extend(location_issues)
    for index, column in enumerate(_MATCH_COLUMNS):
        frame[column] = pd.array([match[index] for match in matches], dtype=object)

    heads, head_issues = explode_heads(frame)
    issues.extend(head_issues)
    if delivered_heads is not None:
        dropped = set(frame.loc[frame["dup_role"].eq("drop"), "signal_gid"])
        issues.extend(
            reconcile(heads, delivered_heads, set(frame["signal_gid"]), dropped)
        )
    frame.attrs["last_number"] = last_number
    return frame, heads, to_frame(issues)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run traffic signal QA/QC in local EPSG:26914 meters."
    )
    parser.add_argument("signals", type=Path)
    parser.add_argument("roads", type=Path)
    parser.add_argument("--heads", type=Path, help="Optional delivered head CSV")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory (default: beside signals, in output/)",
    )
    parser.add_argument(
        "--id-state",
        type=Path,
        help="Persistent ID state (default: output/id_state.json)",
    )
    args = parser.parse_args(argv)
    output = args.output or args.signals.parent / "output"
    state_path = args.id_state or output / "id_state.json"
    try:
        last_number = (
            json.loads(state_path.read_text(encoding="utf-8"))["last_number"]
            if state_path.exists()
            else 0
        )
        cleaned, heads, issues = run(
            read_table(args.signals),
            read_table(args.roads),
            read_table(args.heads) if args.heads else None,
            last_number=last_number,
        )
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    output.mkdir(parents=True, exist_ok=True)
    for name, table in [
        ("signals_clean", cleaned),
        ("heads_expected", heads),
        ("exceptions", issues),
    ]:
        table.to_csv(output / f"{name}.csv", index=False)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"last_number": cleaned.attrs["last_number"]}) + "\n",
        encoding="utf-8",
    )
    counts = issues["severity"].value_counts()
    print(
        f"{len(cleaned)} signals, {len(heads)} heads, {len(issues)} exceptions "
        f"(error {counts.get('error', 0)}, warning {counts.get('warning', 0)}, "
        f"info {counts.get('info', 0)}) -> {output}"
    )


if __name__ == "__main__":
    main()

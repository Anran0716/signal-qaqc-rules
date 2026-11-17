"""Annotate duplicate candidates without deleting or changing source records.

Groups use make_key(location_std) and normalized travel direction. Distances
use the farthest pair in EPSG:26914 meters. Only A_exact permits a drop label;
B_near and C_conflict propose a keeper and leave the other records for review.
B_far and D_distinct leave every record for review. Missing names are not grouped.
"""

import re
from itertools import combinations
from math import hypot, isfinite

import pandas as pd

from signal_qaqc.naming import make_key

_ANNOTATIONS = ["dup_group", "dup_class", "dup_role"]
_METADATA = {
    "globalid",
    "signalgid",
    "signalid",
    "x",
    "y",
    "lon",
    "lat",
    "longitude",
    "latitude",
    "geometry",
    "geometrywkt",
    "editdate",
    "location",
    "locationstd",
    "namestatus",
    "dupgroup",
    "dupclass",
    "duprole",
}


def _blank(value):
    return pd.isna(value) or (isinstance(value, str) and not value.strip())


def _column_key(column):
    return re.sub(r"[^a-z0-9]", "", str(column).casefold())


def _classify_group(same_attributes: bool, distance: float) -> str:
    """Classify a group using attribute equality and its maximum pair distance."""
    if not same_attributes:
        return "C_conflict" if distance <= 15 else "D_distinct"
    if distance <= 1:
        return "A_exact"
    return "B_near" if distance <= 15 else "B_far"


def _maximum_distance(df, positions, x_col, y_col):
    points = []
    for position in positions:
        try:
            x = float(df.iloc[position][x_col])
            y = float(df.iloc[position][y_col])
        except (TypeError, ValueError) as error:
            raise ValueError(
                "Duplicate coordinates must be finite numeric meters"
            ) from error
        if not (isfinite(x) and isfinite(y)):
            raise ValueError("Duplicate coordinates must be finite numeric meters")
        points.append((x, y))
    return max(hypot(a[0] - b[0], a[1] - b[1]) for a, b in combinations(points, 2))


def mark_duplicates(
    df: pd.DataFrame,
    *,
    location_col: str = "location_std",
    direction_col: str = "travel_direction",
    id_col: str = "signal_gid",
    x_col: str = "x",
    y_col: str = "y",
    edit_col: str = "edit_date",
    attribute_cols: list[str] | None = None,
) -> pd.DataFrame:
    """Add only dup_group, dup_class and dup_role, retaining input rows and order.

    Keeper priority is completeness, newest edit date, then first file position.
    All non-metadata columns are compared unless attribute_cols is supplied.
    Invalid coordinates in a candidate group must be repaired before classification.
    """
    required = [location_col, direction_col, x_col, y_col]
    if attribute_cols is not None:
        required.extend(attribute_cols)
    missing = sorted(set(required).difference(df.columns))
    if missing:
        raise ValueError(f"Missing columns: {', '.join(missing)}")

    excluded = _METADATA | {
        _column_key(column)
        for column in [location_col, direction_col, id_col, x_col, y_col, edit_col]
    }
    attributes = [
        column
        for column in (df.columns if attribute_cols is None else attribute_cols)
        if _column_key(column) not in excluded
    ]
    values = (
        list(df[attributes].itertuples(index=False, name=None))
        if attributes
        else [()] * len(df)
    )
    signatures = [
        tuple(None if _blank(value) else value for value in row) for row in values
    ]
    completeness = [sum(not _blank(value) for value in row) for row in values]
    if edit_col in df.columns:
        dates = pd.to_datetime(df[edit_col], errors="coerce", format="mixed", utc=True)
        timestamps = [date.value if pd.notna(date) else float("-inf") for date in dates]
    else:
        timestamps = [float("-inf")] * len(df)

    names = df[location_col].map(make_key)
    directions = df[direction_col].fillna("").astype(str).str.strip().str.upper()
    groups = {}
    group_labels = [""] * len(df)
    classes = [""] * len(df)
    roles = ["keep"] * len(df)
    for position, key in enumerate(zip(names, directions, strict=True)):
        if not key[0]:
            roles[position] = "review"
            continue
        groups.setdefault(key, []).append(position)

    group_number = 0
    for positions in groups.values():
        if len(positions) < 2:
            continue
        group_number += 1
        same = all(
            signatures[position] == signatures[positions[0]] for position in positions
        )
        distance = _maximum_distance(df, positions, x_col, y_col)
        category = _classify_group(same, distance)
        for position in positions:
            group_labels[position] = f"DUP-{group_number:05d}"
            classes[position] = category
            roles[position] = "review"
        if category in {"A_exact", "B_near", "C_conflict"}:
            keeper = max(
                positions,
                key=lambda position: (
                    completeness[position],
                    timestamps[position],
                    -position,
                ),
            )
            for position in positions:
                roles[position] = (
                    "keep"
                    if position == keeper
                    else ("drop" if category == "A_exact" else "review")
                )

    result = df.copy(deep=True)
    for column, data in zip(_ANNOTATIONS, [group_labels, classes, roles], strict=True):
        result[column] = pd.array(data, dtype=object)
    return result

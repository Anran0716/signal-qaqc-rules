"""Rederive signal heads from the current master and reconcile a delivered table.

Head-only placeholders such as ``NA`` and ``null`` are empty; this does not
change the IO rule that preserves a literal ``None`` in other inventory fields.
Positions come from column names, including positions beyond three. Dropped
parents are excluded, and gaps or turns without a head type produce warnings.
"""

import re
from functools import partial

import pandas as pd

from signal_qaqc.report import exception

HEAD_COLUMNS = ["signal_gid", "position", "head_type", "left_turn", "right_turn"]
KEY_COLUMNS = ["signal_gid", "position"]
VALUE_COLUMNS = ["head_type", "left_turn", "right_turn"]
_HEAD_FIELD = re.compile(r"^head_(\d+)_(type|left|right)$", re.IGNORECASE)
_EMPTY = frozenset({"", "na", "n/a", "null", "none"})
_TURN_ALIASES = {
    "protected": "Protected",
    "protected left turn (green arrow)": "Protected",
    "protected right turn (green arrow)": "Protected",
    "permissive": "Permissive",
    "permissive left turn (green circle)": "Permissive",
    "permissive right turn (green circle)": "Permissive",
    "turn not allow": "Turn Not Allowed",
    "turn not allowed": "Turn Not Allowed",
}


def _head_columns(columns):
    fields = {}
    for column in columns:
        match = _HEAD_FIELD.fullmatch(str(column))
        if match is None:
            continue
        position, component = int(match[1]), match[2].lower()
        key = (position, component)
        if position < 1 or key in fields:
            raise ValueError(f"Invalid or ambiguous head column: {column!r}")
        fields[key] = column
    return fields


def detect_positions(columns) -> list[int]:
    """Return sorted positions found in any head type, left, or right column."""
    return sorted({position for position, _ in _head_columns(columns)})


def _normalize(value, *, turn=False):
    if pd.isna(value):
        return None
    text = " ".join(str(value).split())
    if text.casefold() in _EMPTY:
        return None
    return _TURN_ALIASES.get(text.casefold(), text) if turn else text


def _gid(value):
    if pd.isna(value) or not str(value).strip():
        raise ValueError("signal_gid must be nonempty")
    return str(value).strip()


def _issue(gid, position, kind, severity, detail, *, field="position"):
    return exception(
        gid,
        f"head_{kind}",
        field,
        severity,
        "none" if kind == "dropped" else "review",
        f"Head position {position}: {detail}",
    )


def explode_heads(signals) -> tuple[pd.DataFrame, list[dict]]:
    """Return the current long head table and warnings, without editing signals.

    The long schema is signal_gid, position, head_type, left_turn, right_turn.
    A head needs a type; a later occupied position is retained even after a gap.
    Active parent IDs must be unique so the output key identifies one head.
    """
    if "signal_gid" not in signals.columns:
        raise ValueError("signals is missing signal_gid")
    positions = detect_positions(signals.columns)
    if not positions:
        raise ValueError("signals has no head columns")
    fields = _head_columns(signals.columns)
    rows, issues, seen = [], [], set()
    for parent in signals.to_dict(orient="records"):
        if parent.get("dup_role") == "drop":
            continue
        gid = _gid(parent["signal_gid"])
        if gid in seen:
            raise ValueError(f"Duplicate active signal_gid: {gid!r}")
        seen.add(gid)
        occupied = 0
        for position in positions:
            values = [
                _normalize(parent.get(fields.get((position, component))), turn=turn)
                for component, turn in [
                    ("type", False),
                    ("left", True),
                    ("right", True),
                ]
            ]
            head_type, left_turn, right_turn = values
            if head_type is None:
                if left_turn is not None or right_turn is not None:
                    issues.append(
                        _issue(
                            gid,
                            position,
                            "turn_without_type",
                            "warning",
                            "turn value exists without a head type; no head derived.",
                            field=f"head_{position}_type",
                        )
                    )
                continue
            occupied += 1
            if position != occupied:
                issues.append(
                    _issue(
                        gid,
                        position,
                        "position_gap",
                        "warning",
                        "an earlier head position is empty; this head is retained.",
                        field=f"head_{position}_type",
                    )
                )
            rows.append([gid, position, head_type, left_turn, right_turn])
    return pd.DataFrame(rows, columns=HEAD_COLUMNS), issues


def _position(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid head position: {value!r}") from None
    if isinstance(value, bool) or not number.is_integer() or number < 1:
        raise ValueError(f"Invalid head position: {value!r}")
    return int(number)


def _prepare(table, label):
    missing = set(HEAD_COLUMNS) - set(table.columns)
    if missing:
        raise ValueError(f"{label} is missing columns: {sorted(missing)}")
    result = table.loc[:, HEAD_COLUMNS].copy()
    result["signal_gid"] = result["signal_gid"].map(_gid).astype(object)
    result["position"] = result["position"].map(_position).astype("int64")
    for column in VALUE_COLUMNS:
        result[column] = result[column].map(
            partial(_normalize, turn=column != "head_type")
        )
    return result


def reconcile(expected, delivered, master_ids, dropped_ids) -> list[dict]:
    """Report duplicate, missing, stale, orphan, dropped, and changed heads.

    Match on (signal_gid, position), after normalizing head values. Dropped
    parent IDs take precedence over orphan IDs. Duplicate delivery keys get one
    error per key; every copy is still checked for conflicting values. Expected
    heads must come from active master records and have unique keys.
    """
    expected = _prepare(expected, "expected")
    delivered = _prepare(delivered, "delivered")
    master_ids = {_gid(value) for value in master_ids}
    dropped_ids = {_gid(value) for value in dropped_ids}
    if expected.duplicated(KEY_COLUMNS).any():
        raise ValueError("expected has duplicate (signal_gid, position) keys")
    if not set(expected["signal_gid"]) <= master_ids - dropped_ids:
        raise ValueError("expected contains a parent outside the active master")

    issues = []
    for (gid, position), count in (
        delivered.groupby(KEY_COLUMNS, sort=False).size().items()
    ):
        if count > 1:
            issues.append(
                _issue(
                    gid,
                    position,
                    "duplicate",
                    "error",
                    f"{count} delivered rows share this key.",
                )
            )

    comparison = expected.merge(
        delivered,
        on=KEY_COLUMNS,
        how="outer",
        suffixes=("_expected", "_delivered"),
        indicator="source",
        validate="one_to_many",
        sort=False,
    )
    presence_seen, changed = set(), {}
    for row in comparison.itertuples(index=False):
        gid, position = row.signal_gid, row.position
        key = (gid, position)
        if row.source != "both":
            if key in presence_seen:
                continue
            presence_seen.add(key)
            if row.source == "left_only":
                kind, severity, detail = (
                    "missing",
                    "error",
                    "master head is absent from delivery.",
                )
            elif gid in dropped_ids:
                kind, severity, detail = (
                    "dropped",
                    "info",
                    "parent was marked drop; exclude this head.",
                )
            elif gid not in master_ids:
                kind, severity, detail = (
                    "orphan",
                    "warning",
                    "parent does not exist in the master.",
                )
            else:
                kind, severity, detail = (
                    "stale",
                    "warning",
                    "this head no longer exists in the master.",
                )
            issues.append(_issue(gid, position, kind, severity, detail))
            continue
        for column in VALUE_COLUMNS:
            original = _normalize(
                getattr(row, f"{column}_expected"), turn=column != "head_type"
            )
            supplied = _normalize(
                getattr(row, f"{column}_delivered"), turn=column != "head_type"
            )
            if original != supplied:
                changed.setdefault(key, set()).add(column)
    for (gid, position), differing in changed.items():
        columns = [column for column in VALUE_COLUMNS if column in differing]
        issues.append(
            _issue(
                gid,
                position,
                "changed",
                "warning",
                f"values differ from the master in {', '.join(columns)}.",
                field=", ".join(columns),
            )
        )
    return issues

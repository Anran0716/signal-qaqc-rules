"""Assign opaque, permanent SignalIDs to individual non-drop Signals records.

New IDs use SIG-00001 through SIG-99999, independent of road, city or direction.
Run duplicate marking first. Preserve the returned last_number between batches,
including after records are removed, so historical numbers are never recycled.
Valid IDs on drop rows also reserve their numbers, but drop rows are unchanged.
"""

import re
from numbers import Integral

import pandas as pd

from signal_qaqc.report import exception

PREFIX, WIDTH = "SIG-", 5
_VALID = re.compile(rf"^{re.escape(PREFIX)}(\d{{{WIDTH}}})$", re.ASCII)
_MAX_NUMBER = 10**WIDTH - 1


def _number(value):
    match = _VALID.fullmatch(value) if isinstance(value, str) else None
    return int(match.group(1)) if match else None


def _blank(value):
    return pd.isna(value) or (isinstance(value, str) and not value.strip())


def assign_signal_ids(
    df: pd.DataFrame,
    *,
    id_col: str = "SignalID",
    gid_col: str = "signal_gid",
    role_col: str = "dup_role",
    last_number: int = 0,
) -> tuple[pd.DataFrame, list[dict], int]:
    """Return a copy, exception rows, and the updated last used number.

    Keep the first active copy of each valid ID; replace later copies and invalid
    IDs, recording each repair. Missing IDs are normal new allocations. Persist
    the returned number and supply it as last_number on subsequent batches.
    """
    if (
        isinstance(last_number, bool)
        or not isinstance(last_number, Integral)
        or not 0 <= last_number <= _MAX_NUMBER
    ):
        raise ValueError(f"last_number must be an integer from 0 to {_MAX_NUMBER}")
    missing = sorted({gid_col, role_col}.difference(df.columns))
    if missing:
        raise ValueError(
            f"Missing columns: {', '.join(missing)}; mark duplicates first"
        )
    roles = df[role_col].tolist()
    if any(
        not isinstance(role, str) or role not in {"keep", "drop", "review"}
        for role in roles
    ):
        raise ValueError(f"{role_col} must contain only keep, drop or review")

    ids = df[id_col].tolist() if id_col in df.columns else [""] * len(df)
    numbers = [_number(value) for value in ids]
    next_number = max(
        int(last_number), max((n for n in numbers if n is not None), default=0)
    )
    seen = set()
    issues = []
    for position, (record_id, role) in enumerate(zip(df[gid_col], roles, strict=True)):
        if role == "drop":
            continue
        old_id = ids[position]
        number = numbers[position]
        if number is not None and old_id not in seen:
            seen.add(old_id)
            continue
        next_number += 1
        if next_number > _MAX_NUMBER:
            raise ValueError("SignalID number space exhausted")
        new_id = f"{PREFIX}{next_number:0{WIDTH}d}"
        ids[position] = new_id
        seen.add(new_id)
        if not _blank(old_id):
            rule = "duplicate_signal_id" if number is not None else "invalid_signal_id"
            issues.append(
                exception(
                    record_id,
                    rule,
                    id_col,
                    "info",
                    "auto_fixed",
                    f"Replaced {old_id!r} with {new_id}",
                )
            )

    result = df.copy(deep=True)
    result[id_col] = pd.array(ids, dtype=object)
    return result, issues, next_number

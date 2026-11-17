"""Create a consistent exception table with one row per detected problem."""

import pandas as pd

COLUMNS = ["record_id", "rule", "field", "severity", "action", "detail"]
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
ACTIONS = frozenset(
    {"auto_fixed", "review", "review_in_office", "send_to_field", "none"}
)


def exception(record_id, rule, field, severity, action, detail) -> dict:
    """Build one exception row, rejecting unsupported severities and actions."""
    if severity not in SEVERITY_ORDER:
        raise ValueError(f"Unknown severity: {severity!r}")
    if action not in ACTIONS:
        raise ValueError(f"Unknown action: {action!r}")

    return {
        "record_id": record_id,
        "rule": rule,
        "field": field,
        "severity": severity,
        "action": action,
        "detail": detail,
    }


def to_frame(rows) -> pd.DataFrame:
    """Order issues by severity, keeping ties in their original order."""
    table = pd.DataFrame(rows, columns=COLUMNS)
    return table.sort_values(
        "severity",
        key=lambda severity: severity.map(SEVERITY_ORDER),
        kind="stable",
        ignore_index=True,
    )

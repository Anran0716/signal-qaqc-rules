"""Check required answers and agreement between geographic and local coordinates.

Defaults use signal_gid, WGS84 lon/lat degrees, and EPSG:26914 x/y meters.
Checks return exception rows and leave the input table unchanged.
"""

from math import hypot, isfinite

import pandas as pd
from pyproj.exceptions import ProjError

from signal_qaqc.geo import to_local
from signal_qaqc.report import exception

XY_WARNING_M = 1.0
XY_ERROR_M = 15.0


def _require_columns(df, columns):
    missing = sorted(set(columns).difference(df.columns))
    if missing:
        raise ValueError(f"Missing columns: {', '.join(missing)}")


def check_required(
    df: pd.DataFrame, fields: list[str], id_col: str = "signal_gid"
) -> list[dict]:
    """Flag null or whitespace-only answers; reject missing columns with ValueError."""
    _require_columns(df, [id_col, *fields])
    issues = []
    for field in fields:
        values = df[field]
        blank = values.isna() | values.astype(str).str.strip().eq("")
        for record_id in df.loc[blank, id_col]:
            issues.append(
                exception(
                    record_id,
                    "required_field",
                    field,
                    "error",
                    "send_to_field",
                    f"{field} is missing or blank",
                )
            )
    return issues


def check_xy(
    df: pd.DataFrame,
    *,
    id_col: str = "signal_gid",
    lon_col: str = "lon",
    lat_col: str = "lat",
    x_col: str = "x",
    y_col: str = "y",
) -> list[dict]:
    """Flag discrepancies above 1 m as warnings and above 15 m as errors.

    Missing columns raise ValueError; invalid coordinate values become error rows.
    """
    coordinate_columns = [lon_col, lat_col, x_col, y_col]
    _require_columns(df, [id_col, *coordinate_columns])
    issues = []
    for record_id, *coordinates in df[[id_col, *coordinate_columns]].itertuples(
        index=False, name=None
    ):
        try:
            lon, lat, x, y = (float(value) for value in coordinates)
            if not all(isfinite(value) for value in (lon, lat, x, y)):
                raise ValueError("Coordinates must be finite numbers")
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError(
                    f"{lon_col} must be within [-180, 180] "
                    f"and {lat_col} within [-90, 90]"
                )
            expected_x, expected_y = to_local(lon, lat)
        except (TypeError, ValueError, ProjError) as error:
            issues.append(
                exception(
                    record_id,
                    "xy_consistency",
                    ",".join(coordinate_columns),
                    "error",
                    "send_to_field",
                    f"Invalid coordinate values: {error}",
                )
            )
            continue

        distance = hypot(x - expected_x, y - expected_y)
        if distance > XY_ERROR_M:
            severity, action = "error", "send_to_field"
        elif distance > XY_WARNING_M:
            severity, action = "warning", "review"
        else:
            continue
        issues.append(
            exception(
                record_id,
                "xy_consistency",
                f"{x_col},{y_col}",
                severity,
                action,
                f"{lon_col}/{lat_col} and {x_col}/{y_col} differ by {distance:.6f} m",
            )
        )
    return issues

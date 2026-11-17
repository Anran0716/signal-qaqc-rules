"""Adapt the local AGOL/shapefile CSV export without replacing observed lon/lat.

geometry_wkt must contain EPSG:3857 points; x/y are projected to EPSG:26914.
The original Longitude/Latitude become lon/lat unchanged, so a bad coordinate
answer cannot be hidden by recalculating both representations from the geometry.
"""

import re
from math import isfinite

from pyproj import Transformer
from shapely import wkt
from shapely.geometry import Point

_TO_LOCAL = Transformer.from_crs(3857, 26914, always_xy=True)
_FIELDS = {
    "GlobalID": "signal_gid",
    "Location": "location",
    "TravelDire": "travel_direction",
    "City": "city",
    "EmergencyF": "preemption",
    "Longitude": "lon",
    "Latitude": "lat",
    "EditDate": "edit_date",
}
_HEAD = re.compile(r"^Signal_(\d+)_(H|L|R)$", re.IGNORECASE)


def adapt(export):
    """Return a canonical copy of a Web Mercator master CSV; detect every head."""
    rename = dict(_FIELDS)
    for column in export.columns:
        if match := _HEAD.fullmatch(str(column)):
            component = {"H": "type", "L": "left", "R": "right"}[match[2].upper()]
            rename[column] = f"head_{int(match[1])}_{component}"
    result = export.rename(columns=rename).copy(deep=True)
    required = {
        "signal_gid",
        "location",
        "travel_direction",
        "city",
        "preemption",
        "lon",
        "lat",
        "geometry_wkt",
    }
    missing = required - set(result.columns)
    if missing:
        raise ValueError(f"Master export missing columns: {sorted(missing)}")
    if not result.columns.is_unique:
        raise ValueError("Master export has colliding column aliases")
    points = []
    for value in result["geometry_wkt"]:
        point = wkt.loads(value)
        if not isinstance(point, Point) or point.is_empty or not point.is_valid:
            raise ValueError("Master geometry must be a finite Web Mercator point")
        x, y = _TO_LOCAL.transform(point.x, point.y, errcheck=True)
        if not (isfinite(x) and isfinite(y)):
            raise ValueError("Master geometry could not be projected to local meters")
        points.append((x, y))
    result["x"] = [point[0] for point in points]
    result["y"] = [point[1] for point in points]
    return result

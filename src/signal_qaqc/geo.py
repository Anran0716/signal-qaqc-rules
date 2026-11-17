"""Convert WGS84 longitude/latitude to NAD83 / UTM zone 14N meters and back.

Local coordinates use EPSG:26914, matching the San Antonio QA working CRS.
Geographic arguments and return values always follow (longitude, latitude).
"""

from pyproj import Transformer
from pyproj.enums import TransformDirection

_TRANSFORMER = Transformer.from_crs("EPSG:4326", "EPSG:26914", always_xy=True)


def to_local(lon, lat):
    """Convert WGS84 (longitude, latitude) degrees to local (x, y) meters."""
    return _TRANSFORMER.transform(lon, lat, errcheck=True)


def to_lonlat(x, y):
    """Convert local (x, y) meters back to WGS84 (longitude, latitude) degrees."""
    return _TRANSFORMER.transform(
        x, y, direction=TransformDirection.INVERSE, errcheck=True
    )

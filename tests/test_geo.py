import pytest

from signal_qaqc.geo import to_local, to_lonlat


@pytest.mark.parametrize(
    ("lon", "lat"),
    [
        pytest.param(-98.4936, 29.4241, id="san_antonio"),
        pytest.param(-98.7319, 29.7947, id="boerne"),
        pytest.param(-97.9647, 29.5688, id="seguin"),
    ],
)
def test_local_and_lonlat_round_trip_preserves_coordinates(lon, lat):
    x, y = to_local(lon, lat)

    assert to_lonlat(x, y) == pytest.approx((lon, lat), rel=0, abs=1e-8)


def test_to_local_uses_utm_zone_14_meter_coordinates():
    # The central meridian of UTM zone 14 is 99 W, with 500,000 m false easting.
    x, y = to_local(-99.0, 30.0)

    # Allow for datum differences between the WGS84 input and NAD83 output.
    assert x == pytest.approx(500_000.0, rel=0, abs=10)
    assert y > 0

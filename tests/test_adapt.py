import pandas as pd
import pytest
from pyproj import Transformer
from shapely.geometry import Point

from signal_qaqc.adapt import adapt
from signal_qaqc.geo import to_lonlat


def test_adapter_maps_all_head_positions_and_projects_geometry_independently():
    lon, lat = to_lonlat(500_000, 3_300_000)
    web = Transformer.from_crs(26914, 3857, always_xy=True)
    source = pd.DataFrame(
        [
            {
                "GlobalID": "fictional-1",
                "Location": "IH10@FM100",
                "TravelDire": pd.NA,
                "City": "Fictional City",
                "EmergencyF": "None",
                "Longitude": lon + 0.01,
                "Latitude": lat,
                "geometry_wkt": Point(*web.transform(500_000, 3_300_000)).wkt,
                "Signal_1_H": "3-Light",
                "Signal_4_H": "4-Light",
                "Signal_4_L": "Protected",
                "Signal_5_R": "NA",
            }
        ]
    )
    before = source.copy(deep=True)

    result = adapt(source)

    pd.testing.assert_frame_equal(source, before)
    assert result.loc[0, "x"] == pytest.approx(500_000, abs=0.001)
    assert result.loc[0, "y"] == pytest.approx(3_300_000, abs=0.001)
    assert result["lon"].equals(source["Longitude"])
    assert result["lat"].equals(source["Latitude"])
    assert pd.isna(result.loc[0, "travel_direction"])
    assert result.loc[0, "preemption"] == "None"
    assert result.loc[0, "head_4_type"] == "4-Light"
    assert result.loc[0, "head_5_right"] == "NA"

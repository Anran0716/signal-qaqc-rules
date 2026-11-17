"""Exercise the standalone preparation script with fictional roads only."""

import runpy
from pathlib import Path

import pandas as pd
import pytest
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import LineString
from shapely.ops import transform


@pytest.fixture(scope="module")
def prep():
    script = Path(__file__).resolve().parents[1] / "scripts/prepare_private_roads.py"
    return runpy.run_path(str(script))


@pytest.mark.parametrize(
    ("system", "number", "street", "expected"),
    [
        ("FM", "0100", "", "FM 100"),
        ("IH", "0010", "ignored local alias", "IH 10"),
        ("LS", "", "Fictional Oak Rd", "Fictional Oak Rd"),
        ("CR", "", "Fictional Elm Rd", "Fictional Elm Rd"),
        ("FD", "", "Fictional Access Rd", "Fictional Access Rd"),
        ("LS", "", "  ", None),
    ],
)
def test_road_names_follow_export_system_and_number(
    prep, system, number, street, expected
):
    assert prep["road_name"](system, number, street) == expected


def test_preparation_projects_and_keeps_crossing_segments_with_outside_endpoints(
    prep, tmp_path
):
    x, y = 500_000.0, 3_300_000.0
    web = Transformer.from_crs(26914, 3857, always_xy=True)
    lines = [
        LineString([(x - 20, y), (x + 20, y)]),
        LineString([(x - 10_000, y + 50), (x + 2_000, y + 50)]),
        LineString([(x + 10_000, y), (x + 11_000, y)]),
        LineString([(x, y), (x + 20, y)]),
    ]
    source = pd.DataFrame(
        {
            "HSYS": ["FM", "LS", "FM", "LS"],
            "HNUM": ["0100", "", "0200", ""],
            "STE_NAM": ["", "Fictional Oak Rd", "", ""],
            "geometry_wkt": [transform(web.transform, line).wkt for line in lines],
        }
    )
    source_path, output = tmp_path / "source.csv", tmp_path / "roads.csv"
    source.to_csv(source_path, index=False)

    roads, stats = prep["prepare_roads"](
        source_path,
        pd.DataFrame({"x": [x], "y": [y]}),
        output,
        buffer_m=1_000,
    )

    assert roads.columns.tolist() == ["road_name", "geometry_wkt"]
    assert roads["road_name"].tolist() == ["FM 100", "Fictional Oak Rd"]
    assert wkt.loads(roads.loc[0, "geometry_wkt"]).hausdorff_distance(lines[0]) < 0.001
    assert wkt.loads(roads.loc[1, "geometry_wkt"]).length > 11_000
    assert stats["source_segments"] == 4
    assert stats["retained_segments"] == 2
    assert stats["outside_window"] == 1
    assert stats["unnamed_segments"] == 1
    pd.testing.assert_frame_equal(pd.read_csv(output), roads)


@pytest.mark.parametrize("buffer_m", [-1, 0, float("nan"), float("inf")])
def test_invalid_crop_buffer_is_rejected(prep, tmp_path, buffer_m):
    with pytest.raises(ValueError, match="buffer"):
        prep["prepare_roads"](
            tmp_path / "unused.csv",
            pd.DataFrame({"x": [0], "y": [0]}),
            tmp_path / "output.csv",
            buffer_m=buffer_m,
        )


def test_baseline_comparison_reports_differences_without_approximating_counts(prep):
    assert (
        prep["compare_metrics"]({"matching": {"auto": 3}}, {"matching": {"auto": 3}})
        == []
    )
    assert prep["compare_metrics"](
        {"matching": {"auto": 2}}, {"matching": {"auto": 3}}
    ) == [{"metric": "matching.auto", "expected": 3, "actual": 2}]


def test_distance_baseline_counts_directions_and_excludes_fallbacks_and_no_crossing(
    prep,
):
    cleaned = pd.DataFrame(
        {
            "signal_gid": [
                "fictional-a",
                "fictional-b",
                "fictional-c",
                "fictional-d",
                "fictional-drop",
            ],
            "dup_role": ["keep", "review", "review", "keep", "drop"],
            "dup_class": [""] * 5,
            "name_status": ["OK"] * 5,
            "match_tier": ["auto", "flag", "manual", "flag", None],
            "match_method": ["named_crossing"] * 3 + ["fallback_v1", None],
            "match_distance_m": [12, 18, float("inf"), 1, None],
            "location_key": [
                "IH10@FM100",
                "IH10@FM100",
                "IH10@US100",
                "unavailable",
                "IH10@FM100",
            ],
            "location_std": [
                "IH 10 @ FM 100",
                "IH 10 @ FM 100",
                "IH 10 @ US 100",
                "unavailable",
                "IH 10 @ FM 100",
            ],
            "x": [499_988, 500_018, 500_000, 500_100, 510_000],
            "y": [3_300_000] * 5,
        }
    )
    issues = pd.DataFrame(
        [{"record_id": "fictional-c", "rule": "road_association", "severity": "error"}]
    )

    roads = {
        "IH 10": LineString([(499_500, 3_300_000), (500_500, 3_300_000)]),
        "FM 100": LineString([(500_000, 3_299_500), (500_000, 3_301_500)]),
        "US 100": LineString([(499_500, 3_301_000), (500_500, 3_301_000)]),
    }
    metrics = prep["pipeline_metrics"](cleaned, pd.DataFrame(), issues, roads)[
        "matching"
    ]

    assert metrics["participating_records"] == 4
    assert metrics["named_crossing_distance_median_m"] == 15.0
    assert metrics["no_crossing_records"] == 1
    assert metrics["no_crossing_intersections"] == 1
    assert metrics["no_crossing_all_error"]
    assert metrics["fallback_v1_records"] == 1
    assert metrics["fallback_v1_all_flag"]
    assert metrics["named_intersection_count"] == 2
    assert metrics["finite_named_intersection_count"] == 1
    assert metrics["named_intersection_distance_median_m"] == 3.0
    assert metrics["named_intersections_within_auto_max"] == 1


def test_intersection_metric_reassociates_mean_without_drop_or_fallback(prep):
    x, y = 500_000.0, 3_300_000.0
    cleaned = pd.DataFrame(
        {
            "signal_gid": [f"fictional-{n}" for n in range(6)],
            "dup_role": ["keep"] * 4 + ["drop", "keep"],
            "dup_class": [""] * 6,
            "name_status": ["OK"] * 6,
            "location_key": ["IH10@FM100"] * 6,
            "location_std": ["IH 10 @ FM 100"] * 6,
            "match_tier": ["auto", "auto", "flag", "flag", None, "flag"],
            "match_method": ["named_crossing"] * 4 + [None, "fallback_v1"],
            "match_distance_m": [12, 12, 20, 20, None, 10],
            "x": [x - 12, x + 12, x, x, x + 10_000, x + 10_000],
            "y": [y, y, y - 20, y + 20, y, y],
        }
    )
    roads = {
        "IH 10": LineString([(x - 500, y), (x + 500, y)]),
        "FM 100": LineString([(x, y - 500), (x, y + 500)]),
    }
    issues = pd.DataFrame(columns=["rule", "severity", "record_id"])

    distinct = cleaned.iloc[0].to_dict()
    distinct.update(
        signal_gid="fictional-distinct", dup_class="D_distinct", x=x + 10_000
    )
    cleaned = pd.concat([cleaned, pd.DataFrame([distinct])], ignore_index=True)

    metrics = prep["pipeline_metrics"](cleaned, pd.DataFrame(), issues, roads)[
        "matching"
    ]

    assert metrics["named_intersection_count"] == 1
    assert metrics["finite_named_intersection_count"] == 1
    assert metrics["named_intersection_distance_median_m"] == pytest.approx(0)
    assert metrics["named_intersections_within_auto_max"] == 1


def test_existing_baseline_requires_explicit_overwrite(prep, tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"saved": "immutable"}', encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        prep["main"]([str(tmp_path / "unused.csv"), "--baseline", str(baseline)])

    assert error.value.code == 2
    assert baseline.read_text(encoding="utf-8") == '{"saved": "immutable"}'

"""Prepare a private road export and measure the unchanged pipeline baseline.

Run from an installed checkout with a TxDOT CSV path as the positional argument.
Defaults read the local delivery snapshot and write roads_26914.csv/baseline.json
under data/private/. The crop is the signal geometry bounding box plus a meter
buffer. Retain whole intersecting segments, without clipping or merging them.
Existing build_road_index behavior, thresholds, and matching are left unchanged.
Use --expected with a private JSON subset of metrics to check a reference table.
No private names, coordinates, or expected counts belong in this script.
"""

import argparse
import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path

import pandas as pd
import pyproj
import shapely
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import LineString, MultiLineString, Point, box
from shapely.ops import transform

from signal_qaqc.adapt import adapt
from signal_qaqc.io import read_table
from signal_qaqc.naming import make_key, normalize_road
from signal_qaqc.pipeline import run
from signal_qaqc.spatial import AUTO_MAX_M, associate, build_road_index

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "data/private"
_LOCAL_SYSTEMS = {"", "CR", "LS", "FD"}
_ROUTE_SYSTEMS = {
    "IH",
    "BI",
    "US",
    "BU",
    "SH",
    "BS",
    "FM",
    "RM",
    "RR",
    "SL",
    "SS",
    "PR",
    "PA",
}


def road_name(system, number, street):
    """Use HSYS/HNUM for routes and STE_NAM for local systems; remove number padding."""
    system, number, street = (str(value).strip() for value in (system, number, street))
    system = system.upper()
    if system in _LOCAL_SYSTEMS:
        return street or None
    if system not in _ROUTE_SYSTEMS or not number.isascii() or not number.isdigit():
        raise ValueError("Unrecognized HSYS or invalid route HNUM in road export")
    return f"{system} {int(number)}"


def prepare_roads(source, signals, output, *, buffer_m=3_000):
    """Project EPSG:3857 lines, spatially filter in EPSG:26914, and preserve rows."""
    if not isfinite(buffer_m) or buffer_m <= 0:
        raise ValueError("Crop buffer must be finite and positive")
    coordinates = signals[["x", "y"]].apply(pd.to_numeric, errors="raise")
    if (
        coordinates.empty
        or not coordinates.apply(
            lambda column: column.map(lambda value: pd.notna(value) and isfinite(value))
        )
        .all()
        .all()
    ):
        raise ValueError("Signal geometry coordinates must be finite and nonempty")
    bounds = (
        float(coordinates["x"].min()) - buffer_m,
        float(coordinates["y"].min()) - buffer_m,
        float(coordinates["x"].max()) + buffer_m,
        float(coordinates["y"].max()) + buffer_m,
    )
    window = box(*bounds)
    projection = Transformer.from_crs(3857, 26914, always_xy=True)
    rows, source_names = [], set()
    stats = {"source_segments": 0, "unnamed_segments": 0, "outside_window": 0}
    columns = ["HSYS", "HNUM", "STE_NAM", "geometry_wkt"]
    for chunk in pd.read_csv(
        source,
        usecols=columns,
        dtype=str,
        keep_default_na=False,
        encoding="utf-8-sig",
        chunksize=5_000,
    ):
        for system, number, street, raw in chunk[columns].itertuples(
            index=False, name=None
        ):
            stats["source_segments"] += 1
            name = road_name(system, number, street)
            if name is None:
                stats["unnamed_segments"] += 1
                continue
            source_names.add(name)
            geometry = wkt.loads(raw)
            if (
                not isinstance(geometry, (LineString, MultiLineString))
                or geometry.is_empty
                or not geometry.is_valid
            ):
                raise ValueError(
                    f"Invalid line geometry at source row {stats['source_segments']}"
                )
            geometry = transform(projection.transform, geometry)
            if not geometry.is_valid or not all(
                isfinite(value) for value in geometry.bounds
            ):
                raise ValueError(
                    f"Bad projected geometry at source row {stats['source_segments']}"
                )
            if geometry.intersects(window):
                rows.append((name, geometry.wkt))
            else:
                stats["outside_window"] += 1
    roads = pd.DataFrame(rows, columns=["road_name", "geometry_wkt"])
    if roads.empty:
        raise ValueError("No named road segments intersect the buffered signal extent")
    stats.update(
        source_road_names=len(source_names),
        retained_segments=len(roads),
        retained_road_names=int(roads["road_name"].nunique()),
        retained_canonical_keys=len(
            {make_key(normalize_road(name)) for name in roads["road_name"]}
        ),
        buffer_m=buffer_m,
        window_bounds_26914=list(bounds),
        crop="signal_geometry_bbox_buffer; whole intersecting segments",
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    roads.to_csv(output, index=False)
    return roads, stats


def _counts(values):
    return {str(key): int(count) for key, count in values.value_counts().items()}


def _intersection_metrics(named, roads):
    """Reassociate mean XY for each named intersection; do not average distances."""
    if isinstance(roads, pd.DataFrame):
        roads = [
            (name, wkt.loads(raw))
            for name, raw in roads[["road_name", "geometry_wkt"]].itertuples(
                index=False, name=None
            )
        ]
    index = build_road_index(roads)
    groups = named.groupby("location_key", sort=False)
    distances = []
    for _, group in groups:
        xy = group[["x", "y"]].apply(pd.to_numeric, errors="raise").mean()
        match = associate(Point(xy["x"], xy["y"]), group["location_std"].iloc[0], index)
        if isfinite(match.distance_m):
            distances.append(match.distance_m)
    median = float(pd.Series(distances, dtype=float).median()) if distances else None
    return {
        "named_intersection_count": groups.ngroups,
        "finite_named_intersection_count": len(distances),
        "named_intersection_distance_median_m": round(median, 1)
        if median is not None
        else None,
        "named_intersection_distance_median_unrounded_m": median,
        "named_intersections_within_auto_max": sum(
            value <= AUTO_MAX_M for value in distances
        ),
    }


def pipeline_metrics(cleaned, heads, issues, roads):
    """Measure direction distances and re-associated intersection means separately."""
    active = cleaned.loc[cleaned["dup_role"].ne("drop")]
    participants = active.loc[active["match_tier"].notna()]
    named = participants.loc[participants["match_method"].eq("named_crossing")]
    distances = pd.to_numeric(named["match_distance_m"], errors="coerce")
    finite = distances.map(lambda value: pd.notna(value) and isfinite(value))
    valid_distances = distances.loc[finite]
    no_crossing = named.loc[~finite]
    fallback = participants.loc[participants["match_method"].eq("fallback_v1")]
    location_errors = set(
        issues.loc[
            issues["rule"].eq("road_association") & issues["severity"].eq("error"),
            "record_id",
        ]
    )
    return {
        "signals": {
            "input_records": len(cleaned),
            "active_records": len(active),
            "drop_records": int(cleaned["dup_role"].eq("drop").sum()),
            "duplicate_classes": _counts(cleaned["dup_class"]),
            "name_status": _counts(cleaned["name_status"]),
        },
        "matching": {
            **_intersection_metrics(named, roads),
            "participating_records": len(participants),
            "tier_counts": _counts(participants["match_tier"]),
            "method_counts": _counts(participants["match_method"]),
            "method_tier_counts": {
                str(method): _counts(group["match_tier"])
                for method, group in participants.groupby("match_method")
            },
            "named_crossing_distance_median_m": round(
                float(valid_distances.median()), 1
            )
            if len(valid_distances)
            else None,
            "named_crossing_distance_median_unrounded_m": float(
                valid_distances.median()
            )
            if len(valid_distances)
            else None,
            "named_crossing_distance_over_300m": int(valid_distances.gt(300).sum()),
            "no_crossing_records": len(no_crossing),
            "no_crossing_intersections": int(no_crossing["location_key"].nunique()),
            "no_crossing_all_error": set(no_crossing["signal_gid"]) <= location_errors,
            "fallback_v1_records": len(fallback),
            "fallback_v1_all_flag": bool(fallback["match_tier"].eq("flag").all()),
        },
        "heads": {"derived_records": len(heads)},
        "exceptions": {
            "total": len(issues),
            "by_severity": _counts(issues["severity"]),
            "by_rule": _counts(issues["rule"]),
        },
    }


def compare_metrics(actual, expected, prefix=""):
    """Compare an exact reference subset; keep comparison values in private JSON."""
    differences = []
    for key, wanted in expected.items():
        metric = f"{prefix}.{key}" if prefix else key
        found = actual.get(key)
        if isinstance(wanted, dict) and isinstance(found, dict):
            differences.extend(compare_metrics(found, wanted, metric))
        elif key not in actual or wanted != found:
            differences.append({"metric": metric, "expected": wanted, "actual": found})
    return differences


def _fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(Path(path).resolve()), "sha256": digest.hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("road_export", type=Path)
    parser.add_argument(
        "--snapshot", type=Path, default=PRIVATE / "signals_delivered_2025.csv"
    )
    parser.add_argument("--output", type=Path, default=PRIVATE / "roads_26914.csv")
    parser.add_argument("--baseline", type=Path, default=PRIVATE / "baseline.json")
    parser.add_argument(
        "--expected",
        type=Path,
        help="Private reference JSON containing a metrics subset",
    )
    parser.add_argument("--buffer-km", type=float, default=3.0)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly replace an existing baseline",
    )
    args = parser.parse_args(argv)
    if args.baseline.exists() and not args.overwrite:
        parser.error(
            "Baseline exists; choose another --baseline or explicitly --overwrite"
        )
    signals = adapt(read_table(args.snapshot))
    print("Projecting and filtering road segments...", flush=True)
    roads, preparation = prepare_roads(
        args.road_export, signals, args.output, buffer_m=args.buffer_km * 1_000
    )
    print(
        f"Prepared {len(roads)} road segments. Running the unchanged pipeline...",
        flush=True,
    )
    cleaned, heads, issues = run(signals, roads)
    metrics = pipeline_metrics(cleaned, heads, issues, roads)
    expected = (
        json.loads(args.expected.read_text(encoding="utf-8")) if args.expected else None
    )
    differences = compare_metrics(metrics, expected) if expected is not None else []
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    baseline = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "code_commit": commit,
        "environment": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "shapely": shapely.__version__,
            "pyproj": pyproj.__version__,
        },
        "inputs": {
            "snapshot": _fingerprint(args.snapshot),
            "road_export": _fingerprint(args.road_export),
        },
        "matching_source": _fingerprint(ROOT / "src/signal_qaqc/spatial.py"),
        "pipeline_source": _fingerprint(ROOT / "src/signal_qaqc/pipeline.py"),
        "preparation_script": _fingerprint(__file__),
        "prepared_roads": _fingerprint(args.output),
        "preparation": preparation,
        "metrics": metrics,
        "reference_comparison": {
            "status": "match"
            if expected is not None and not differences
            else "mismatch"
            if differences
            else "not_supplied",
            "differences": differences,
        },
    }
    args.baseline.parent.mkdir(parents=True, exist_ok=True)
    args.baseline.write_text(
        json.dumps(baseline, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"metrics": metrics, "comparison": baseline["reference_comparison"]},
            indent=2,
        )
    )
    print(f"Saved private baseline: {args.baseline}")
    if differences:
        raise SystemExit(
            "Baseline differs; investigate before changing matching rules."
        )


if __name__ == "__main__":
    main()

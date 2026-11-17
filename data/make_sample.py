"""Generate fictional inputs with seed 7; no real locations or answers are copied.

Six road segments form seven selected crossings at a fictional UTM offset.
The delivered heads deliberately emulate an old 1-3-only derivation and an
outdated snapshot. Running this script writes only the three sample input CSVs.
"""

import argparse
import random
from pathlib import Path

import pandas as pd
from shapely.geometry import LineString

from signal_qaqc.geo import to_lonlat
from signal_qaqc.heads import HEAD_COLUMNS

X, Y = 500_000.0, 3_300_000.0


def _geometry(points):
    return LineString([(X + x, Y + y) for x, y in points]).wkt


def _variant(name, kind):
    first, second = name.split(" @ ")
    if kind == 0:
        return name.replace("IH 10", "IH10").replace(" @ ", "@")
    if kind == 1:
        return name.replace(" @ ", " at ")
    if kind == 2:
        return (
            name.replace("IH 10", "I-10")
            .replace("US 90", "U.S. Hwy 90")
            .replace(" @ ", "@")
        )
    if kind == 3:
        return f"{second} @ {first}"
    if kind == 4:
        return name.replace("SL 1604", "LP 1604").replace(" @ ", "  @  ")
    return name.lower()


def make_sample(seed=7):
    """Return reproducible (signals, roads, delivered_heads) DataFrames."""
    rng = random.Random(seed)
    roads = pd.DataFrame(
        [
            ("IH 10", _geometry([(-500, 0), (1_100, 0)])),
            ("US 90", _geometry([(-500, 400), (1_100, 400)])),
            ("FM 100", _geometry([(0, -300), (0, 700)])),
            ("FM 200", _geometry([(400, -300), (400, 700)])),
            ("SL 1604", _geometry([(800, -300), (800, 700)])),
            ("Fictional Cedar Rd", _geometry([(-300, 200), (200, 200)])),
        ],
        columns=["road_name", "geometry_wkt"],
    )
    crossings = [
        ("IH 10 @ FM 100", 0, 0),
        ("IH 10 @ FM 200", 400, 0),
        ("IH 10 @ SL 1604", 800, 0),
        ("US 90 @ FM 100", 0, 400),
        ("US 90 @ FM 200", 400, 400),
        ("US 90 @ SL 1604", 800, 400),
        ("FM 100 @ Fictional Cedar Rd", 0, 200),
    ]
    rows = []
    for name, x, y in crossings:
        for direction in ["NB", "SB", "EB", "WB"]:
            index = len(rows)
            px, py = X + x + rng.uniform(-18, 18), Y + y + rng.uniform(-18, 18)
            lon, lat = to_lonlat(px, py)
            rows.append(
                {
                    "signal_gid": f"sample-{index + 1:03d}",
                    "location": name,
                    "travel_direction": direction,
                    "city": "Fictional City",
                    "preemption": "None" if index % 5 == 0 else "Emergency vehicle",
                    "x": px,
                    "y": py,
                    "lon": lon,
                    "lat": lat,
                    "SignalID": "",
                    "edit_date": "2026-01-01",
                    **{
                        f"head_{n}_{component}": (
                            "3-Light" if n == 1 and component == "type" else "NA"
                        )
                        for n in range(1, 5)
                        for component in ["type", "left", "right"]
                    },
                }
            )
    # Each flashing beacon is along one road, away from crossings.
    for number, (x, y, route) in enumerate(
        [
            (-250, 0, "IH 10"),
            (-250, 400, "US 90"),
            (400, 650, "FM 200"),
            (800, 650, "SL 1604"),
        ],
        1,
    ):
        row = rows[0].copy()
        row.update(
            signal_gid=f"sample-{len(rows) + 1:03d}",
            location=f"{1000 + number * 10} {route}",
            travel_direction="NB",
            x=X + x,
            y=Y + y,
            head_1_type="Flashing beacon",
            preemption="None" if len(rows) % 5 == 0 else "Emergency vehicle",
        )
        row["lon"], row["lat"] = to_lonlat(row["x"], row["y"])
        rows.append(row)

    # Dynamic fourth head, plus an old snapshot with a subsequently cleared head.
    rows[0]["head_1_left"] = "Protected"
    for position in [2, 3, 4]:
        rows[0][f"head_{position}_type"] = "3-Light"
    rows[9]["head_2_left"] = "Protected Left Turn (Green Arrow)"
    rows[10]["head_2_type"] = "4-Light"
    variants = list(range(6)) * 2
    rng.shuffle(variants)
    for index, kind in zip(
        [1, 2, 3, 5, 6, 7, 9, 11, 13, 15, 17], variants, strict=False
    ):
        rows[index]["location"] = _variant(rows[index]["location"], kind)
    # Always include the LP alias as well as randomized route spellings.
    rows[11]["location"] = "IH 10 @ LP 1604"
    rows[0]["SignalID"] = "SIG-00007"
    rows[1]["SignalID"] = "SIG-00007"
    rows[2]["SignalID"] = "7"
    rows[20]["city"] = " "
    rows[21]["lon"], rows[21]["lat"] = to_lonlat(rows[21]["x"] + 3, rows[21]["y"])
    rows[22]["lon"], rows[22]["lat"] = to_lonlat(rows[22]["x"] + 220, rows[22]["y"])
    rows[23]["location"] = "UA 100 @ FM 200"
    rows[24]["location"] = "FM 100 Fictional Cedar Rd"
    for index, x, y in [(25, X + 70, Y + 200), (26, X, Y)]:
        rows[index].update(x=x, y=y)
        rows[index]["lon"], rows[index]["lat"] = to_lonlat(x, y)
    rows[27]["travel_direction"] = ""
    # Two exact groups (one spelling variant), 6 m near, conflicting, and 64 m distinct.
    for base, offset, conflict in [
        (0, 0, False),
        (4, 0, False),
        (8, 6, False),
        (12, 3, True),
        (16, 64, True),
    ]:
        row = rows[base].copy()
        row.update(
            signal_gid=f"sample-{len(rows) + 1:03d}", SignalID="", x=row["x"] + offset
        )
        row["lon"], row["lat"] = to_lonlat(row["x"], row["y"])
        if conflict:
            row["city"] = "Another Fictional City"
        if base == 4:
            row["location"] = "I-10 at FM200"
        rows.append(row)

    # Intentionally buggy legacy derivation: head 4 never enters this delivery.
    delivered_rows = []
    for row in rows:
        for position in range(1, 4):
            if row[f"head_{position}_type"] == "NA":
                continue
            delivered_rows.append(
                [
                    row["signal_gid"],
                    position,
                    row[f"head_{position}_type"],
                    row[f"head_{position}_left"],
                    row[f"head_{position}_right"],
                ]
            )
    delivered = pd.DataFrame(delivered_rows, columns=HEAD_COLUMNS)
    # One legacy spelling is equivalent; a separate altered value is a real change.
    delivered.loc[0, "left_turn"] = "Protected Left Turn (Green Arrow)"
    delivered.loc[5, "right_turn"] = "Permissive"
    orphan = pd.DataFrame(
        [["fictional-orphan", 1, "3-Light", "NA", "NA"]], columns=HEAD_COLUMNS
    )
    delivered = pd.concat(
        [delivered, delivered.iloc[:4].copy(), orphan], ignore_index=True
    )
    rows[10]["head_1_type"] = "null"
    return pd.DataFrame(rows), roads, delivered


def write_sample(directory, seed=7):
    """Write fictional sample CSVs only; private/ is never read or written."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, table in zip(
        ["signals", "roads", "heads_delivered"], make_sample(seed), strict=True
    ):
        table.to_csv(directory / f"{name}.csv", index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    write_sample(args.output, args.seed)
    print(f"Wrote fictional sample CSVs to {args.output}")

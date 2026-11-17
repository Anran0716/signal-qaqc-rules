# Run the pipeline

Use Python 3.10 or later from the repository root, with its virtual environment
activated. Install the package once so `python -m signal_qaqc.pipeline` and the
sample script can import the `src/` package:

```powershell
python -m pip install -e ".[dev]"
python data/make_sample.py
python -m signal_qaqc.pipeline data/signals.csv data/roads.csv --heads data/heads_delivered.csv
pytest --cov=signal_qaqc --cov-report=term-missing --cov-fail-under=85
```

The generator lives in the installed `signal_qaqc.sample` module;
`data/make_sample.py` is a thin CLI wrapper. It uses seed 7 by default.
Its six fictional road segments and
seven selected crossings are translated to local coordinates near
`(500000, 3300000)` in EPSG:26914. It generates 37 Signals, including four
address-style beacons and five added duplicate candidates. It never reads real
data. `--seed` changes the random offsets/spellings; `--output` changes where the
three sample CSVs are written.

The pipeline writes `signals_clean.csv`, `heads_expected.csv`, `exceptions.csv`,
and `id_state.json` into `data/output/`. Use `--output PATH` to choose another
directory. Inputs remain untouched. The cleaned table retains drop-labeled
records; heads and location checks exclude those records. Exceptions are sorted
error, warning, info and include the matching method for location problems.

For named crossings, `match_scope=intersection` means that `match_distance_m`
belongs to the active points' mean, stored in `intersection_x/y`. The original
coordinates are preserved. `approach_distance_m` measures each direction's
offset from that mean; offsets above 60 m receive a separate warning. Singletons
have no direction comparison. Fallbacks and beacons use `match_scope=record`.
`D_distinct` device candidates also remain individual and are excluded from the
intersection mean. `match_road_a/b` identify the selected named-road pair, and
`match_reason` records why a local crossing could not be verified. Named matching
clips roads to the initial 2000 m search window and compares up to nine pairs.

`road_gap` means two distinct nearby roads do not intersect. `match_gap_m` is
their minimum separation; `match_distance_m` is undefined for that case.
`match_road_a_distance_m` and `match_road_b_distance_m` measure the query point
(the group mean or individual point) to each selected road. Only when the gap
and both distances are <=`GAP_MAX_M = 50.0` is the exception warning /
`review_in_office`. Otherwise it is manual / error / `send_to_field`, with
`match_reason=named_roads_far_apart`. Reports include all three distances.
These groups retain intersection means and direction-outlier checks. Missing
roads use the capped per-record fallback. Same-key sides carry a separate
naming-review reason and an office-review action.

Rules run in this order: original required answers and coordinates, standardized
names/keys, duplicate marking, SignalID allocation, location matching, then head
derivation and optional delivery reconciliation. Unusable points are left for
review instead of being assigned a duplicate distance or location result.

The Python API is:

```python
from signal_qaqc.pipeline import run

cleaned, heads, exceptions = run(signals, roads, delivered_heads, last_number=0)
saved_last_number = cleaned.attrs["last_number"]
```

Road CSV columns are `road_name` and `geometry_wkt`, in EPSG:26914 meters. The API
also accepts a mapping or iterable of `(road_name, geometry)` pairs. Keep the
returned highest ID number between batches. The CLI reloads it from
`id_state.json`; `--id-state PATH` lets different output directories share the
same history. A malformed state file fails instead of silently resetting IDs.

# Optional private delivery snapshot test

The local `SignalLocation.csv` is stored unchanged as
`data/private/signals_delivered_2025.csv`. This is the delivered snapshot and has
not yet been repaired. The private test automatically skips when the
file is absent. Private input and default generated outputs are ignored by Git.
It checks stable properties, without embedding private rows or asserting exact
counts. No private roads are required for these table invariants.

`signal_qaqc.adapt.adapt` maps `GlobalID`, `Location`, `TravelDire`, `EmergencyF`
and dynamic `Signal_{n}_{H,L,R}` fields to the package schema. The input
`geometry_wkt` must be a Web Mercator (EPSG:3857) point; it supplies projected
EPSG:26914 x/y. Original `Longitude` and `Latitude` are preserved as lon/lat,
allowing the XY rule to detect disagreements.

# Run the pipeline

Use Python 3.10 or later from the repository root, with its virtual environment
activated. Install the package once so `python -m signal_qaqc.pipeline` and the
sample script can import the `src/` package:

```powershell
python -m pip install -e ".[dev]"
python data/make_sample.py
python -m signal_qaqc.pipeline data/signals.csv data/roads.csv --heads data/heads_delivered.csv
python -m pytest -q
```

`data/make_sample.py` uses seed 7 by default. Its six fictional road segments and
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

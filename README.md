# signal-qaqc-rules

[![ci](https://github.com/Anran0716/signal-qaqc-rules/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Anran0716/signal-qaqc-rules/actions/workflows/ci.yml)

## Problem

Field signal inventories can contain inconsistent intersection names, missing
answers, repeat submissions, shifted coordinates, and head tables derived from
an older master. A plausible-looking record can still be wrong. This Python
project makes those decisions explicit and produces a consistent exception table.

## Rules

| Rule | Check | Result / action |
| --- | --- | --- |
| CSV and required fields | Preserve literal `"None"`; detect empty or whitespace-only answers | Report missing required answers |
| Coordinate consistency | Compare original lon/lat with local x/y | >1 m warning; >15 m error |
| Intersection naming | Normalize `Major @ Minor` under the project hierarchy | Six statuses; fix safe formatting/order changes, review ambiguity |
| Duplicate candidates | Canonical name + travel direction, attributes, and distance | Five classes; annotate keep/drop/review without deleting rows |
| SignalID | Preserve valid unique IDs; assign after duplicate marking | Opaque `SIG-00001` IDs; persist the highest number to prevent reuse |
| Location | Measure the intersection mean to the named crossing; check each direction's offset | <=15 m auto; <=50 m flag; >50 m manual; direction offset >60 m warning |
| Road layer | Two distinct nearby roads do not intersect | `road_gap` flag with measured separation; `review_in_office` |
| Heads | Discover all numbered head fields and reconcile delivery by parent/position | Report duplicate, missing, stale, orphan, dropped, or changed heads |

Detailed behavior and assumptions are in [docs/RULES.md](docs/RULES.md).

## Key decisions

- Naming returns six states because incorrect, out-of-scope, and undecidable
  names need different actions.
- Location matching compares nearby route/alias pairs and selects the nearest
  actual crossing. Named intersections use eligible active points' mean positions,
  with a separate direction-outlier check. `D_distinct` devices remain individual.
  Unverified fallbacks cannot auto-pass.
  Missing crossing evidence gets office review with a measured road gap;
  missing roads retain conservative nearest-road fallback evidence.
  Paired tests retain strict expected failures for the nearest-road v1 baseline.
- A repeated key can represent distinct signals. Only exact duplicates are
  marked drop; source rows remain available for review.
- Head derivation is rerun from the current master and reconciled with delivery;
  correct derivation code alone cannot detect an outdated delivered table.
- CI caught a real packaging issue: `python -m pytest` passed locally while
  plain `pytest` failed to import `data.make_sample`. The generator now lives in
  the installed package. See the [failed run](https://github.com/Anran0716/signal-qaqc-rules/actions/runs/44130470982).

## Run

Use Python >=3.10 in a virtual environment, from the repository root:

```bash
python -m pip install -e ".[dev]"
python data/make_sample.py
python -m signal_qaqc.pipeline data/signals.csv data/roads.csv --heads data/heads_delivered.csv
ruff check .
ruff format --check .
pytest --cov=signal_qaqc --cov-report=term-missing --cov-fail-under=85
```

The fixed seed produces 37 fictional Signals. Outputs go to `data/output/`:
cleaned Signals, newly derived heads, exceptions, and persistent ID state.
CI runs on Python 3.10 and 3.12 on every push and pull request, with an 85%
coverage floor. The optional local delivery-snapshot test skips when its private
file is absent. See [docs/RUNNING.md](docs/RUNNING.md) for schemas and ID state.

## Limits

Distances use local EPSG:26914 meters; another region needs a suitable projection.
Ranking Loop below FM/RR is a project assumption. The 15/50 m association
thresholds need calibration against real field distributions. Public examples
are synthetic, and private inputs and generated outputs are ignored by Git.
`SEARCH_RADIUS_M = 2000.0` is an initial local-road search limit: a smaller radius
can cut off a valid large interchange; a larger radius can admit remote same-name
segments. Each side contributes at most three available keys (nine pairs).

The original client project had no tests or version control. This repo is my rebuild of the rule logic on synthetic data to practice testing and CI.

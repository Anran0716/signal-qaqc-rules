# Detailed QA/QC rules

## Road naming rules

Intersection names use `Major @ Minor`. Inputs may use `@`, `at`, `&`, or `and`.
The project hierarchy is `IH > BI > US > BU > SH > BS > FM/RR > Loop > local`.
Within the same rank, the smaller numeric route number comes first; FM and RR
share a rank. Equal rank and number retain their original order.

**Project assumption:** Loop routes (`SL`, including `LP` and `LOOP` spellings)
rank after FM/RR and before local streets. Other unranked state designations,
such as Spur (`SS`) and Park Road (`PR`), require review.

A slash separates aliases on one side, such as `SH 16 / BANDERA RD`.
All aliases are retained in the display name; the highest-priority alias
determines the side's rank. Local road names keep their original case, with
whitespace normalized. A route prefix must be followed by a number, so
`BULVERDE RD` and `BUCKINGHAM VILLAGE ST` are never interpreted as Business US.
Unknown prefixes and ambiguous names are reviewed without guessing a correction.

Directional numbered streets such as `W 3rd St` remain local roads.
Names containing `TURNAROUND` follow normal intersection checks when a separator
is present; only unseparated turnaround labels are excluded as `NOT_INTERSECTION`.

| Status | Meaning | Action |
| --- | --- | --- |
| `OK` | Order and format already match | None |
| `FIX_FORMAT` | Correct order; spelling, spacing, or separator needs normalization | Apply proposal |
| `FIX_ORDER` | Class or numeric route order is reversed | Apply proposal |
| `LOCAL_ONLY` | Both sides are local streets | Hierarchy rule does not apply |
| `NOT_INTERSECTION` | Address-style location or unseparated turnaround | Do not treat as an intersection |
| `REVIEW` | Unknown prefix, missing side/separator, or another ambiguity | Human review |

`check_intersection_name` returns `status`, `reason`, and `proposed`.
Only `FIX_FORMAT` and `FIX_ORDER` authorize automatic renaming; `LOCAL_ONLY`
may provide a formatted name for comparison without authorizing a change.
`make_key` uses the standardized name, uppercase, and no whitespace for grouping
and matching. Unresolved review names keep their original text in the key.

## Duplicate records

`mark_duplicates` groups `make_key(location_std)` together with `travel_direction`.
Direction is trimmed and uppercased; true missing directions become empty strings
before string conversion. Different directions remain separate Signals records.
Missing location names are left for review instead of being merged.

Coordinates must be local EPSG:26914 meters. A group's distance is its farthest
pairwise distance, so a chain of nearby points cannot hide a distant pair.
Distances at exactly 1 m and 15 m follow the inclusive limits below; boundary
tests use the pure numeric classifier rather than projected coordinates.

| Class | Attributes | Maximum distance | Roles |
| --- | --- | --- | --- |
| `A_exact` | Same | <= 1 m | One `keep`, remaining `drop` |
| `B_near` | Same | > 1 m and <= 15 m | One proposed `keep`, remaining `review`; confirm on map |
| `B_far` | Same | > 15 m | All `review`; no keeper selected |
| `C_conflict` | Different | <= 15 m | One proposed `keep`, remaining `review`; confirm attributes |
| `D_distinct` | Different | > 15 m | All `review`; do not delete; verify travel direction |

Keeper priority is most filled business attributes, newest `edit_date`, then
first file position. Missing or invalid edit dates sort oldest. Literal `None`
is a filled answer, while null and whitespace-only cells are missing.

Comparison excludes identifiers (`GlobalID`, `signal_gid`, `SignalID`), coordinates,
geometry, edit date, raw location name, `location_std`, `name_status`, grouping
direction and previous duplicate annotations. `SignalID` is excluded so assigning
IDs cannot change duplicate classification on a later run. Use `attribute_cols`
to explicitly select business fields and column arguments to adapt another schema.

The function retains every input row and column and adds only `dup_group`,
`dup_class`, and `dup_role`. These are annotations, not a physical deletion.
Candidate groups with invalid coordinates must be repaired before classification.

## Permanent SignalIDs

`assign_signal_ids` requires `dup_role`, so duplicate marking runs first.
New IDs are opaque text such as `SIG-00001`: an uppercase `SIG-` prefix and
five ASCII digits, without city, county, road, direction or other business meaning.
IDs belong to individual Signals records, including separate directions at one
intersection. Unique valid IDs remain unchanged. The first active copy of a
duplicate ID is preserved; later copies and malformed IDs get fresh IDs and
an `auto_fixed` exception. Blank IDs are normal new allocations.

Rows marked `drop` remain unchanged and receive no new ID. Their existing valid
numbers still count toward the largest number ever used. Rows marked `keep`
or `review` are active and receive IDs independently.

```python
from signal_qaqc.duplicates import mark_duplicates
from signal_qaqc.ids import assign_signal_ids

# First batch: saved_last_number = 0. Later batches load the saved value.
marked = mark_duplicates(signals)
identified, id_issues, last_number = assign_signal_ids(
    marked, last_number=saved_last_number
)
```

Persist the returned `last_number` between batches, even if high-numbered records
are later removed. Subsequent runs use the larger of that saved number and all
valid input numbers, including drop rows. This prevents historical number reuse;
the current batch alone cannot identify IDs removed from an earlier batch.
Exhausting `99999` raises an error without changing the input table.

## Location rules: nearest-road v1 baseline

`spatial.py` retains `nearest_v1` as the baseline alongside `associate`, which
checks the named intersection. Both use distance-only tiering through
`classify_distance`, `AUTO_MAX_M = 15.0` and `FLAG_MAX_M = 50.0`.

| Tier | Distance | Intended action |
| --- | --- | --- |
| `auto` | <= 15 m | Automatic association |
| `flag` | > 15 m and <= 50 m | Warning; review |
| `manual` | > 50 m | Error; send back to field |

V1 measures distance to every road and returns the nearest candidate and the
two smallest distances. If a point qualifies for `auto` but the second road is
less than `AMBIGUITY_GAP_M = 10.0` m farther away, v1 changes its tier to `flag`.
A gap of exactly 10 m is considered clear. Points beyond 50 m remain `manual`
even when two roads are similarly distant. Only `auto` permits association;
the returned `road_id` remains a candidate on review/manual results.

All geometry uses local EPSG:26914 meters. Tests translate a fictional road
network to `(500000, 3300000)` rather than using real intersections. Invalid
point or road geometry raises an error; no roads produces a manual result.

The first version has two demonstrated design failures:

1. At a correct intersection, distances to both roads are 0 m. V1 calls this
   ambiguous and flags it, even though the point is at the expected crossing.
2. A point can be 8 m from one road and about 200 m from its claimed crossing.
   The second road is 92 m away, so v1 confidently accepts the wrong location.

These tests were first run as ordinary tests and failed with `flag != auto`
and `auto != manual`. They remain strict expected failures (`xfail`) while
separate passing tests characterize the observed v1 behavior. Unexpected passes
fail the suite; exceptions other than the expected assertion failures also fail.
Run the paired desired-behavior tests as ordinary failures to inspect the evidence:

```powershell
python -m pytest tests/test_spatial.py -k known_v1_failure --runxfail -q
```

The design lesson is to check distance to the intersection claimed by the name.
V1 is retained unchanged for comparison with the implemented named-crossing rule.

## Named-crossing association

Build the road index with `build_road_index`: each route is normalized before
`make_key`, and repeated feature names have their segments merged. Each side of
the signal name supplies its ranked route followed by all aliases/qualifiers.
Try candidate pairs in that priority order, skip identical keys, and use the
first distinct pair that actually intersects. If all available pairs fail,
return manual. Shared route numbers can still have intersecting local aliases.

Measure the nearest point on the actual intersection. Multiple crossing points
and overlapping line segments use their nearest point to the signal, never a
centroid. Missing/unparseable names or unavailable roads use `fallback_v1`, with
auto capped at flag because the claimed crossing has not been verified.
`NOT_INTERSECTION` records use `nearest_v1` directly and can retain auto. The
`RoadMatch.method` records `named_crossing`, `fallback_v1`, or `nearest_v1`.

## Input answers, required fields, and XY

`read_table` disables pandas' default NA tokens and treats only empty CSV cells
as missing. In particular, `preemption = "None"` is a real answer. Required
checks also reject whitespace-only answers; zero and False remain valid values.
Missing required columns raise an error.

`check_xy` projects the supplied WGS84 lon/lat to local meters and compares it
with x/y. Distances >1 m are warnings and >15 m are errors. Empty coordinates
are reported by the required rule only; XY skips them. Malformed/nonfinite
coordinates are XY errors. Numeric boundaries are tested in a pure classifier;
geometric tests use offsets safely away from boundaries.

## Head derivation and delivery reconciliation

`explode_heads` discovers positions from every `head_{n}_type/left/right` column.
No maximum position is hardcoded. It excludes drop parents and returns
`signal_gid, position, head_type, left_turn, right_turn` plus warnings. A later
occupied position after an empty position is retained and warned about. A turn
without a type gets a warning and does not create a head.

Head fields have their own empty placeholders, including `NA` and `null`.
Legacy turn descriptions such as `Protected Left Turn (Green Arrow)` normalize
to `Protected`; this normalization does not change the IO rule for preemption.

`reconcile` compares rederived and delivered heads on `(signal_gid, position)`
using an outer merge with `indicator="source"`. Display names are not keys.
Normalize values before comparing. Report one duplicate error per delivered key
while still checking every copy for conflicting values.

| Difference | Meaning | Severity |
| --- | --- | --- |
| duplicate | Repeated delivered key | error |
| missing | Expected head absent from delivery | error |
| stale | Parent exists, but this head no longer does | warning |
| orphan | Delivered parent does not exist | warning |
| dropped | Parent was marked drop; takes priority over orphan | info |
| changed | Normalized values differ | warning |

## Pipeline and exception contract

`pipeline.run` executes required/XY checks on original answers, then naming,
duplicate marking, ID allocation, location matching, and head derivation with
optional reconciliation. It returns three tables and leaves source inputs
unchanged. Invalid points stay in review and are excluded from geometric
duplicate classification and location matching. Missing travel directions are
reported and normalized safely before grouping, including under pandas 3.

Each exception has `record_id, rule, field, severity, action, detail`.
Severity and action values are validated; rows sort error, warning, info.
The CLI writes the three tables and persists the ID high-water mark. See
[RUNNING.md](RUNNING.md) for schemas, file paths, and CLI options.

## CI import failure and fix

The first [CI run](https://github.com/Anran0716/signal-qaqc-rules/actions/runs/44130470982)
failed three tests on both Python versions with `ModuleNotFoundError: No module
named 'data'`. Locally, `python -m pytest` put the repository root on the import
path; CI's plain `pytest` exposed tests importing the generator by accident from
the uninstalled `data/` directory. The fix moves the generator to
`signal_qaqc.sample`, keeps `data/make_sample.py` as a small CLI wrapper, and
imports the installed module in tests. No root-path workaround is added.

That run resolved pandas 2.3.3 on Python 3.10 and pandas 3.1.1 on Python 3.12;
the matrix exercises both generations' missing-value behavior. CI runs Ruff
lint, format checks, plain pytest, and an overall coverage floor of 85% on
pushes and pull requests. CLI tests call `main([...])` directly so coverage sees
the command logic; a subprocess smoke test checks module execution outside the
repository. Private data tests skip when the local delivery snapshot is absent.

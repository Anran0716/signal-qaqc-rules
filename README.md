# signal-qaqc-rules

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

`spatial.py` intentionally retains `nearest_v1` for comparison with the later
named-intersection version. The later version will reuse distance-only tiering
through `classify_distance`, `AUTO_MAX_M = 15.0` and `FLAG_MAX_M = 50.0`.

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
Run the two desired-behavior tests as ordinary failures to inspect the evidence:

```powershell
python -m pytest tests/test_spatial.py -k known_v1_failure --runxfail -q
```

The design lesson is to check distance to the intersection claimed by the name,
rather than asking whether one nearby road is clearly nearest. The improved
version will be implemented separately; v1 is retained unchanged for comparison.

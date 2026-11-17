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

| Status | Meaning | Action |
| --- | --- | --- |
| `OK` | Order and format already match | None |
| `FIX_FORMAT` | Correct order; spelling, spacing, or separator needs normalization | Apply proposal |
| `FIX_ORDER` | Class or numeric route order is reversed | Apply proposal |
| `LOCAL_ONLY` | Both sides are local streets | Hierarchy rule does not apply |
| `NOT_INTERSECTION` | Address-style location or turnaround | Do not treat as an intersection |
| `REVIEW` | Unknown prefix, missing side/separator, or another ambiguity | Human review |

`check_intersection_name` returns `status`, `reason`, and `proposed`.
Only `FIX_FORMAT` and `FIX_ORDER` authorize automatic renaming; `LOCAL_ONLY`
may provide a formatted name for comparison without authorizing a change.
`make_key` uses the standardized name, uppercase, and no whitespace for grouping
and matching. Unresolved review names keep their original text in the key.

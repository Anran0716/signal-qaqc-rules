# Calibration notes

## Direction records and intersection geometry

Measure both distributions before changing location decisions. For intersection
statistics, exclude drop rows, `D_distinct` candidates, and non-named matches,
group by `location_key`,
average the local XY positions, then call the existing `associate` again. Report
finite-distance groups separately from all groups. Averaging existing distances
does not measure the distance of the intersection's mean point.

The measurements and input/code fingerprints remain in local private baseline
artifacts. The original baseline is retained separately from its extension.

Named-crossing decisions now use the intersection mean under the existing
distance tiers. Each direction remains visible through a separate distance from
that mean; the provisional approach limit is 60 m. Exclude drop rows and invalid
points, omit direction checks for singletons, and retain record-level checks for
beacons, `D_distinct` candidates, and unverified fallbacks. Distinct-device
candidates are excluded from both the mean and direction comparisons. The mean
does not repair source coordinates.

## Nearby road combinations

Keep the merged name index, but clip candidate geometries to
`SEARCH_RADIUS_M = 2000.0` around the query point before computing crossings.
Compare up to three available keys per side and select the nearest crossing
across all distinct pairs. Use route/alias priority only to break exact ties.
The selected keys are retained as matching evidence, including alias matches.

The search radius is provisional. Too small a window can exclude a valid large
interchange crossing; too large a window can reintroduce remote same-name road
pieces. Retain the pre-change artifacts and compare stages separately. Missing
local roads/crossings are identified explicitly and handled separately below.

## Missing crossing evidence and office review

When two distinct named roads are present locally but do not intersect, measure
their gap and both query-point distances. Only when all three are at most
`GAP_MAX_M = 50.0` does `road_gap` receive flag / `review_in_office`. Otherwise
retain manual / error / `send_to_field`, with reason `named_roads_far_apart`.
Reports and cleaned output preserve all three measurements. The absent crossing
must not downgrade a large separation or a distant point. The gap remains
separate from the undefined signal-to-crossing distance; review must check road
identity and topology as well as signal location.

If a named road is missing locally, retain the per-record nearest-road fallback,
capped so it cannot auto-pass. A same-key result on both sides is kept as a
separate naming-review reason with an office action; it does not assert a road
gap or guess a frontage-road alias. Those cases remain queued for inspection.

## Route names can hide local aliases

The preparation script names state-route segments using `HSYS` and `HNUM` only.
It does not index the route segment's `STE_NAM` as another name. A signal named
only by that local alias may therefore use the conservative nearest-road
fallback even when the relevant geometry exists. This is a possible cause to
investigate, rather than a confirmed explanation for every fallback.

Preserve this route-only naming for the baseline and the current calibration.
Evaluate explicit local-alias indexing separately. Abbreviation/fuzzy matching
such as RD versus ROAD remains outside this change.

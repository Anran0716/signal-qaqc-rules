"""Normalize and classify Major @ Minor names under the project hierarchy.

Loop routes rank below FM/RR and above local streets; see the README assumption.
Names containing TURNAROUND follow normal intersection checks when a separator
exists. Without a separator, they are classified as NOT_INTERSECTION.
Only FIX_FORMAT and FIX_ORDER proposals are eligible for automatic renaming.
"""

import re
from dataclasses import dataclass

ROUTE_RANK = {
    "IH": 1,
    "BI": 2,
    "US": 3,
    "BU": 4,
    "SH": 5,
    "BS": 6,
    "FM": 7,
    "RR": 7,
    "SL": 8,
}
LOCAL_RANK = 99
_UNRANKED_STATE = {"SS", "PR", "PA"}
_ROUTE_LIKE = re.compile(r"^([A-Z]{2})\s*-?\s*\d+(?![A-Z0-9])", re.IGNORECASE)
_SEPARATORS = re.compile(r"\s*[@&]\s*|\s+(?:AT|AND)\s+", re.IGNORECASE)
_NOT_IN_USE = re.compile(r"^\(\s*NOT\s+IN\s+USE\s*\)", re.IGNORECASE)
_ADDRESS = re.compile(r"^\d+\s+\S")

_PREFIX_PATTERNS = [
    ("BI", r"BUS(?:INESS)?\s+(?:INTERSTATE|IH|I)|BI"),
    ("BU", r"BUS(?:INESS)?\s+US|BU"),
    ("BS", r"BUS(?:INESS)?\s+SH|BS"),
    ("IH", r"INTERSTATE(?:\s+(?:HIGHWAY|HWY))?|IH|I"),
    ("US", r"U\.?S\.?(?:\s*(?:HIGHWAY|HWY))?"),
    ("SH", r"STATE\s+(?:HIGHWAY|HWY)|SH|TX"),
    ("FM", r"FARM\s+TO\s+MARKET(?:\s+(?:ROAD|RD))?|F\.?M\.?"),
    ("RR", r"RANCH\s+(?:TO\s+MARKET(?:\s+ROAD)?|ROAD)|R\.?M\.?|R\.?R\.?"),
    ("SL", r"LOOP|SL|LP"),
    ("SS", r"SPUR|SS"),
    ("PR", r"PARK\s+ROAD|PR"),
    ("PA", r"PA"),
    ("CR", r"COUNTY\s+(?:ROAD|RD)|CR"),
]
# Known routes with invalid suffixes still need review, e.g. US 90AB.
_KNOWN_ROUTE_START = re.compile(
    rf"^(?:{'|'.join(pattern for _, pattern in _PREFIX_PATTERNS)})\s*-?\s*\d",
    re.IGNORECASE,
)
# A prefix must be followed by a number, so BULVERDE is never parsed as BU.
# At most one suffix letter is allowed; reject a longer alphanumeric fragment.
_ROUTE_MATCHERS = [
    (
        prefix,
        re.compile(
            rf"^(?:{pattern})\s*-?\s*(\d+)([A-Z]?)(?![A-Z0-9])(.*)$",
            re.IGNORECASE,
        ),
    )
    for prefix, pattern in _PREFIX_PATTERNS
]


@dataclass(frozen=True)
class Road:
    """One normalized side, with its best ranked alias or a review reason."""

    text: str
    route: str | None = None
    rank: int = LOCAL_RANK
    number: int | None = None
    reason: str | None = None

    def sort_key(self) -> tuple[int, int]:
        return self.rank, self.number if self.number is not None else 0


@dataclass(frozen=True)
class NameCheck:
    """A naming decision; apply proposed only for FIX_FORMAT or FIX_ORDER."""

    status: str
    reason: str
    proposed: str | None = None


def _parse_route(text):
    for prefix, pattern in _ROUTE_MATCHERS:
        match = pattern.fullmatch(text)
        if match:
            return prefix, int(match.group(1)), match.group(2).upper(), match.group(3)
    return None


def normalize_road(token: str) -> str:
    """Normalize a known route prefix, preserving local names and unknown prefixes."""
    if not isinstance(token, str) or not token.strip():
        raise ValueError("A non-empty road name is required")
    cleaned = " ".join(token.split())
    route = _parse_route(cleaned)
    if route is None:
        return cleaned
    prefix, number, suffix, rest = route
    return f"{prefix} {number}{suffix}{rest}".strip()


def parse_side(text: str) -> Road:
    """Keep slash-separated aliases and select the highest-priority route."""
    if not isinstance(text, str) or not text.strip():
        return Road("", reason="Road side is blank")
    parts = text.split("/")
    if any(not part.strip() for part in parts):
        return Road(" ".join(text.split()), reason="A road alias is blank")
    parts = [normalize_road(part) for part in parts]
    normalized = " / ".join(parts)
    candidates, problems = [], []
    for part in parts:
        route = _parse_route(part)
        if route is not None:
            prefix, number, suffix, _ = route
            name = f"{prefix} {number}{suffix}"
            if prefix in ROUTE_RANK:
                candidates.append((ROUTE_RANK[prefix], number, name))
            elif prefix in _UNRANKED_STATE:
                problems.append(f"{name} has no rank in the project hierarchy")
        elif match := _ROUTE_LIKE.match(part):
            problems.append(
                f"Unrecognized or malformed route: {match.group(1).upper()} in {part}"
            )
        elif _KNOWN_ROUTE_START.match(part):
            problems.append(f"Malformed known route: {part}")
    if problems:
        return Road(normalized, reason="; ".join(problems))
    if not candidates:
        return Road(normalized)
    rank, number, route = min(candidates, key=lambda candidate: candidate[:2])
    return Road(normalized, route=route, rank=rank, number=number)


def check_intersection_name(location: str | None) -> NameCheck:
    """Return one of six statuses, a reason, and a standardized name when safe.

    TURNAROUND excludes a record only when no intersection separator is present.
    """
    if not isinstance(location, str) or not location.strip():
        return NameCheck("REVIEW", "Location is blank or is not text")
    cleaned = " ".join(location.split())
    if _NOT_IN_USE.match(cleaned):
        return NameCheck("REVIEW", "Marked NOT IN USE; confirm status before renaming")
    if _ADDRESS.match(cleaned):
        return NameCheck(
            "NOT_INTERSECTION", "Address-style location, not an intersection"
        )
    sides = _SEPARATORS.split(cleaned)
    if len(sides) == 1 and re.search(r"\bTURNAROUND\b", cleaned, re.IGNORECASE):
        return NameCheck(
            "NOT_INTERSECTION", "Turnaround without an intersection separator"
        )
    if len(sides) != 2 or any(not side.strip() for side in sides):
        return NameCheck(
            "REVIEW", "Expected exactly two non-empty sides separated by @, at, or &"
        )
    first, second = (parse_side(side) for side in sides)
    problems = [road.reason for road in (first, second) if road.reason]
    if problems:
        return NameCheck("REVIEW", "; ".join(problems))
    if first.rank == second.rank == LOCAL_RANK:
        return NameCheck(
            "LOCAL_ONLY",
            "Both sides are local roads; highway ordering does not apply",
            f"{first.text} @ {second.text}",
        )
    if second.sort_key() < first.sort_key():
        reason = (
            "Same class: the smaller route number comes first"
            if first.rank == second.rank
            else f"{second.route} has higher project priority"
        )
        return NameCheck("FIX_ORDER", reason, f"{second.text} @ {first.text}")
    proposed = f"{first.text} @ {second.text}"
    if proposed != location:
        return NameCheck(
            "FIX_FORMAT",
            "Order is correct; standardize spelling, spacing, or separator",
            proposed,
        )
    return NameCheck("OK", "Road order and formatting match the project rule", proposed)


def make_key(location: str | None) -> str:
    """Standardize a name, uppercase it, and remove whitespace for grouping."""
    if not isinstance(location, str) or not location.strip():
        return ""
    result = check_intersection_name(location)
    base = result.proposed if result.proposed is not None else location
    return "".join(base.upper().split())

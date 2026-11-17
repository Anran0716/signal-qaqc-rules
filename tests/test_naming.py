import pytest

from signal_qaqc.naming import (
    check_intersection_name,
    make_key,
    normalize_road,
    parse_side,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("IH35", "IH 35"),
        ("I-10", "IH 10"),
        ("U.S. Hwy 90", "US 90"),
        ("LP 1604", "SL 1604"),
        ("BULVERDE RD", "BULVERDE RD"),
        ("BUCKINGHAM VILLAGE ST", "BUCKINGHAM VILLAGE ST"),
        ("Interstate Highway 37", "IH 37"),
        ("BUS IH 35", "BI 35"),
        ("Business US 281", "BU 281"),
        ("BUS SH 46", "BS 46"),
        ("TX-46", "SH 46"),
        ("State Highway 16", "SH 16"),
        ("F.M. 471", "FM 471"),
        ("Farm to Market Road 1560", "FM 1560"),
        ("Ranch Road 12", "RR 12"),
        ("RM1431", "RR 1431"),
        ("Loop1604", "SL 1604"),
        ("  us   0090a  ", "US 90A"),
        ("  Oak   Dr  ", "Oak Dr"),
        ("UA 46", "UA 46"),
        ("US 90AB", "US 90AB"),
        ("SH 46BULVERDE", "SH 46BULVERDE"),
    ],
)
def test_normalize_road_standardizes_known_routes_without_guessing(raw, expected):
    assert normalize_road(raw) == expected
    assert normalize_road(expected) == expected


@pytest.mark.parametrize("raw", ["", " \t\n", None])
def test_normalize_road_rejects_missing_road_names(raw):
    with pytest.raises(ValueError, match="road name"):
        normalize_road(raw)


@pytest.mark.parametrize(
    ("raw", "route", "text"),
    [
        ("SH 16 / BANDERA RD", "SH 16", "SH 16 / BANDERA RD"),
        ("FM1560 / IH35", "IH 35", "FM 1560 / IH 35"),
        ("FM 1976 / RR 1516", "RR 1516", "FM 1976 / RR 1516"),
        ("LP 1604 / FM471", "FM 471", "SL 1604 / FM 471"),
    ],
)
def test_parse_side_uses_the_highest_ranked_alias_and_keeps_all_names(raw, route, text):
    road = parse_side(raw)

    assert road.route == route
    assert road.text == text
    assert road.reason is None


@pytest.mark.parametrize(
    "name", ["BULVERDE RD", "E 6TH ST", "W 3rd St", "NE 6TH ST", "SW 13th St"]
)
def test_parse_side_keeps_directional_numbered_streets_and_bulverde_local(name):
    road = parse_side(name)

    assert road.text == name
    assert road.route is None
    assert road.reason is None


@pytest.mark.parametrize("raw", ["SH 16 / UA 46", "SH 16 /", "/ SH 16", ""])
def test_parse_side_records_ambiguous_or_empty_aliases_for_review(raw):
    assert parse_side(raw).reason


@pytest.mark.parametrize(
    ("raw", "status", "proposed"),
    [
        ("IH 10 @ FM 1560", "OK", "IH 10 @ FM 1560"),
        ("IH10 @ FM 1560", "FIX_FORMAT", "IH 10 @ FM 1560"),
        ("IH 10 at FM 1560", "FIX_FORMAT", "IH 10 @ FM 1560"),
        ("SH 46@FM 471", "FIX_FORMAT", "SH 46 @ FM 471"),
        ("FM 1560 @ IH 10", "FIX_ORDER", "IH 10 @ FM 1560"),
        ("FM 1976 @ FM 1516", "FIX_ORDER", "FM 1516 @ FM 1976"),
        ("Oak Dr @ Pine St", "LOCAL_ONLY", "Oak Dr @ Pine St"),
        ("1220 FM 471", "NOT_INTERSECTION", None),
        ("TURNAROUND", "NOT_INTERSECTION", None),
        ("UA 46 @ FM 471", "REVIEW", None),
        ("UA 87 @ FM 100", "REVIEW", None),
        ("SH 46 FM 1560", "REVIEW", None),
        ("Main St @ US 90", "FIX_ORDER", "US 90 @ Main St"),
        ("SH 46 @ BULVERDE RD", "OK", "SH 46 @ BULVERDE RD"),
        ("SH 46 @ BUCKINGHAM VILLAGE ST", "OK", "SH 46 @ BUCKINGHAM VILLAGE ST"),
        ("LP 1604 @ FM 471", "FIX_ORDER", "FM 471 @ SL 1604"),
        ("SL 1604 @ Oak Dr", "OK", "SL 1604 @ Oak Dr"),
        ("FM 9 @ RR 10", "OK", "FM 9 @ RR 10"),
        ("RR 10 @ FM 9", "FIX_ORDER", "FM 9 @ RR 10"),
        ("FM 10 @ FM 9", "FIX_ORDER", "FM 9 @ FM 10"),
        ("  IH 10 @ FM 1560  ", "FIX_FORMAT", "IH 10 @ FM 1560"),
        (
            "SH 16 / BANDERA RD @ FM471 / GRISSOM RD",
            "FIX_FORMAT",
            "SH 16 / BANDERA RD @ FM 471 / GRISSOM RD",
        ),
        ("FM 471 @ BANDERA RD / SH16", "FIX_ORDER", "BANDERA RD / SH 16 @ FM 471"),
        ("US 90 & FM 1560", "FIX_FORMAT", "US 90 @ FM 1560"),
        ("US 90 AND FM 1560", "FIX_FORMAT", "US 90 @ FM 1560"),
        ("Oak Dr at Pine St", "LOCAL_ONLY", "Oak Dr @ Pine St"),
        (None, "REVIEW", None),
        ("", "REVIEW", None),
        (float("nan"), "REVIEW", None),
        ("US 90 @", "REVIEW", None),
        ("@ FM 471", "REVIEW", None),
        ("US 90 @ @ FM 471", "REVIEW", None),
        ("US 90 @ FM 471 @ Main St", "REVIEW", None),
        ("SH 16 / UA 46 @ FM 471", "REVIEW", None),
        ("US 90AB @ FM 471", "REVIEW", None),
        ("SS 55 @ FM 471", "REVIEW", None),
        ("(NOT IN USE) SH 46 @ FM 471", "REVIEW", None),
        ("IH 10 @ TURNAROUND", "OK", "IH 10 @ TURNAROUND"),
        (
            "FM 2790 / SOMERSET RD @ FM 2790 / W DIXON RD",
            "OK",
            "FM 2790 / SOMERSET RD @ FM 2790 / W DIXON RD",
        ),
    ],
)
def test_check_intersection_name_returns_six_distinct_statuses(raw, status, proposed):
    result = check_intersection_name(raw)

    assert result.status == status
    assert result.proposed == proposed
    assert isinstance(result.reason, str) and result.reason


@pytest.mark.parametrize(
    ("raw", "status", "proposed"),
    [
        ("FM 100 @ W 3rd St", "OK", "FM 100 @ W 3rd St"),
        ("FM 100 @ E 6TH ST", "OK", "FM 100 @ E 6TH ST"),
        ("SL 200 @ NE 6TH ST", "OK", "SL 200 @ NE 6TH ST"),
        ("LP 200 @ SW 13th St", "FIX_FORMAT", "SL 200 @ SW 13th St"),
    ],
)
def test_directional_numbered_local_streets_are_not_unknown_routes(
    raw, status, proposed
):
    result = check_intersection_name(raw)

    assert result.status == status
    assert result.proposed == proposed


@pytest.mark.parametrize(
    ("raw", "status", "proposed"),
    [
        (
            "US 100 SB @ FICTIONAL VISTA TURNAROUND",
            "OK",
            "US 100 SB @ FICTIONAL VISTA TURNAROUND",
        ),
        (
            "US 100 SB at FICTIONAL VISTA TURNAROUND",
            "FIX_FORMAT",
            "US 100 SB @ FICTIONAL VISTA TURNAROUND",
        ),
        (
            "FICTIONAL VISTA TURNAROUND @ US 100 SB",
            "FIX_ORDER",
            "US 100 SB @ FICTIONAL VISTA TURNAROUND",
        ),
        ("FICTIONAL VISTA TURNAROUND", "NOT_INTERSECTION", None),
        ("US 100 SB @ @ FICTIONAL VISTA TURNAROUND", "REVIEW", None),
        ("TURNAROUND @", "REVIEW", None),
    ],
)
def test_turnaround_names_follow_intersection_rules_when_a_separator_exists(
    raw, status, proposed
):
    result = check_intersection_name(raw)

    assert result.status == status
    assert result.proposed == proposed


@pytest.mark.parametrize(
    ("major", "minor"),
    [
        ("IH 35", "BI 35"),
        ("BI 35", "US 90"),
        ("US 90", "BU 90"),
        ("BU 90", "SH 46"),
        ("SH 46", "BS 46"),
        ("BS 46", "FM 471"),
        ("BS 46", "RR 12"),
        ("FM 471", "SL 1604"),
        ("RR 12", "SL 1604"),
        ("SL 1604", "Oak Dr"),
    ],
)
def test_check_intersection_name_applies_every_level_of_the_project_hierarchy(
    major, minor
):
    result = check_intersection_name(f"{minor} @ {major}")

    assert result.status == "FIX_ORDER"
    assert result.proposed == f"{major} @ {minor}"


@pytest.mark.parametrize(
    ("raw", "canonical", "expected"),
    [
        ("IH35 @ SH218", "IH 35 @ SH 218", "IH35@SH218"),
        ("FM 1560 at IH10", "IH 10 @ FM 1560", "IH10@FM1560"),
        ("LP 1604 @ FM 471", "FM 471 @ SL 1604", "FM471@SL1604"),
        ("fm 1560 & U.S. Hwy 90", "US 90 @ FM 1560", "US90@FM1560"),
        ("Oak Dr at Pine St", "OAK DR @ PINE ST", "OAKDR@PINEST"),
    ],
)
def test_make_key_normalizes_order_spelling_case_and_whitespace(
    raw, canonical, expected
):
    assert make_key(raw) == make_key(canonical) == expected


@pytest.mark.parametrize("raw", [None, "", " \t\n"])
def test_make_key_returns_an_empty_key_for_missing_names(raw):
    assert make_key(raw) == ""


def test_make_key_keeps_unknown_prefixes_without_guessing():
    assert make_key("UA 46 @ FM 471") == "UA46@FM471"


@pytest.mark.parametrize(
    "raw",
    [
        "IH10@FM1560",
        "FM 1976 @ FM 1516",
        "LP 1604 @ FM 471",
        "FM 471 @ BANDERA RD / SH16",
    ],
)
def test_applying_an_automatic_proposal_produces_an_ok_name(raw):
    result = check_intersection_name(raw)

    assert result.status in {"FIX_FORMAT", "FIX_ORDER"}
    assert check_intersection_name(result.proposed).status == "OK"

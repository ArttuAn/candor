"""Value-level detection."""

import pytest

from candor import detect


@pytest.mark.parametrize("raw,expected", [
    ("", True), ("   ", True), ("null", True), ("NULL", True), ("N/A", True),
    ("nan", True), ("None", True), ("0", False), ("false", False), ("unknown", False),
])
def test_null_tokens(raw, expected):
    assert detect.is_null(raw) is expected


@pytest.mark.parametrize("raw", ["unknown", "TBD", "-", "??", "not specified", "(blank)"])
def test_placeholders_are_not_nulls(raw):
    # The distinction matters: a placeholder is counted by every aggregation.
    assert detect.is_placeholder(raw)
    assert not detect.is_null(raw)


@pytest.mark.parametrize("raw,expected", [
    ("1234", 1234.0),
    ("1,234.56", 1234.56),
    ("1.234,56", 1234.56),     # European
    ("$1,299", 1299.0),
    ("€ 45,50", 45.50),
    ("(500)", -500.0),          # accounting negative
    ("12.5%", 12.5),
    ("1 234 567", 1234567.0),
    ("-0.75", -0.75),
    ("1e3", 1000.0),
    ("abc", None),
])
def test_number_parsing(raw, expected):
    assert detect.normalise_number(raw) == expected


@pytest.mark.parametrize("raw,fmt", [
    ("2024-03-15", "iso_date"),
    ("2024-03-15T10:30:00", "iso_datetime"),
    ("15.03.2024", "euro_dmy"),
    ("03/15/2024", "us_mdy"),
    ("2024-03", "iso_month"),
    ("March 2024", "text_month"),
])
def test_date_formats(raw, fmt):
    parsed = detect.parse_date(raw)
    assert parsed is not None
    assert parsed[1] == fmt


def test_unparseable_date():
    assert detect.parse_date("sometime last spring") is None


@pytest.mark.parametrize("raw,ambiguous", [
    ("03/04/2024", True),    # could be 3 April or 4 March
    ("15/03/2024", False),   # only one reading is valid
    ("03/03/2024", False),   # both readings agree
    ("2024-03-04", False),
])
def test_ambiguous_slash_dates(raw, ambiguous):
    assert detect.slash_date_ambiguous(raw) is ambiguous


@pytest.mark.parametrize("values,expected", [
    (["a@x.com", "b@y.org", "c@z.net"], "email"),
    (["https://a.com", "http://b.org", "https://c.net"], "url"),
    (["12.5%", "3%", "99.9%"], "percentage"),
    (["$10", "$20", "$30"], "currency"),
    (["hello", "world", "there"], None),
])
def test_semantic_types(values, expected):
    assert detect.detect_semantic(values) == expected


def test_mojibake_detection():
    untrimmed, mojibake, control = detect.text_flags(" élevé ".encode().decode("latin-1"))
    assert mojibake


def test_bare_year_is_integer_not_date():
    # detect_kind is per-value; a column of bare years is resolved in the profiler.
    assert detect.detect_kind("2024") == "integer"

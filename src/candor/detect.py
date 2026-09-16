"""Value-level detection: nulls, placeholders, types, semantic types, formats.

Deliberately dependency-free and string-first, because the interesting data
quality problems live in how values were *written*, not in how a dataframe
library coerced them.
"""

from __future__ import annotations

import re
from datetime import date, datetime

# --------------------------------------------------------------------------- #
# nulls and fake nulls

NULL_TOKENS = {"", "none", "null", "nil", "nan", "na", "n/a", "#n/a", "<na>", "\\n"}

# Values that are technically present but mean "we don't know". These are worse
# than a null, because every naive aggregation silently counts them.
PLACEHOLDER_TOKENS = {
    "unknown", "undefined", "unspecified", "not specified", "not available",
    "missing", "tbd", "to be determined", "pending", "n.a.", "no data",
    "-", "--", "---", "?", "??", "x", "xx", "xxx", "test", "asdf", "foo",
    "default", "other", "misc", "placeholder", "dummy", "sample", "example",
    "no value", "blank", "empty", "(blank)", "(none)", "null value",
}

# Numeric sentinels that stand in for missing data.
NUMERIC_SENTINELS = {-1.0, -9.0, -99.0, -999.0, -9999.0, 9999.0, 99999.0, 999999.0, 0.0}
STRONG_NUMERIC_SENTINELS = {-999.0, -9999.0, 9999.0, 99999.0, 999999.0}

BOOL_TRUE = {"true", "t", "yes", "y", "1"}
BOOL_FALSE = {"false", "f", "no", "n", "0"}

# --------------------------------------------------------------------------- #
# patterns

_NUM_RE = re.compile(r"^[+-]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?([eE][+-]?\d+)?$")
_INT_RE = re.compile(r"^[+-]?(\d{1,3}(,\d{3})+|\d+)$")
_CURRENCY_RE = re.compile(r"^\s*([$€£¥₹]|USD|EUR|GBP|SEK|NOK|DKK)\s*[+-]?[\d,. ]+\s*$", re.I)
_PERCENT_RE = re.compile(r"^\s*[+-]?[\d,.]+\s*%\s*$")
_PAREN_NEG_RE = re.compile(r"^\(\s*[\d,.]+\s*\)$")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_URL_RE = re.compile(r"^(https?|ftp)://\S+$", re.I)
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_IPV4_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
_PHONE_RE = re.compile(r"^\+?[\d][\d\s().-]{6,19}$")
_COUNTRY2_RE = re.compile(r"^[A-Z]{2}$")
_JSONISH_RE = re.compile(r"^\s*[\[{].*[\]}]\s*$", re.S)

_MOJIBAKE_RE = re.compile(r"Ã[\x80-\xbf]|â€|Â[\x80-\xbf]|ï»¿|�")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Date formats we recognise, most specific first. The *name* matters as much as
# the parse: two different names in one column is a consistency defect.
DATE_FORMATS: list[tuple[str, str, str]] = [
    ("iso_datetime_tz", "%Y-%m-%dT%H:%M:%S%z", "second"),
    ("iso_datetime", "%Y-%m-%dT%H:%M:%S", "second"),
    ("iso_datetime_space", "%Y-%m-%d %H:%M:%S", "second"),
    ("iso_datetime_minute", "%Y-%m-%d %H:%M", "second"),
    ("iso_date", "%Y-%m-%d", "day"),
    ("iso_month", "%Y-%m", "month"),
    ("slash_ymd", "%Y/%m/%d", "day"),
    ("euro_dmy", "%d.%m.%Y", "day"),
    ("euro_dmy_slash", "%d/%m/%Y", "day"),
    ("us_mdy", "%m/%d/%Y", "day"),
    ("us_mdy_short", "%m/%d/%y", "day"),
    ("dmy_dash", "%d-%m-%Y", "day"),
    ("text_dmy", "%d %b %Y", "day"),
    ("text_mdy", "%b %d, %Y", "day"),
    ("text_month", "%B %Y", "month"),
    ("year", "%Y", "year"),
]

# %d/%m/%Y and %m/%d/%Y are indistinguishable for day <= 12. Track it rather
# than guess, because guessing wrong silently reorders a whole time series.
_AMBIGUOUS_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$")


def is_null(raw: object) -> bool:
    if raw is None:
        return True
    if isinstance(raw, float) and raw != raw:  # NaN
        return True
    return str(raw).strip().lower() in NULL_TOKENS


def is_placeholder(raw: object) -> bool:
    if raw is None:
        return False
    return str(raw).strip().lower() in PLACEHOLDER_TOKENS


def normalise_number(text: str) -> float | None:
    """Parse a number written the way humans and spreadsheets write them."""
    s = text.strip()
    if not s:
        return None
    negative = False
    if _PAREN_NEG_RE.match(s):  # accounting negative
        negative, s = True, s[1:-1].strip()
    s = re.sub(r"^\s*(USD|EUR|GBP|SEK|NOK|DKK)\s*", "", s, flags=re.I)
    s = s.lstrip("$€£¥₹").strip()
    percent = s.endswith("%")
    if percent:
        s = s[:-1].strip()
    s = s.replace(" ", "").replace(" ", "")
    # 1.234,56 (European) vs 1,234.56 (Anglo)
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = s.replace(",", "." if (len(tail) != 3 or "," in head) else "")
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


def parse_date(text: str) -> tuple[datetime, str, str] | None:
    """Return (value, format_name, granularity) for the first format that fits."""
    s = text.strip()
    if not s or len(s) > 40:
        return None
    for name, fmt, granularity in DATE_FORMATS:
        try:
            value = datetime.strptime(s, fmt)
        except ValueError:
            continue
        if name == "year" and not (1900 <= value.year <= 2200):
            continue
        return value, name, granularity
    # Trailing Z / fractional seconds that strptime is fussy about
    try:
        value = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return value, "iso_datetime", "second"
    except ValueError:
        return None


def slash_date_ambiguous(text: str) -> bool:
    """True when d/m/Y and m/d/Y would both parse to a valid but different date."""
    m = _AMBIGUOUS_SLASH.match(text.strip())
    if not m:
        return False
    a, b = int(m.group(1)), int(m.group(2))
    return a != b and a <= 12 and b <= 12


def detect_kind(text: str) -> str:
    """Coarse type of a single raw value."""
    s = text.strip()
    if not s:
        return "empty"
    low = s.lower()
    if low in BOOL_TRUE or low in BOOL_FALSE:
        # Bare 0/1 is only boolean if the whole column agrees; decided upstream.
        return "boolean" if low not in {"1", "0"} else "integer"
    if _INT_RE.match(s):
        return "integer"
    if _NUM_RE.match(s) or _PERCENT_RE.match(s) or _CURRENCY_RE.match(s) or _PAREN_NEG_RE.match(s):
        return "number"
    if parse_date(s):
        return "datetime" if re.search(r"[T:]\d{2}:", s) else "date"
    return "string"


def detect_semantic(values: list[str]) -> str | None:
    """Semantic type of a column, decided by majority of non-empty samples."""
    sample = [v.strip() for v in values if v and v.strip()][:400]
    if not sample:
        return None
    n = len(sample)

    def hits(pattern: re.Pattern[str]) -> float:
        return sum(1 for v in sample if pattern.match(v)) / n

    for name, pattern in (
        ("email", _EMAIL_RE),
        ("url", _URL_RE),
        ("uuid", _UUID_RE),
        ("ipv4", _IPV4_RE),
        ("json", _JSONISH_RE),
        ("currency", _CURRENCY_RE),
        ("percentage", _PERCENT_RE),
    ):
        if hits(pattern) >= 0.8:
            return name
    if hits(_COUNTRY2_RE) >= 0.9 and len({v for v in sample}) > 2:
        return "country_code"
    if hits(_PHONE_RE) >= 0.8 and any(not v.isdigit() for v in sample):
        return "phone"
    return None


def text_flags(text: str) -> tuple[bool, bool, bool]:
    """(untrimmed, mojibake, control_chars) for one value."""
    return (
        text != text.strip(),
        bool(_MOJIBAKE_RE.search(text)),
        bool(_CONTROL_RE.search(text)),
    )


def coerce_to_str(raw: object) -> str:
    if raw is None:
        return ""
    if isinstance(raw, (datetime, date)):
        return raw.isoformat()
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    return str(raw)

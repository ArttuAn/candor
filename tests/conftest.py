"""Shared fixtures.

Every test that touches dates pins `NOW`, because staleness and future-date
rules would otherwise change their verdict as the calendar moves.
"""

from __future__ import annotations

import json

import pytest

from helpers import NOW


@pytest.fixture
def now():
    return NOW


@pytest.fixture
def write(tmp_path):
    def _write(name: str, content: str):
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _write


@pytest.fixture
def write_json(tmp_path):
    def _write(name: str, payload):
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def clean_csv(write):
    """12 monthly rows, no defects: the control case."""
    rows = ["date,region,revenue,customer_id"]
    for month in range(1, 13):
        for i, region in enumerate(("EMEA", "APAC", "AMER")):
            rows.append(f"2024-{month:02d}-15,{region},{1000 + month * 10 + i},C{month:02d}{i}")
    return write("clean.csv", "\n".join(rows) + "\n")


@pytest.fixture
def messy_csv(write):
    """Every defect candor claims to detect, in one small file."""
    return write("messy.csv", "\n".join([
        "order_id,order_date,region,amount,email",
        "1,2024-01-05,EMEA,100,a@x.com",
        "2,01/05/2024,emea ,200,b@x.com",
        "3,2024-02-10,APAC,,c@x.com",
        "4,2024-02-11,Unknown,-999,",
        "5,05.03.2024,APAC,150,e@x.com",
        "6,2024-03-20,AMER,N/A,f@x.com",
        "6,2024-03-20,AMER,N/A,f@x.com",
        "8,2024-04-01,AMER,99999,h@x.com",
    ]) + "\n")


@pytest.fixture
def recent_csv(write):
    """Like clean_csv, but anchored to the real clock.

    The CLI has no `--now`, so anything exercising it needs data that is still
    fresh today; otherwise the staleness rule fires and the test rots.
    """
    from datetime import date, timedelta

    today = date.today()
    rows = ["date,region,revenue,customer_id"]
    for week in range(26):
        day = today - timedelta(days=week * 7 + 1)
        for i, region in enumerate(("EMEA", "APAC", "AMER")):
            rows.append(f"{day.isoformat()},{region},{1000 + week * 10 + i},C{week:02d}{i}")
    return write("recent.csv", "\n".join(rows) + "\n")


@pytest.fixture
def anyio_backend():
    """MCP's server API is async; the tests only need the asyncio backend."""
    return "asyncio"

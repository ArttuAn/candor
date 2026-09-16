"""Regenerate the example datasets.

The messy export is deliberately built to trip every rule in the catalogue, so
`candor profile examples/messy_orders.csv` is a tour of what the tool detects.
Run with: python examples/generate.py
"""

from __future__ import annotations

import csv
import datetime
import json
import pathlib
import random

SEED = 7
ROOT = pathlib.Path(__file__).parent


def messy(rng: random.Random) -> list[dict]:
    regions = ["EMEA", "emea", "APAC", "AMER", " AMER", "Unknown"]
    plans = ["pro", "Pro", "enterprise", "free", "N/A"]
    rows: list[dict] = []
    start = datetime.date(2023, 1, 1)
    for i in range(420):
        # Days 181-242 are excluded: a two-month hole across July-August 2023.
        day = start + datetime.timedelta(days=rng.choice(
            [d for d in range(700) if not 181 <= d <= 242]))
        style = rng.choice(["iso", "iso", "iso", "us", "euro"])
        date = (day.isoformat() if style == "iso"
                else f"{day.month:02d}/{day.day:02d}/{day.year}" if style == "us"
                else f"{day.day:02d}.{day.month:02d}.{day.year}")
        amount = round(rng.lognormvariate(6, 0.8), 2)
        if rng.random() < 0.02:
            amount = 9999999.0                       # unit mix-up / sentinel
        rows.append({
            "order_id": f"ORD-{1000 + (i if rng.random() > 0.03 else i - 1)}",
            "order_date": date,
            "customer_email": f"user{i}@example.com" if rng.random() > 0.12 else "",
            "region": rng.choice(regions),
            "plan": rng.choice(plans),
            "amount_eur": "" if rng.random() < 0.08 else amount,
            "seats": rng.choice([1, 2, 3, 5, 10, -1]),
            "churned": rng.choice(["true", "false"]),
            "notes": rng.choice(["", "", "", "follow up", "Ã©levÃ© priority"]),
            "source_system": "salesforce",
        })
    rows.append(dict(rows[0]))                       # exact duplicates
    rows.append(dict(rows[5]))
    return rows


def clean(rng: random.Random) -> list[dict]:
    return [
        {
            "order_id": f"ORD-{i:06d}",
            "order_date": (datetime.date(2024, 1, 1) + datetime.timedelta(days=i % 600)).isoformat(),
            "region": rng.choice(["EMEA", "APAC", "AMER"]),
            "plan": rng.choice(["free", "pro", "enterprise"]),
            "amount_eur": round(rng.uniform(20, 900), 2),
            "seats": rng.randint(1, 40),
        }
        for i in range(1200)
    ]


def survey(rng: random.Random) -> list[dict]:
    return [
        {
            "respondent": f"R{i}",
            "satisfaction": rng.choice([1, 2, 3, 4, 5]),
            "segment": rng.choice(["SMB", "Mid", "Enterprise"]),
            "would_recommend": rng.choice(["yes", "no", "unknown"]),
        }
        for i in range(14)
    ]


def write_csv(name: str, rows: list[dict]) -> None:
    with (ROOT / name).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rng = random.Random(SEED)
    write_csv("messy_orders.csv", messy(rng))
    write_csv("clean_orders.csv", clean(rng))
    (ROOT / "survey.json").write_text(json.dumps(survey(rng), indent=2))
    print("wrote", ", ".join(sorted(p.name for p in ROOT.iterdir() if p.suffix in (".csv", ".json"))))


if __name__ == "__main__":
    main()

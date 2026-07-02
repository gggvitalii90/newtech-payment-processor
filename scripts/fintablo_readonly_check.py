from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payment_processor.fintablo_client import FinTabloClient, FinTabloError, load_fintablo_settings


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def fintablo_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def compact_items(items: list[dict[str, Any]], fields: list[str], limit: int = 5) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items[:limit]:
        result.append({field: item.get(field) for field in fields if field in item})
    return result


def build_summary(client: FinTabloClient, start: date, end: date) -> dict[str, Any]:
    moneybags = client.list_moneybags()
    categories = client.list_categories()
    partners = client.list_partners()
    directions = client.list_directions()
    deals = client.list_deals()
    employees = client.list_employees()
    transactions = client.list_transactions(date_from=fintablo_date(start), date_to=fintablo_date(end))
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "counts": {
            "moneybags": len(moneybags),
            "categories": len(categories),
            "partners": len(partners),
            "directions": len(directions),
            "deals": len(deals),
            "employees": len(employees),
            "transactions": len(transactions),
        },
        "transaction_groups": count_by(transactions, "group"),
        "sample_moneybags": compact_items(moneybags, ["id", "name", "type", "archived"]),
        "sample_categories": compact_items(categories, ["id", "name", "group", "parentId"]),
        "sample_directions": compact_items(directions, ["id", "name", "parentId"]),
        "sample_deals": compact_items(deals, ["id", "name", "directionId", "responsibleId"]),
        "sample_transactions": compact_items(transactions, ["id", "date", "group", "value", "moneybagId", "moneybag2Id", "categoryId", "partnerId", "directionId", "dealId", "description"]),
    }


def count_by(items: list[dict[str, Any]], field: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        key = str(item.get(field) or "")
        result[key] = result.get(key, 0) + 1
    return dict(sorted(result.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only FinTablo diagnostics")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    args = parser.parse_args()

    start = parse_iso_date(args.start)
    end = parse_iso_date(args.end)
    if end < start:
        raise SystemExit("--end must be >= --start")

    try:
        client = FinTabloClient(load_fintablo_settings())
        summary = build_summary(client, start, end)
    except FinTabloError as exc:
        print(f"FinTablo error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

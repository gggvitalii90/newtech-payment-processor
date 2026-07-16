from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payment_processor.env import load_env
from payment_processor.fintablo_client import FinTabloClient, load_fintablo_settings
from payment_processor.fintablo_google_income import append_missing_fintablo_expenses, fetch_non_cash_expense_records, highlight_existing_fintablo_expense_rows
from payment_processor.google_api import build_sheets_service, get_credentials, load_google_settings
from payment_processor.google_payments import FINAL_SHEET_NAME


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append missing non-cash FinTablo expense rows to Google final sheet")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--sheet-name", default=FINAL_SHEET_NAME)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--highlight-existing", action="store_true", help="Reapply pale orange fill to existing fintablo expense rows")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    env = load_env()
    google_settings = load_google_settings(env)
    credentials = get_credentials(google_settings)
    sheets = build_sheets_service(credentials)
    client = FinTabloClient(load_fintablo_settings(env))
    records = fetch_non_cash_expense_records(client, start, end)
    summary = append_missing_fintablo_expenses(
        sheets,
        google_settings.archive_spreadsheet_id,
        records,
        sheet_name=args.sheet_name,
        apply=args.apply,
    )
    if args.apply and args.highlight_existing:
        summary["google_expense_highlighted"] = highlight_existing_fintablo_expense_rows(
            sheets,
            google_settings.archive_spreadsheet_id,
            sheet_name=args.sheet_name,
        )
    summary.update({
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "sheet_name": args.sheet_name,
        "apply": bool(args.apply),
    })
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

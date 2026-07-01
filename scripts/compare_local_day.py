from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from analyze_manual_matching import match_date  # noqa: E402
from compare_manual_final import DEFAULT_MANUAL_SPREADSHEET_ID, load_manual_rows  # noqa: E402
from payment_processor.env import load_env  # noqa: E402
from payment_processor.google_api import build_sheets_service, get_credentials, load_google_settings  # noqa: E402

MONTH_SHEETS = {
    "04": "\u0410\u043f\u0440\u0435\u043b\u044c 2026",
    "05": "\u041c\u0430\u0439 2026",
    "06": "\u0418\u044e\u043d\u044c 2026",
}


def iso_from_dmy(value: str) -> str:
    day, month, year = value.split(".")
    return f"{year}-{month}-{day}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="dd.mm.yyyy")
    parser.add_argument("--manual-spreadsheet-id", default=DEFAULT_MANUAL_SPREADSHEET_ID)
    parser.add_argument("--final-csv", default="reports/payment_final_full.csv")
    parser.add_argument("--output")
    args = parser.parse_args()

    month = args.date[3:5]
    manual_sheet = MONTH_SHEETS[month]
    settings = load_google_settings(load_env())
    sheets = build_sheets_service(get_credentials(settings))
    manual = [row for row in load_manual_rows(sheets, args.manual_spreadsheet_id, [manual_sheet]) if row["values"][1] == args.date]
    iso = iso_from_dmy(args.date)
    final = []
    with open(args.final_csv, encoding="utf-8-sig") as file:
        for index, row in enumerate(csv.reader(file), start=1):
            if index == 1:
                continue
            values = row + [""] * 14
            if values[1] in {iso, args.date}:
                values[1] = args.date
                final.append({"sheet": "local", "row": index, "values": values[:14]})
    rows = match_date(manual, final, args.date)
    output = Path(args.output or f"reports/manual_matching_{iso}_local.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["date", "status", "score", "amount", "manual_sheet", "manual_row", "final_row", "diff_fields", "manual", "final"])
        writer.writeheader()
        writer.writerows(rows)
    status_counts = Counter(row["status"] for row in rows)
    field_counts = Counter()
    for row in rows:
        if row["status"] == "matched_with_diffs":
            for field in row["diff_fields"].split(", "):
                if field:
                    field_counts[field] += 1
    print("manual", len(manual), "final", len(final), dict(status_counts))
    print("diff_fields", dict(field_counts.most_common()))
    print("report=" + str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

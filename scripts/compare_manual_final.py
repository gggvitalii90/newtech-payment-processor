
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date, datetime
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payment_processor.env import load_env
from payment_processor.google_api import build_sheets_service, get_credentials, load_google_settings

DEFAULT_MANUAL_SPREADSHEET_ID = "1zPEtx_qNOWypYcvCJCvwckqAc8FP8qVFB7sgFW9F57I"
DEFAULT_MANUAL_SHEETS = ["\u0410\u043f\u0440\u0435\u043b\u044c 2026", "\u041c\u0430\u0439 2026", "\u0418\u044e\u043d\u044c 2026"]
FINAL_SHEET = "\u0418\u0442\u043e\u0433\u043e\u0432\u0430\u044f"

FIELDS = [
    (2, "\u0422\u0438\u043f \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0438"),
    (3, "\u0422\u0438\u043f \u043e\u043f\u043b\u0430\u0442\u044b"),
    (4, "\u0411\u0430\u043d\u043a"),
    (7, "\u041e\u0431\u044a\u0435\u043a\u0442"),
    (8, "\u041f\u0440\u043e\u0435\u043a\u0442"),
    (9, "\u0421\u0442\u0430\u0442\u044c\u044f \u0431\u044e\u0434\u0436\u0435\u0442\u0430"),
    (10, "\u041e\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0439"),
    (11, "\u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435"),
    (12, "\u0421\u0441\u044b\u043b\u043a\u0430"),
]


def cents(value: str) -> str:
    text = str(value or "").replace("\xa0", " ").strip()
    text = re.sub(r"[^0-9,.-]", "", text).replace(",", ".")
    if not text:
        return ""
    try:
        return str(int((Decimal(text) * 100).quantize(Decimal("1"))))
    except InvalidOperation:
        return re.sub(r"\D+", "", text)


def norm(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value or "").strip().casefold().replace("?", "?").replace('"', "").replace("?", "").replace("?", ""),
    )


def soft(value: str) -> str:
    return norm(value).replace(".", "").replace(",", "").replace("-", " ")


def dmy(value: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        year, month, day = text.split("-")
        return f"{day}.{month}.{year}"
    return text


def file_id(link: str) -> str:
    text = str(link or "").strip()
    if text in {"", "-"}:
        return text
    match = (
        re.search(r"/d/([^/]+)", text)
        or re.search(r"[?&]id=([^&]+)", text)
        or re.search(r"/spreadsheets/d/([^/]+)", text)
        or re.search(r"/document/d/([^/]+)", text)
    )
    return match.group(1) if match else text


def links_equal(left: str, right: str) -> bool:
    return file_id(left) == file_id(right) or (str(left).strip() == "-" and str(right).strip() == "")


def row_key(values: list[str]) -> tuple[str, str, str, str]:
    row = values + [""] * 15
    return (dmy(row[1]), norm(row[5]), norm(row[6]), cents(row[13]))


def load_manual_rows(sheets, spreadsheet_id: str, sheet_names: list[str]) -> list[dict]:
    result = []
    for sheet_name in sheet_names:
        rows = sheets.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'!A:O",
        ).execute().get("values", [])
        for index, row in enumerate(rows, start=1):
            values = row + [""] * 15
            if re.fullmatch(r"\d{2}\.\d{2}\.2026", values[1].strip()):
                result.append({"sheet": sheet_name, "row": index, "values": values[:15]})
    return result


def load_final_rows(sheets, spreadsheet_id: str, months: set[str]) -> list[dict]:
    rows = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{FINAL_SHEET}'!A:N",
    ).execute().get("values", [])
    result = []
    for index, row in enumerate(rows, start=1):
        values = row + [""] * 14
        values[1] = dmy(values[1])
        if re.fullmatch(r"\d{2}\.\d{2}\.2026", values[1]) and values[1][3:5] in months:
            result.append({"sheet": FINAL_SHEET, "row": index, "values": values[:14]})
    return result


def compare(manual_rows: list[dict], final_rows: list[dict]) -> list[dict]:
    manual_by_key = defaultdict(list)
    final_by_key = defaultdict(list)
    for row in manual_rows:
        manual_by_key[row_key(row["values"])].append(row)
    for row in final_rows:
        final_by_key[row_key(row["values"])].append(row)

    issues = []
    for key in sorted(set(manual_by_key) | set(final_by_key)):
        manual_items = manual_by_key.get(key, [])
        final_items = final_by_key.get(key, [])
        if len(manual_items) > len(final_items):
            for item in manual_items[len(final_items):]:
                issues.append({
                    "type": "missing",
                    "date": key[0],
                    "month": key[0][3:5],
                    "manual_sheet": item["sheet"],
                    "manual_row": item["row"],
                    "final_row": "",
                    "field": "",
                    "manual": "|".join(item["values"][:14]),
                    "final": "",
                })
        elif len(final_items) > len(manual_items):
            for item in final_items[len(manual_items):]:
                issues.append({
                    "type": "extra",
                    "date": key[0],
                    "month": key[0][3:5],
                    "manual_sheet": "",
                    "manual_row": "",
                    "final_row": item["row"],
                    "field": "",
                    "manual": "",
                    "final": "|".join(item["values"][:14]),
                })
        for manual, final in zip(manual_items, final_items):
            manual_values = manual["values"] + [""] * 15
            final_values = final["values"] + [""] * 14
            for index, field_name in FIELDS:
                left = (manual_values[index] or "").strip()
                right = (final_values[index] or "").strip()
                if index == 12:
                    if links_equal(left, right):
                        continue
                elif left == right or soft(left) == soft(right):
                    continue
                issues.append({
                    "type": "field_diff",
                    "date": key[0],
                    "month": key[0][3:5],
                    "manual_sheet": manual["sheet"],
                    "manual_row": manual["row"],
                    "final_row": final["row"],
                    "field": field_name,
                    "manual": left,
                    "final": right,
                })
    return issues


def write_report(path: Path, issues: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["type", "date", "month", "manual_sheet", "manual_row", "final_row", "field", "manual", "final"],
        )
        writer.writeheader()
        writer.writerows(issues)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manual-spreadsheet-id", default=DEFAULT_MANUAL_SPREADSHEET_ID)
    parser.add_argument("--manual-sheet", action="append", dest="manual_sheets")
    parser.add_argument("--output", default="reports/manual_vs_itogovaya_apr_jun_actionable_diff.csv")
    parser.add_argument("--start")
    parser.add_argument("--end")
    args = parser.parse_args()

    settings = load_google_settings(load_env())
    sheets = build_sheets_service(get_credentials(settings))
    sheet_names = args.manual_sheets or DEFAULT_MANUAL_SHEETS
    months = {"04", "05", "06"}
    manual_rows = load_manual_rows(sheets, args.manual_spreadsheet_id, sheet_names)
    final_rows = load_final_rows(sheets, settings.archive_spreadsheet_id, months)
    if args.start or args.end:
        start = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else date.min
        end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.max
        manual_rows = [row for row in manual_rows if start <= datetime.strptime(row["values"][1], "%d.%m.%Y").date() <= end]
        final_rows = [row for row in final_rows if start <= datetime.strptime(row["values"][1], "%d.%m.%Y").date() <= end]
    issues = compare(manual_rows, final_rows)
    output = Path(args.output)
    write_report(output, issues)

    print(f"manual_rows={len(manual_rows)} final_rows={len(final_rows)} issues={len(issues)} report={output}")
    print("by_type", dict(Counter(issue["type"] for issue in issues)))
    print("by_month", dict(Counter(issue["month"] for issue in issues)))
    print("by_field", dict(Counter(issue["field"] for issue in issues if issue["type"] == "field_diff").most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

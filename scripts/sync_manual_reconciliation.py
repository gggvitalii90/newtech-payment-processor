"""Mirror the manually maintained workbook and reconcile it with automatic output."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payment_processor.env import load_env
from payment_processor.google_api import build_sheets_service, get_credentials, load_google_settings
from compare_manual_final import (  # noqa: E402
    DEFAULT_MANUAL_SPREADSHEET_ID,
    compare,
    load_final_rows,
    load_manual_rows,
)

MIRROR_SHEET = "\u0420\u0443\u0447\u043d\u0430\u044f \u0441\u0432\u0435\u0440\u043a\u0430"
MONTH_NAMES = (
    "\u042f\u043d\u0432\u0430\u0440\u044c", "\u0444\u0435\u0432\u0440\u0430\u043b\u044c", "\u043c\u0430\u0440\u0442",
    "\u0410\u043f\u0440\u0435\u043b\u044c", "\u041c\u0430\u0439", "\u0418\u044e\u043d\u044c", "\u0418\u044e\u043b\u044c",
    "\u0410\u0432\u0433\u0443\u0441\u0442", "\u0421\u0435\u043d\u0442\u044f\u0431\u0440\u044c", "\u041e\u043a\u0442\u044f\u0431\u0440\u044c",
    "\u041d\u043e\u044f\u0431\u0440\u044c", "\u0414\u0435\u043a\u0430\u0431\u0440\u044c",
)


def _metadata(sheets, spreadsheet_id: str) -> dict:
    return sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()


def _sheet_titles(metadata: dict) -> list[str]:
    return [str(item.get("properties", {}).get("title", "")) for item in metadata.get("sheets", [])]


def discover_manual_sheets(sheets, spreadsheet_id: str) -> list[str]:
    titles = _sheet_titles(_metadata(sheets, spreadsheet_id))
    known = {f"{month} 2026" for month in MONTH_NAMES}
    found = [title for title in titles if title in known]
    return sorted(found, key=lambda title: (MONTH_NAMES.index(title.rsplit(" ", 1)[0]), title))


def ensure_mirror_sheet(sheets, spreadsheet_id: str) -> None:
    if MIRROR_SHEET in _sheet_titles(_metadata(sheets, spreadsheet_id)):
        return
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": MIRROR_SHEET}}}]},
    ).execute()


def copy_to_mirror(sheets, source_id: str, archive_id: str, sheet_names: list[str]) -> tuple[int, int]:
    """Copy the manual workbook into one stable archive tab; return rows and columns."""
    ensure_mirror_sheet(sheets, archive_id)
    rows: list[list[str]] = []
    header: list[str] | None = None
    for sheet_name in sheet_names:
        values = sheets.spreadsheets().values().get(
            spreadsheetId=source_id, range=f"'{sheet_name}'!A:O"
        ).execute().get("values", [])
        if values and header is None:
            header = list(values[0][:15])
        for row in values[1:]:
            padded = list(row[:15]) + [""] * max(0, 15 - len(row))
            if any(str(item).strip() for item in padded):
                rows.append(padded[:15])
    if header is None:
        header = ["№", "Дата"] + [f"Колонка {index}" for index in range(3, 16)]
    payload = [header[:15]] + rows
    sheets.spreadsheets().values().clear(
        spreadsheetId=archive_id, range=f"'{MIRROR_SHEET}'!A:O", body={}
    ).execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=archive_id,
        range=f"'{MIRROR_SHEET}'!A1:O{len(payload)}",
        valueInputOption="USER_ENTERED",
        body={"values": payload},
    ).execute()
    return len(rows), len(header)


def _parse_date(value: str) -> date | None:
    text = str(value or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def write_report(path: Path, issues: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["type", "date", "month", "manual_sheet", "manual_row", "final_row", "field", "manual", "final"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(issues)


def run(args: argparse.Namespace) -> dict:
    settings = load_google_settings(load_env())
    sheets = build_sheets_service(get_credentials(settings))
    source_id = args.manual_spreadsheet_id
    source_sheets = args.manual_sheet or discover_manual_sheets(sheets, source_id)
    if not source_sheets:
        raise RuntimeError("Не найдены листы ручной таблицы с месяцами 2026")

    mirror_rows, mirror_columns = copy_to_mirror(
        sheets, source_id, settings.archive_spreadsheet_id, source_sheets
    )
    manual_rows = load_manual_rows(sheets, source_id, source_sheets)
    months = {f"{month:02d}" for month in range(1, 13)}
    final_rows = load_final_rows(sheets, settings.archive_spreadsheet_id, months)

    start = _parse_date(args.start) if args.start else date.min
    end = _parse_date(args.end) if args.end else date.max
    if start is None or end is None or end < start:
        raise ValueError("Период должен быть в формате YYYY-MM-DD")
    manual_rows = [row for row in manual_rows if start <= (_parse_date(row["values"][1]) or date.min) <= end]
    final_rows = [row for row in final_rows if start <= (_parse_date(row["values"][1]) or date.min) <= end]
    issues = compare(manual_rows, final_rows)
    output = Path(args.output)
    write_report(output, issues)
    by_type = Counter(issue["type"] for issue in issues)
    result = {
        "manual_sheets": source_sheets,
        "mirror_sheet": MIRROR_SHEET,
        "mirror_rows": mirror_rows,
        "mirror_columns": mirror_columns,
        "manual_rows": len(manual_rows),
        "final_rows": len(final_rows),
        "matched_rows": max(0, len(manual_rows) - by_type.get("missing", 0)),
        "issues": len(issues),
        "missing": by_type.get("missing", 0),
        "extra": by_type.get("extra", 0),
        "field_diff": by_type.get("field_diff", 0),
        "report": str(output),
    }
    print(json.dumps(result, ensure_ascii=False))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manual-spreadsheet-id", default=DEFAULT_MANUAL_SPREADSHEET_ID)
    parser.add_argument("--manual-sheet", action="append")
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    parser.add_argument("--output", default="reports/manual_reconciliation.csv")
    args = parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

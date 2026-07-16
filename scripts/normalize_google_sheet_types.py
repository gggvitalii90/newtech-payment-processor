from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from payment_processor.env import load_env
from payment_processor.google_api import build_sheets_service, get_credentials, load_google_settings
from payment_processor.google_archive import setup_archive_sheet
from payment_processor.google_payments import setup_payment_sheets

GOOGLE_DATE_BASE = date(1899, 12, 30)


@dataclass(frozen=True)
class SheetTypeSpec:
    name: str
    date_columns: dict[str, str]
    amount_columns: set[str]


SHEET_SPECS = [
    SheetTypeSpec("Итоговая", {"Дата": "DATE"}, {"Сумма"}),
    SheetTypeSpec("Итоговая ИС", {"Дата": "DATE"}, {"Сумма"}),
    SheetTypeSpec("Архив ПП", {"Дата": "DATE"}, {"Сумма"}),
    SheetTypeSpec("Архив счетов", {"Дата MAX": "DATE_TIME", "Дата счета": "DATE"}, {"Сумма"}),
]


@dataclass
class ResolvedSheetTypeSpec:
    name: str
    width: int
    date_columns: dict[int, str]
    amount_columns: set[int]


@dataclass
class NormalizeSummary:
    sheet: str
    rows: int = 0
    date_cells: int = 0
    amount_cells: int = 0
    skipped_dates: int = 0
    skipped_amounts: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize Google Sheets date and amount cells to typed values.")
    parser.add_argument("--apply", action="store_true", help="Write converted values. Without this flag only prints a dry-run summary.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    env = load_env()
    settings = load_google_settings(env)
    sheets = build_sheets_service(get_credentials(settings))
    spreadsheet_id = settings.archive_spreadsheet_id
    if not spreadsheet_id:
        raise RuntimeError("GOOGLE_ARCHIVE_SPREADSHEET_ID is empty")

    # Keep sheet-level filters, headers and column number formats in sync with code first.
    if args.apply:
        setup_payment_sheets(sheets, spreadsheet_id)
        setup_archive_sheet(sheets, spreadsheet_id, settings.archive_sheet_name)

    metadata = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheet_ids = {
        item.get("properties", {}).get("title", ""): item.get("properties", {}).get("sheetId")
        for item in metadata.get("sheets", [])
    }
    for spec in SHEET_SPECS:
        if spec.name not in sheet_ids:
            print(f"{spec.name}: sheet not found")
            continue
        resolved = resolve_spec(sheets, spreadsheet_id, spec)
        if resolved.width == 0:
            print(f"{spec.name}: no headers")
            continue
        summary = normalize_sheet(sheets, spreadsheet_id, sheet_ids[spec.name], resolved, apply=args.apply)
        print(
            f"{summary.sheet}: rows={summary.rows}, dates={summary.date_cells}, "
            f"amounts={summary.amount_cells}, skipped_dates={summary.skipped_dates}, "
            f"skipped_amounts={summary.skipped_amounts}"
        )


def resolve_spec(sheets, spreadsheet_id: str, spec: SheetTypeSpec) -> ResolvedSheetTypeSpec:
    response = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{spec.name}'!A1:AZ1",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    headers = [str(value).strip() for value in response.get("values", [[]])[0]]
    header_index = {header: index for index, header in enumerate(headers) if header}
    return ResolvedSheetTypeSpec(
        name=spec.name,
        width=len(headers),
        date_columns={header_index[name]: kind for name, kind in spec.date_columns.items() if name in header_index},
        amount_columns={header_index[name] for name in spec.amount_columns if name in header_index},
    )


def normalize_sheet(sheets, spreadsheet_id: str, sheet_id: int, spec: ResolvedSheetTypeSpec, apply: bool) -> NormalizeSummary:
    response = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{spec.name}'!A2:{column_letter(spec.width)}",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    rows = response.get("values", [])
    summary = NormalizeSummary(sheet=spec.name, rows=len(rows))
    if not rows:
        return summary

    converted_rows: list[dict[str, Any]] = []
    for row in rows:
        values = []
        padded = [str(row[index]).strip() if index < len(row) else "" for index in range(spec.width)]
        for index, value in enumerate(padded):
            cell, changed, skipped = typed_cell(value, index, spec)
            if index in spec.date_columns:
                summary.date_cells += int(changed)
                summary.skipped_dates += int(skipped)
            elif index in spec.amount_columns:
                summary.amount_cells += int(changed)
                summary.skipped_amounts += int(skipped)
            values.append(cell)
        converted_rows.append({"values": values})

    if apply:
        requests = []
        for start, chunk in chunked(list(enumerate(converted_rows)), 400):
            requests.append(
                {
                    "updateCells": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": start + 1,
                            "endRowIndex": start + 1 + len(chunk),
                            "startColumnIndex": 0,
                            "endColumnIndex": spec.width,
                        },
                        "rows": [row for _index, row in chunk],
                        "fields": "userEnteredValue",
                    }
                }
            )
        requests.extend(format_requests(sheet_id, spec))
        for request_chunk in chunked_requests(requests, 20):
            sheets.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": request_chunk}).execute()
    return summary


def typed_cell(value: str, column_index_value: int, spec: ResolvedSheetTypeSpec) -> tuple[dict[str, Any], bool, bool]:
    if not value:
        return {}, False, False
    if column_index_value in spec.date_columns:
        parsed = parse_date_or_datetime(value)
        if parsed is None:
            return {"userEnteredValue": {"stringValue": value}}, False, True
        return {"userEnteredValue": {"numberValue": google_serial(parsed)}}, True, False
    if column_index_value in spec.amount_columns:
        parsed_amount = parse_amount(value)
        if parsed_amount is None:
            return {"userEnteredValue": {"stringValue": value}}, False, True
        return {"userEnteredValue": {"numberValue": float(parsed_amount)}}, True, False
    return {"userEnteredValue": {"stringValue": value}}, False, False


def parse_date_or_datetime(value: str) -> datetime | None:
    text = normalize_date_text(value)
    for fmt in (
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%d.%m.%Y",
        "%d.%m.%y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def normalize_date_text(value: str) -> str:
    text = str(value or "").strip().replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    if text.endswith("Z"):
        text = text[:-1]
    return text


def google_serial(value: datetime) -> float:
    days = (value.date() - GOOGLE_DATE_BASE).days
    seconds = value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1_000_000
    return days + seconds / 86400


def parse_amount(value: str) -> Decimal | None:
    text = str(value or "").strip().replace("\u00a0", " ")
    if not text:
        return None
    text = text.replace("+", "")
    text = re.sub(r"[^0-9,\.\-]", "", text)
    if not text or text in {"-", ",", "."}:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def format_requests(sheet_id: int, spec: ResolvedSheetTypeSpec) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for index, kind in spec.date_columns.items():
        pattern = "dd.mm.yyyy hh:mm:ss" if kind == "DATE_TIME" else "dd.mm.yyyy"
        requests.append(number_format_request(sheet_id, index, {"type": kind, "pattern": pattern}))
    for index in spec.amount_columns:
        requests.append(number_format_request(sheet_id, index, {"type": "NUMBER", "pattern": "#,##0.00"}))
    return requests


def number_format_request(sheet_id: int, index: int, number_format: dict[str, str]) -> dict[str, Any]:
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "startColumnIndex": index,
                "endColumnIndex": index + 1,
            },
            "cell": {"userEnteredFormat": {"numberFormat": number_format}},
            "fields": "userEnteredFormat.numberFormat",
        }
    }


def column_letter(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def chunked(items: list[Any], size: int) -> Iterable[tuple[int, list[Any]]]:
    for offset in range(0, len(items), size):
        chunk = items[offset : offset + size]
        yield chunk[0][0], chunk


def chunked_requests(items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


if __name__ == "__main__":
    main()
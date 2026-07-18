from __future__ import annotations

import re
import time

from googleapiclient.errors import HttpError
from datetime import datetime, timedelta
from typing import Callable, Iterable

from .models import COLUMNS, PaymentRecord




def _execute_google_request(request, retries: int = 5):
    for attempt in range(retries):
        try:
            return request.execute()
        except HttpError as exc:
            if exc.resp.status not in {429, 500, 502, 503, 504} or attempt == retries - 1:
                raise
            time.sleep(65 if exc.resp.status == 429 else 2 ** attempt)
    return request.execute()
FINAL_SHEET_NAME = "Итоговая"
FINAL_IS_SHEET_NAME = "Итоговая ИС"
PAYMENT_ARCHIVE_SHEET_NAME = "Архив ПП"
FINAL_COLUMNS = ["№", *COLUMNS[1:]]
FINTABLO_INCOME_FILL = {"red": 0.86, "green": 0.96, "blue": 0.86}
FINTABLO_EXPENSE_FILL = {"red": 1.0, "green": 0.91, "blue": 0.78}
OPERATION_INCOME = "\u041f\u0440\u0438\u0445\u043e\u0434"
OPERATION_EXPENSE = "\u0420\u0430\u0441\u0445\u043e\u0434"
PAYMENT_ARCHIVE_COLUMNS = [
    "№",
    "Дата",
    "Тип операции",
    "Тип оплаты",
    "Банк",
    "Контрагент",
    "Номер счета",
    "Назначение платежа",
    "Ссылка на ПП",
    "Сумма",
]


def payment_archive_row(record: PaymentRecord) -> list[str]:
    return [
        record.name,
        _display_date(record.date),
        record.operation_type,
        record.payment_type,
        record.bank,
        record.counterparty,
        record.invoice_number,
        record.purpose,
        record.invoice_link,
        record.amount,
    ]



def final_row(record: PaymentRecord) -> list[str]:
    row = record.as_row()
    row[1] = _display_date(row[1])
    return row


def _display_date(value: str) -> str:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%d.%m.%Y")
        except ValueError:
            pass
    return text

def setup_payment_sheets(sheets_service, spreadsheet_id: str) -> None:
    metadata = _execute_google_request(sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id))
    existing = _sheet_ids(metadata)
    schemas = {
        FINAL_SHEET_NAME: (FINAL_COLUMNS, "N"),
        FINAL_IS_SHEET_NAME: (FINAL_COLUMNS, "N"),
        PAYMENT_ARCHIVE_SHEET_NAME: (PAYMENT_ARCHIVE_COLUMNS, "J"),
    }
    missing = [name for name in schemas if name not in existing]
    if missing:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": name}}} for name in missing]},
        ).execute()
        existing = _sheet_ids(sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute())
    for name, (columns, last_column) in schemas.items():
        headers = _read_headers(sheets_service, spreadsheet_id, name, "N")
        if headers != columns:
            if headers:
                _clear_values(sheets_service, spreadsheet_id, f"'{name}'!A1:N")
            _write_values(sheets_service, spreadsheet_id, f"'{name}'!A1:{last_column}1", [columns])
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": _format_requests(existing[name], len(columns))},
        ).execute()


def replace_final_rows(
    sheets_service,
    spreadsheet_id: str,
    records: Iterable[PaymentRecord],
    sheet_name: str = FINAL_SHEET_NAME,
) -> int:
    return _replace_rows(
        sheets_service, spreadsheet_id, sheet_name, records,
        FINAL_COLUMNS, "N", "N", final_row,
    )


def replace_payment_archive_rows(sheets_service, spreadsheet_id: str, records: Iterable[PaymentRecord]) -> int:
    return _replace_rows(
        sheets_service, spreadsheet_id, PAYMENT_ARCHIVE_SHEET_NAME, records,
        PAYMENT_ARCHIVE_COLUMNS, "J", "N", payment_archive_row,
    )


def upsert_final_rows(
    sheets_service,
    spreadsheet_id: str,
    records: Iterable[PaymentRecord],
    sheet_name: str = FINAL_SHEET_NAME,
) -> tuple[int, int]:
    return _upsert_rows(
        sheets_service, spreadsheet_id, sheet_name, records,
        FINAL_COLUMNS, "N", final_row, _final_row_key,
    )


def final_sheet_name_for_mode(mode: str) -> str:
    return FINAL_IS_SHEET_NAME if (mode or "").strip().upper() == "ИС" else FINAL_SHEET_NAME


def upsert_payment_archive(sheets_service, spreadsheet_id: str, records: Iterable[PaymentRecord]) -> tuple[int, int]:
    return _upsert_rows(
        sheets_service, spreadsheet_id, PAYMENT_ARCHIVE_SHEET_NAME, records,
        PAYMENT_ARCHIVE_COLUMNS, "J", payment_archive_row, _archive_row_key,
    )


def highlight_fintablo_final_rows(
    sheets_service,
    spreadsheet_id: str,
    sheet_name: str = FINAL_SHEET_NAME,
    extra_names: set[str] | None = None,
) -> tuple[int, int]:
    response = _execute_google_request(sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A2:N",
    ))
    income_rows: list[int] = []
    expense_rows: list[int] = []
    for offset, row in enumerate(response.get("values", []), start=2):
        values = list(row) + [""] * len(FINAL_COLUMNS)
        name = _normalize(values[0])
        if not name.startswith("fintablo:"):
            continue
        if extra_names is not None and name not in extra_names:
            continue
        operation = _normalize(values[2])
        if operation == _normalize(OPERATION_INCOME):
            income_rows.append(offset)
        elif operation == _normalize(OPERATION_EXPENSE):
            expense_rows.append(offset)
    _format_row_numbers(sheets_service, spreadsheet_id, sheet_name, income_rows, FINTABLO_INCOME_FILL)
    _format_row_numbers(sheets_service, spreadsheet_id, sheet_name, expense_rows, FINTABLO_EXPENSE_FILL)
    return len(income_rows), len(expense_rows)


def _replace_rows(
    sheets_service,
    spreadsheet_id: str,
    sheet_name: str,
    records: Iterable[PaymentRecord],
    columns: list[str],
    write_last_column: str,
    clear_last_column: str,
    row_builder: Callable[[PaymentRecord], list[str]],
) -> int:
    rows = [row_builder(record) for record in records]
    _clear_values(sheets_service, spreadsheet_id, f"'{sheet_name}'!A2:{clear_last_column}")
    values = [columns, *rows]
    _write_values(
        sheets_service,
        spreadsheet_id,
        f"'{sheet_name}'!A1:{write_last_column}{len(values)}",
        values,
    )
    return len(rows)



def delete_rows_for_dates(
    sheets_service,
    spreadsheet_id: str,
    sheet_name: str,
    dates: Iterable[str],
    last_column: str = "N",
) -> int:
    targets = {_normalize(value) for value in dates if str(value or "").strip()}
    if not targets:
        return 0
    response = _execute_google_request(sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A2:{last_column}",
    ))
    rows = response.get("values", [])
    row_numbers = [
        index + 2
        for index, row in enumerate(rows)
        if len(row) > 1 and _normalize(row[1]) in targets
    ]
    if not row_numbers:
        return 0
    sheet_id = _sheet_ids(_execute_google_request(sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id)))[sheet_name]
    ranges: list[tuple[int, int]] = []
    start = previous = row_numbers[0]
    for number in row_numbers[1:]:
        if number == previous + 1:
            previous = number
        else:
            ranges.append((start, previous))
            start = previous = number
    ranges.append((start, previous))
    requests = [
        {"deleteDimension": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": start - 1, "endIndex": end}}}
        for start, end in reversed(ranges)
    ]
    sheets_service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
    return len(row_numbers)

def _upsert_rows(
    sheets_service,
    spreadsheet_id: str,
    sheet_name: str,
    records: Iterable[PaymentRecord],
    columns: list[str],
    last_column: str,
    row_builder: Callable[[PaymentRecord], list[str]],
    key_builder: Callable[[list[str]], tuple[str, ...] | None],
) -> tuple[int, int]:
    headers = _read_headers(sheets_service, spreadsheet_id, sheet_name, last_column)
    if not _headers_match_schema(headers, columns):
        if headers:
            _clear_values(sheets_service, spreadsheet_id, f"'{sheet_name}'!A1:N")
        _write_values(sheets_service, spreadsheet_id, f"'{sheet_name}'!A1:{last_column}1", [columns])
    response = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A2:{last_column}",
    ).execute()
    existing_by_key = {
        key: index + 2
        for index, row in enumerate(response.get("values", []))
        if (key := key_builder(row)) is not None
    }
    incoming_by_key: dict[tuple[str, ...], list[list[str]]] = {}
    for record in records:
        row = row_builder(record)
        key = key_builder(row)
        if key is not None:
            rows_for_key = incoming_by_key.setdefault(key, [])
            if row not in rows_for_key:
                rows_for_key.append(row)
    updates: list[tuple[str, list[list[str]]]] = []
    new_rows: list[list[str]] = []
    for key, rows in incoming_by_key.items():
        row_number = existing_by_key.get(key)
        if len(rows) > 1:
            new_rows.extend(rows)
        elif row_number is None:
            new_rows.append(rows[0])
        else:
            updates.append((f"'{sheet_name}'!A{row_number}:{last_column}{row_number}", [rows[0]]))
    if updates:
        _batch_update_values(sheets_service, spreadsheet_id, updates)
    if new_rows:
        sheets_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": new_rows},
        ).execute()
    return len(updates), len(new_rows)


def _final_row_key(row: list[str]) -> tuple[str, ...] | None:
    values = list(row) + [""] * len(FINAL_COLUMNS)
    name = _normalize(values[0])
    if name.endswith(".pdf"):
        return _payment_identity_key(
            values[0], values[1], values[2], values[3], values[4],
            values[5], values[6], values[11], values[13],
        )
    composite = tuple(_normalize(values[index]) for index in (0, 1, 5, 6, 13, 11))
    return ("operation", *composite) if any(composite) else None


def _archive_row_key(row: list[str]) -> tuple[str, ...] | None:
    values = list(row) + [""] * len(PAYMENT_ARCHIVE_COLUMNS)
    if not any(values):
        return None
    return _payment_identity_key(
        values[0], values[1], values[2], values[3], values[4],
        values[5], values[6], values[7], values[9],
    )


def _payment_identity_key(
    file_name: str,
    payment_date: str,
    operation_type: str,
    payment_type: str,
    bank: str,
    counterparty: str,
    invoice_number: str,
    purpose: str,
    amount: str,
) -> tuple[str, ...]:
    return (
        "payment",
        _payment_document_number(file_name),
        *(_normalize(value) for value in (
            payment_date, operation_type, payment_type, bank, counterparty,
            invoice_number,
        )),
    )


def _payment_document_number(file_name: str) -> str:
    normalized = _normalize(file_name)
    match = re.search(r"(?:№|no[.]?)\s*(\d+)", normalized, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"_\d{2}[.]\d{2}[.]\d{4}_(\d+)(?:_|[.])", normalized)
    if match:
        return match.group(1)
    match = re.search(r"_(\d+)[.]pdf$", normalized)
    return match.group(1) if match else normalized

def _headers_match_schema(headers: list[str], columns: list[str]) -> bool:
    if headers == columns:
        return True
    return len(headers) >= len(columns) and headers[:len(columns)] == columns


def _read_headers(sheets_service, spreadsheet_id: str, sheet_name: str, last_column: str) -> list[str]:
    response = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A1:{last_column}1",
    ).execute()
    rows = response.get("values", [])
    return [str(value).strip() for value in rows[0]] if rows else []


def _clear_values(sheets_service, spreadsheet_id: str, range_name: str) -> None:
    sheets_service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        body={},
    ).execute()


def _write_values(sheets_service, spreadsheet_id: str, range_name: str, values: list[list[str]]) -> None:
    sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()


def _batch_update_values(
    sheets_service,
    spreadsheet_id: str,
    updates: list[tuple[str, list[list[str]]]],
) -> None:
    if not updates:
        return
    sheets_service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "valueInputOption": "USER_ENTERED",
            "data": [
                {"range": range_name, "values": values}
                for range_name, values in updates
            ],
        },
    ).execute()


def _sheet_ids(metadata: dict) -> dict[str, int]:
    return {
        sheet.get("properties", {}).get("title", ""): sheet.get("properties", {}).get("sheetId")
        for sheet in metadata.get("sheets", [])
    }


def _format_requests(sheet_id: int, column_count: int) -> list[dict]:
    amount_column_index = column_count - 1
    return [
        {"updateSheetProperties": {"properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}}, "fields": "gridProperties.frozenRowCount"}},
        {"setBasicFilter": {"filter": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": column_count}}}},
        {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": column_count}, "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}, "textFormat": {"bold": True}}}, "fields": "userEnteredFormat(backgroundColor,textFormat)"}},
        {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": column_count}, "cell": {"userEnteredFormat": {"textFormat": {"bold": False}}}, "fields": "userEnteredFormat.textFormat.bold"}},
        _number_format_request(sheet_id, 1, {"type": "DATE", "pattern": "dd.mm.yyyy"}),
        _number_format_request(sheet_id, amount_column_index, {"type": "NUMBER", "pattern": "#,##0.00"}),
    ]


def _number_format_request(sheet_id: int, column_index: int, number_format: dict) -> dict:
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "startColumnIndex": column_index,
                "endColumnIndex": column_index + 1,
            },
            "cell": {"userEnteredFormat": {"numberFormat": number_format}},
            "fields": "userEnteredFormat.numberFormat",
        }
    }


def _format_row_numbers(
    sheets_service,
    spreadsheet_id: str,
    sheet_name: str,
    row_numbers: list[int],
    background_color: dict[str, float],
) -> None:
    if not row_numbers:
        return
    metadata = _execute_google_request(sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id))
    sheet_id = _sheet_ids(metadata)[sheet_name]
    ranges: list[tuple[int, int]] = []
    start = previous = row_numbers[0]
    for row_number in row_numbers[1:]:
        if row_number == previous + 1:
            previous = row_number
            continue
        ranges.append((start, previous))
        start = previous = row_number
    ranges.append((start, previous))
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row - 1,
                    "endRowIndex": end_row,
                    "startColumnIndex": 0,
                    "endColumnIndex": len(FINAL_COLUMNS),
                },
                "cell": {"userEnteredFormat": {"backgroundColor": background_color}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        }
        for start_row, end_row in ranges
    ]
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()
def _normalize(value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").strip().lower().replace("\u0451", "\u0435"))
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalized, fmt).date().isoformat()
        except ValueError:
            pass
    # Google Sheets may return a date cell as its serial number (days since 1899-12-30).
    # Treat valid date-range serials as dates so cleanup/upsert comparisons still work.
    try:
        serial = float(normalized)
        if serial.is_integer() and 20000 <= serial <= 60000:
            return (datetime(1899, 12, 30) + timedelta(days=int(serial))).date().isoformat()
    except ValueError:
        pass
    return normalized

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .fintablo_transactions import fintablo_transactions_to_payment_records
from .google_payments import FINAL_COLUMNS, FINAL_SHEET_NAME, final_row, setup_payment_sheets
from .models import PaymentRecord


def _u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


OPERATION_INCOME = _u(r"\u041f\u0440\u0438\u0445\u043e\u0434")
PALE_GREEN = {"red": 0.86, "green": 0.96, "blue": 0.86}


def fetch_non_cash_income_records(client: Any, start: date, end: date) -> list[PaymentRecord]:
    moneybags = client.list_moneybags()
    categories = client.list_categories()
    partners = client.list_partners()
    deals = client.list_deals()
    directions = client.list_directions()
    moneybag_by_id = {_int_id(item.get("id")): item for item in moneybags}
    transactions = [
        tx for tx in client.list_transactions(date_from=_fintablo_date(start), date_to=_fintablo_date(end))
        if str(tx.get("group") or "").strip() == "income"
        and str(moneybag_by_id.get(_int_id(tx.get("moneybagId")), {}).get("type") or "").strip() != "nal"
    ]
    return fintablo_transactions_to_payment_records(
        transactions,
        moneybags=moneybags,
        categories=categories,
        partners=partners,
        deals=deals,
        directions=directions,
    )


def append_missing_fintablo_incomes(
    sheets_service: Any,
    spreadsheet_id: str,
    records: Iterable[PaymentRecord],
    *,
    sheet_name: str = FINAL_SHEET_NAME,
    apply: bool = False,
) -> dict[str, int]:
    setup_payment_sheets(sheets_service, spreadsheet_id)
    incoming = [record for record in records if _same_text(record.operation_type, OPERATION_INCOME)]
    existing_rows = _read_sheet_rows(sheets_service, spreadsheet_id, sheet_name)
    missing = find_missing_income_records(incoming, [PaymentRecord.from_row(row) for row in existing_rows])
    appended = 0
    if apply and missing:
        start_row, end_row = _append_final_rows(sheets_service, spreadsheet_id, sheet_name, missing)
        _highlight_rows(sheets_service, spreadsheet_id, sheet_name, start_row, end_row)
        appended = len(missing)
    return {
        "fintablo_income_records": len(incoming),
        "google_income_existing": len(incoming) - len(missing),
        "google_income_missing": len(missing),
        "google_income_appended": appended,
    }


def find_missing_income_records(
    incoming: Iterable[PaymentRecord],
    existing: Iterable[PaymentRecord],
) -> list[PaymentRecord]:
    existing_names = {_normalize(record.name) for record in existing if _normalize(record.name)}
    existing_keys = {_business_key(record) for record in existing if _business_key(record)}
    result: list[PaymentRecord] = []
    seen_keys: set[tuple[str, ...]] = set()
    for record in incoming:
        name = _normalize(record.name)
        key = _business_key(record)
        if name and name in existing_names:
            continue
        if key and key in existing_keys:
            continue
        if key and key in seen_keys:
            continue
        result.append(record)
        if key:
            seen_keys.add(key)
    return result


def _read_sheet_rows(sheets_service: Any, spreadsheet_id: str, sheet_name: str) -> list[list[str]]:
    response = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A2:N",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    return response.get("values", [])


def _append_final_rows(
    sheets_service: Any,
    spreadsheet_id: str,
    sheet_name: str,
    records: list[PaymentRecord],
) -> tuple[int, int]:
    response = sheets_service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [final_row(record) for record in records]},
    ).execute()
    updated_range = str(response.get("updates", {}).get("updatedRange") or "")
    match = re.search(r"!A(\d+):N(\d+)", updated_range)
    if match:
        return int(match.group(1)), int(match.group(2))
    row_count = len(_read_sheet_rows(sheets_service, spreadsheet_id, sheet_name))
    return row_count - len(records) + 2, row_count + 1


def _highlight_rows(
    sheets_service: Any,
    spreadsheet_id: str,
    sheet_name: str,
    start_row: int,
    end_row: int,
) -> None:
    metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheet_id = _sheet_ids(metadata)[sheet_name]
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": start_row - 1,
                            "endRowIndex": end_row,
                            "startColumnIndex": 0,
                            "endColumnIndex": len(FINAL_COLUMNS),
                        },
                        "cell": {"userEnteredFormat": {"backgroundColor": PALE_GREEN}},
                        "fields": "userEnteredFormat.backgroundColor",
                    }
                }
            ]
        },
    ).execute()


def _business_key(record: PaymentRecord) -> tuple[str, ...]:
    amount = _normalize_amount(record.amount)
    if not amount:
        return ()
    return (
        _normalize_date(record.date),
        _normalize(record.operation_type),
        _normalize(record.payment_type),
        _normalize(record.bank),
        _normalize(record.counterparty),
        _normalize(record.invoice_number),
        _normalize(record.purpose),
        amount,
    )


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower().replace("\u0451", "\u0435"))


def _normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return _normalize(text)


def _normalize_amount(value: Any) -> str:
    text = str(value or "").replace("\xa0", " ").replace(" ", "").replace(",", ".").strip("+")
    if not text:
        return ""
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return _normalize(text)
    return format(number.normalize(), "f")


def _same_text(left: Any, right: Any) -> bool:
    return _normalize(left) == _normalize(right)


def _fintablo_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def _int_id(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _sheet_ids(metadata: dict[str, Any]) -> dict[str, int]:
    return {
        sheet.get("properties", {}).get("title", ""): sheet.get("properties", {}).get("sheetId")
        for sheet in metadata.get("sheets", [])
    }

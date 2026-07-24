from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .fintablo_transactions import fintablo_transactions_to_payment_records
from .google_payments import FINAL_COLUMNS, FINAL_SHEET_NAME, final_row, setup_payment_sheets
from .models import PaymentRecord


def _u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


OPERATION_EXPENSE = _u(r"\u0420\u0430\u0441\u0445\u043e\u0434")
OPERATION_INCOME = _u(r"\u041f\u0440\u0438\u0445\u043e\u0434")
OPERATION_CONVERSION = _u(r"\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f")
PALE_GREEN = {"red": 0.86, "green": 0.96, "blue": 0.86}
PALE_ORANGE = {"red": 1.0, "green": 0.91, "blue": 0.78}
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


def fetch_non_cash_expense_records(client: Any, start: date, end: date) -> list[PaymentRecord]:
    moneybags = client.list_moneybags()
    categories = client.list_categories()
    partners = client.list_partners()
    deals = client.list_deals()
    directions = client.list_directions()
    moneybag_by_id = {_int_id(item.get("id")): item for item in moneybags}
    transactions = [
        tx for tx in client.list_transactions(date_from=_fintablo_date(start), date_to=_fintablo_date(end))
        if str(tx.get("group") or "").strip() == "outcome"
        and str(moneybag_by_id.get(_int_id(tx.get("moneybagId")), {}).get("type") or "").strip() != "nal"
    ]
    records = fintablo_transactions_to_payment_records(
        transactions,
        moneybags=moneybags,
        categories=categories,
        partners=partners,
        deals=deals,
        directions=directions,
    )
    return [_with_absolute_amount(record) for record in records]


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
    # Existing rows are colored only by the provenance-aware history pass.
    highlighted = 0
    return {
        "fintablo_income_records": len(incoming),
        "google_income_existing": len(incoming) - len(missing),
        "google_income_missing": len(missing),
        "google_income_appended": appended,
        "google_income_highlighted": highlighted,
    }


def append_missing_fintablo_expenses(
    sheets_service: Any,
    spreadsheet_id: str,
    records: Iterable[PaymentRecord],
    *,
    sheet_name: str = FINAL_SHEET_NAME,
    apply: bool = False,
) -> dict[str, int]:
    setup_payment_sheets(sheets_service, spreadsheet_id)
    all_expenses = [
        _with_absolute_amount(record)
        for record in records
        if _same_text(record.operation_type, OPERATION_EXPENSE)
        and not _same_text(record.operation_type, OPERATION_CONVERSION)
    ]
    incoming = aggregate_google_expense_records([record for record in all_expenses if is_safe_fintablo_expense_for_google(record)])
    existing_rows = _read_sheet_rows(sheets_service, spreadsheet_id, sheet_name)
    existing_records = [PaymentRecord.from_row(row) for row in existing_rows]
    existing_summary_rows = _existing_expense_summary_rows(existing_rows)
    summary_records = [record for record in incoming if _is_expense_summary_record(record)]
    passthrough_records = [record for record in incoming if not _is_expense_summary_record(record)]
    summaries_to_append = [
        record for record in summary_records
        if _normalize(record.name) not in existing_summary_rows
    ]
    summaries_to_update = [
        record for record in summary_records
        if _normalize(record.name) in existing_summary_rows
    ]
    missing = [
        *summaries_to_append,
        *find_missing_income_records(passthrough_records, existing_records),
    ]
    appended = 0
    updated = 0
    updated_rows: list[int] = []
    if apply and summaries_to_update:
        updates = []
        for record in summaries_to_update:
            row_number = existing_summary_rows[_normalize(record.name)]
            updates.append((f"'{sheet_name}'!A{row_number}:N{row_number}", [final_row(record)]))
            updated_rows.append(row_number)
        _batch_update_sheet_values(sheets_service, spreadsheet_id, updates)
        _highlight_row_numbers(sheets_service, spreadsheet_id, sheet_name, updated_rows, color=PALE_ORANGE)
        updated = len(summaries_to_update)
    if apply and missing:
        start_row, end_row = _append_final_rows(sheets_service, spreadsheet_id, sheet_name, missing)
        _highlight_rows(sheets_service, spreadsheet_id, sheet_name, start_row, end_row, color=PALE_ORANGE)
        appended = len(missing)
    legacy_row_numbers = _legacy_expense_summary_source_row_numbers(existing_rows, summary_records)
    legacy_removed = 0
    if apply and legacy_row_numbers:
        _delete_sheet_rows(sheets_service, spreadsheet_id, sheet_name, legacy_row_numbers)
        legacy_removed = len(legacy_row_numbers)
    # Existing rows are colored only by the provenance-aware history pass.
    highlighted = 0
    return {
        "fintablo_expense_records": len(all_expenses),
        "google_expense_candidates": len(incoming),
        "google_expense_skipped_as_document_flow": len(all_expenses) - len(incoming),
        "google_expense_existing": len(incoming) - len(missing),
        "google_expense_missing": len(missing),
        "google_expense_appended": appended,
        "google_expense_updated": updated,
        "google_expense_legacy_removed": legacy_removed,
        "google_expense_highlighted": highlighted,
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


def fintablo_income_row_numbers(rows: Iterable[list[str]], *, first_row_number: int = 2) -> list[int]:
    return _fintablo_operation_row_numbers(rows, OPERATION_INCOME, first_row_number=first_row_number)


def fintablo_expense_row_numbers(rows: Iterable[list[str]], *, first_row_number: int = 2) -> list[int]:
    return _fintablo_operation_row_numbers(rows, OPERATION_EXPENSE, first_row_number=first_row_number)


def _fintablo_operation_row_numbers(
    rows: Iterable[list[str]],
    operation_type: str,
    *,
    first_row_number: int = 2,
) -> list[int]:
    result: list[int] = []
    for offset, row in enumerate(rows):
        values = list(row) + [""] * len(FINAL_COLUMNS)
        if _normalize(values[0]).startswith("fintablo:") and _same_text(values[2], operation_type):
            result.append(first_row_number + offset)
    return result


def highlight_existing_fintablo_income_rows(
    sheets_service: Any,
    spreadsheet_id: str,
    *,
    sheet_name: str = FINAL_SHEET_NAME,
) -> int:
    rows = _read_sheet_rows(sheets_service, spreadsheet_id, sheet_name)
    row_numbers = fintablo_income_row_numbers(rows)
    if row_numbers:
        _highlight_row_numbers(sheets_service, spreadsheet_id, sheet_name, row_numbers)
    return len(row_numbers)


def highlight_existing_fintablo_expense_rows(
    sheets_service: Any,
    spreadsheet_id: str,
    *,
    sheet_name: str = FINAL_SHEET_NAME,
) -> int:
    rows = _read_sheet_rows(sheets_service, spreadsheet_id, sheet_name)
    row_numbers = fintablo_expense_row_numbers(rows)
    if row_numbers:
        _highlight_row_numbers(sheets_service, spreadsheet_id, sheet_name, row_numbers, color=PALE_ORANGE)
    return len(row_numbers)


def _read_sheet_rows(sheets_service: Any, spreadsheet_id: str, sheet_name: str) -> list[list[str]]:
    response = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A2:N",
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    return response.get("values", [])


def _existing_expense_summary_rows(rows: Iterable[list[str]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for index, row in enumerate(rows, start=2):
        name = _normalize((list(row) + [""])[0])
        if name.startswith("fintablo:expense-summary:"):
            result[name] = index
    return result


def _is_expense_summary_record(record: PaymentRecord) -> bool:
    return _normalize(record.name).startswith("fintablo:expense-summary:")


def _legacy_expense_summary_source_row_numbers(
    rows: Iterable[list[str]],
    summary_records: Iterable[PaymentRecord],
) -> list[int]:
    summary_keys = {_expense_summary_key(record) for record in summary_records}
    result: list[int] = []
    for index, row in enumerate(rows, start=2):
        record = PaymentRecord.from_row(row)
        name = _normalize(record.name)
        if not name.startswith("fintablo:") or name.startswith("fintablo:expense-summary:"):
            continue
        if _normalize(record.budget_item) not in AGGREGATED_EXPENSE_CATEGORY_KEYS:
            continue
        if _expense_summary_key(record) in summary_keys:
            result.append(index)
    return result


def _expense_summary_key(record: PaymentRecord) -> tuple[str, str, str, str]:
    record_date = _parse_record_date(record.date)
    month = record_date.strftime("%Y-%m") if record_date else _normalize(record.date)
    return (month, _normalize(record.bank), _normalize(record.budget_item), _normalize(record.payment_type))


def _batch_update_sheet_values(
    sheets_service: Any,
    spreadsheet_id: str,
    updates: list[tuple[str, list[list[str]]]],
) -> None:
    if not updates:
        return
    sheets_service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "valueInputOption": "USER_ENTERED",
            "data": [{"range": range_name, "values": values} for range_name, values in updates],
        },
    ).execute()


def _delete_sheet_rows(
    sheets_service: Any,
    spreadsheet_id: str,
    sheet_name: str,
    row_numbers: list[int],
) -> None:
    if not row_numbers:
        return
    metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheet_id = _sheet_ids(metadata)[sheet_name]
    requests = [
        {
            "deleteDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": row_number - 1,
                    "endIndex": row_number,
                }
            }
        }
        for row_number in sorted(set(row_numbers), reverse=True)
    ]
    sheets_service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()


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
    *,
    color: dict[str, float] = PALE_GREEN,
) -> None:
    _highlight_row_ranges(sheets_service, spreadsheet_id, sheet_name, [(start_row, end_row)], color=color)


def _highlight_row_numbers(
    sheets_service: Any,
    spreadsheet_id: str,
    sheet_name: str,
    row_numbers: list[int],
    *,
    color: dict[str, float] = PALE_GREEN,
) -> None:
    if not row_numbers:
        return
    ranges: list[tuple[int, int]] = []
    start = previous = row_numbers[0]
    for row_number in row_numbers[1:]:
        if row_number == previous + 1:
            previous = row_number
            continue
        ranges.append((start, previous))
        start = previous = row_number
    ranges.append((start, previous))
    _highlight_row_ranges(sheets_service, spreadsheet_id, sheet_name, ranges, color=color)


def _highlight_row_ranges(
    sheets_service: Any,
    spreadsheet_id: str,
    sheet_name: str,
    row_ranges: list[tuple[int, int]],
    *,
    color: dict[str, float] = PALE_GREEN,
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
                        "cell": {"userEnteredFormat": {"backgroundColor": color}},
                        "fields": "userEnteredFormat.backgroundColor",
                    }
                }
                for start_row, end_row in row_ranges
            ]
        },
    ).execute()


def aggregate_google_expense_records(records: Iterable[PaymentRecord]) -> list[PaymentRecord]:
    grouped: dict[tuple[str, str, str, str], list[PaymentRecord]] = {}
    passthrough: list[PaymentRecord] = []
    for record in records:
        if _normalize(record.budget_item) not in AGGREGATED_EXPENSE_CATEGORY_KEYS:
            passthrough.append(record)
            continue
        record_date = _parse_record_date(record.date)
        month = record_date.strftime("%Y-%m") if record_date else _normalize(record.date)
        key = (month, _normalize(record.bank), _normalize(record.budget_item), _normalize(record.payment_type))
        grouped.setdefault(key, []).append(record)

    result = list(passthrough)
    for records_for_key in grouped.values():
        result.append(_aggregate_expense_group(records_for_key))
    return result


def _aggregate_expense_group(records: list[PaymentRecord]) -> PaymentRecord:
    dates = [_parse_record_date(record.date) for record in records]
    date_values = [value for value in dates if value is not None]
    row_date = _month_end(date_values).strftime("%d.%m.%Y") if date_values else records[0].date
    first = records[0]
    total = sum((_decimal_amount(record.amount) for record in records), Decimal("0"))
    month_text = (max(date_values).strftime("%m.%Y") if date_values else str(first.date or "").strip())
    budget = first.budget_item
    bank = first.bank
    object_name = _first_non_empty(record.object_name for record in records) or _default_object_for_bank(bank)
    project = _first_non_empty(record.project for record in records) or _u(r"\u041e\u0444\u0438\u0441")
    counterparty = _first_non_empty(record.counterparty for record in records)
    purpose = f"{budget} {bank} \u0437\u0430 {month_text}"
    return PaymentRecord(
        name=f"fintablo:expense-summary:{month_text}:{_normalize(bank)}:{_normalize(budget)}",
        date=row_date,
        operation_type=OPERATION_EXPENSE,
        payment_type=first.payment_type,
        bank=bank,
        counterparty=counterparty,
        invoice_number="",
        object_name=object_name,
        project=project,
        budget_item=budget,
        responsible=_first_non_empty(record.responsible for record in records),
        purpose=purpose,
        invoice_link="",
        amount=_format_decimal(total),
    )


def _month_end(values: list[date]) -> date:
    last = max(values)
    return date(last.year, last.month, monthrange(last.year, last.month)[1])


def _default_object_for_bank(bank: str) -> str:
    if _normalize(_u(r"\u0418\u041d\u0412\u0415\u0421\u0422\u0421\u0422\u0420\u041e\u0419")) in _normalize(bank):
        return _u(r"\u041f\u0421\u041a \u0418\u0421")
    return _u(r"\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a")


def _first_non_empty(values: Iterable[str]) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _parse_record_date(value: Any) -> date | None:
    text = str(value or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _decimal_amount(value: Any) -> Decimal:
    text = str(value or "").replace("\xa0", " ").replace(" ", "").replace(",", ".").strip("+-")
    try:
        return Decimal(text or "0")
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _format_decimal(value: Decimal) -> str:
    if value == value.to_integral():
        return str(value.quantize(Decimal("1")))
    return format(value.normalize(), "f")

def is_safe_fintablo_expense_for_google(record: PaymentRecord) -> bool:
    return _normalize(record.budget_item) in SAFE_EXPENSE_CATEGORY_KEYS

def _with_absolute_amount(record: PaymentRecord) -> PaymentRecord:
    text = str(record.amount or "").strip()
    if text.startswith("-"):
        record = PaymentRecord(**{**record.__dict__, "amount": text[1:]})
    return record

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


SAFE_EXPENSE_CATEGORY_KEYS = {
    _normalize(_u(r"\u041a\u043e\u043c\u0438\u0441\u0441\u0438\u044f")),
    _normalize(_u(r"\u041e\u0431\u0441\u043b\u0443\u0436\u0438\u0432\u0430\u043d\u0438\u0435 \u0441\u0447\u0435\u0442\u0430")),
    _normalize(_u(r"\u041e\u0432\u0435\u0440\u0434\u0440\u0430\u0444\u0442 \u043f\u0440\u043e\u0446\u0435\u043d\u0442")),
    _normalize(_u(r"\u041a\u0440\u0435\u0434\u0438\u0442")),
    _normalize(_u(r"\u0412\u044b\u043f\u043b\u0430\u0442\u0430 \u0442\u0435\u043b\u0430 \u043a\u0440\u0435\u0434\u0438\u0442\u0430")),
    _normalize(_u(r"\u041f\u0440\u043e\u0446\u0435\u043d\u0442\u044b \u043f\u043e \u043a\u0440\u0435\u0434\u0438\u0442\u0443")),
    _normalize(_u(r"\u0414\u043e\u043b\u0433")),
    _normalize(_u(r"\u0412\u043e\u0437\u0432\u0440\u0430\u0442 \u0434\u043e\u043b\u0433\u0430")),
    _normalize(_u(r"\u041d\u0430\u043b\u043e\u0433\u0438 \u043d\u0430 \u0434\u043e\u0445\u043e\u0434\u044b (\u043f\u0440\u0438\u0431\u044b\u043b\u044c)")),
    _normalize(_u(r"\u041d\u0430\u043b\u043e\u0433\u0438 \u0437\u0430 \u0441\u043e\u0442\u0440\u0443\u0434\u043d\u0438\u043a\u043e\u0432")),
    _normalize(_u(r"\u041d\u0414\u0424\u041b")),
    _normalize(_u(r"\u0412\u0437\u043d\u043e\u0441\u044b \u0432 \u0444\u043e\u043d\u0434\u044b")),
    _normalize(_u(r"\u041f\u043e\u0448\u043b\u0438\u043d\u044b, \u043d\u043e\u0442\u0430\u0440\u0438\u0443\u0441, \u0448\u0442\u0440\u0430\u0444\u044b")),
    _normalize(_u(r"\u0428\u0442\u0440\u0430\u0444 \u041f\u0414\u0414")),
}

AGGREGATED_EXPENSE_CATEGORY_KEYS = {
    _normalize(_u(r"\u041a\u043e\u043c\u0438\u0441\u0441\u0438\u044f")),
    _normalize(_u(r"\u041e\u0431\u0441\u043b\u0443\u0436\u0438\u0432\u0430\u043d\u0438\u0435 \u0441\u0447\u0435\u0442\u0430")),
}

def _normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return _normalize(text)


def _normalize_amount(value: Any) -> str:
    text = str(value or "").replace("\xa0", " ").replace(" ", "").replace(",", ".").strip("+-")
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

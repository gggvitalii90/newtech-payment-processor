from __future__ import annotations

from typing import Iterable
import re
from datetime import datetime

from .google_payments import FINAL_SHEET_NAME, PAYMENT_ARCHIVE_SHEET_NAME, delete_rows_for_dates, highlight_fintablo_final_rows, replace_final_rows, replace_payment_archive_rows, setup_payment_sheets, upsert_final_rows, upsert_payment_archive
from .models import PaymentRecord


def write_google_history(
    sheets_service,
    spreadsheet_id: str,
    payment_records: Iterable[PaymentRecord],
    final_records: Iterable[PaymentRecord],
    dry_run: bool = False,
    final_sheet_name: str = FINAL_SHEET_NAME,
    replace_payment_archive: bool = True,
    upsert: bool = False,
    replace_final_dates: Iterable[str] | None = None,
) -> tuple[int, int]:
    if dry_run:
        return 0, 0
    payments = list(payment_records)
    final = list(final_records)
    setup_payment_sheets(sheets_service, spreadsheet_id)
    if upsert:
        payment_dates = {date for record in payments if (date := getattr(record, "date", ""))}
        if replace_final_dates is None:
            final_dates = {date for record in final if (date := getattr(record, "date", ""))}
        else:
            final_dates = {str(date) for date in replace_final_dates if str(date or "").strip()}
        delete_rows_for_dates(sheets_service, spreadsheet_id, final_sheet_name, final_dates, "N")
        updated, appended = upsert_payment_archive(sheets_service, spreadsheet_id, payments)
        payment_count = updated + appended
        if "ИС" in final_sheet_name:
            final_count = replace_final_rows(sheets_service, spreadsheet_id, final, sheet_name=final_sheet_name)
        else:
            updated, appended = upsert_final_rows(
                sheets_service, spreadsheet_id, final, sheet_name=final_sheet_name,
            )
            final_count = updated + appended
        _highlight_fintablo_rows_if_supported(sheets_service, spreadsheet_id, final_sheet_name, _extra_fintablo_names(final, payments))
    else:
        if replace_payment_archive:
            payment_count = replace_payment_archive_rows(sheets_service, spreadsheet_id, payments)
        else:
            updated, appended = upsert_payment_archive(sheets_service, spreadsheet_id, payments)
            payment_count = updated + appended
        final_count = replace_final_rows(sheets_service, spreadsheet_id, final, sheet_name=final_sheet_name)
        _highlight_fintablo_rows_if_supported(sheets_service, spreadsheet_id, final_sheet_name, _extra_fintablo_names(final, payments))
    return payment_count, final_count

def _highlight_fintablo_rows_if_supported(sheets_service, spreadsheet_id: str, final_sheet_name: str, extra_names: set[str] | None = None) -> None:
    if not hasattr(sheets_service, "spreadsheets"):
        return
    highlight_fintablo_final_rows(sheets_service, spreadsheet_id, final_sheet_name, extra_names=extra_names)

def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())

def _date_key(value: object) -> str:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return _norm(text)

def _amount(value: object) -> str:
    text = re.sub(r"[^0-9,.-]", "", str(value or "").replace(" ", ""))
    text = text.replace(",", ".")
    try:
        return f"{float(text):.2f}"
    except ValueError:
        return text

def _match_keys(record: PaymentRecord) -> set[tuple[str, ...]]:
    date = _date_key(getattr(record, "date", ""))
    amount = _amount(getattr(record, "amount", ""))
    counterparty = _norm(getattr(record, "counterparty", ""))
    invoice = _norm(getattr(record, "invoice_number", ""))
    if not date or not amount:
        return set()
    keys = {("base", date, amount, counterparty)}
    if invoice:
        keys.add(("invoice", date, amount, invoice))
    return keys

def _extra_fintablo_names(final: list[PaymentRecord], chat: list[PaymentRecord]) -> set[str]:
    chat_keys = set().union(*(_match_keys(record) for record in chat)) if chat else set()
    return {str(record.name).strip() for record in final if _norm(record.name).startswith("fintablo:") and not (_match_keys(record) & chat_keys)}


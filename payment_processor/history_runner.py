from __future__ import annotations

from typing import Iterable

from .google_payments import FINAL_SHEET_NAME, PAYMENT_ARCHIVE_SHEET_NAME, delete_rows_for_dates, replace_final_rows, replace_payment_archive_rows, setup_payment_sheets, upsert_final_rows, upsert_payment_archive
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
) -> tuple[int, int]:
    if dry_run:
        return 0, 0
    payments = list(payment_records)
    final = list(final_records)
    setup_payment_sheets(sheets_service, spreadsheet_id)
    if upsert:
        payment_dates = {date for record in payments if (date := getattr(record, "date", ""))}
        final_dates = {date for record in final if (date := getattr(record, "date", ""))}
        delete_rows_for_dates(sheets_service, spreadsheet_id, PAYMENT_ARCHIVE_SHEET_NAME, payment_dates, "J")
        delete_rows_for_dates(sheets_service, spreadsheet_id, final_sheet_name, final_dates, "N")
        updated, appended = upsert_payment_archive(sheets_service, spreadsheet_id, payments)
        payment_count = updated + appended
        updated, appended = upsert_final_rows(
            sheets_service, spreadsheet_id, final, sheet_name=final_sheet_name,
        )
        final_count = updated + appended
    else:
        if replace_payment_archive:
            payment_count = replace_payment_archive_rows(sheets_service, spreadsheet_id, payments)
        else:
            updated, appended = upsert_payment_archive(sheets_service, spreadsheet_id, payments)
            payment_count = updated + appended
        final_count = replace_final_rows(sheets_service, spreadsheet_id, final, sheet_name=final_sheet_name)
    return payment_count, final_count
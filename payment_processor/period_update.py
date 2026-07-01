from __future__ import annotations

from collections.abc import Iterable

from .invoice_archive import InvoiceArchiveRecord


def select_invoice_records_for_files(
    records: Iterable[InvoiceArchiveRecord],
    downloaded_file_names: set[str],
) -> list[InvoiceArchiveRecord]:
    return [
        record
        for record in records
        if not record.file_name or record.file_name in downloaded_file_names
    ]
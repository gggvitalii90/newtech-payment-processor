from payment_processor.invoice_archive import InvoiceArchiveRecord
from payment_processor.period_update import select_invoice_records_for_files


def record(name: str, file_type: str = "pdf") -> InvoiceArchiveRecord:
    return InvoiceArchiveRecord("", "ПСК", "-1", "", name, file_type, "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "")


def test_select_invoice_records_keeps_downloaded_invoices_and_text_operations_only() -> None:
    records = [record("invoice.pdf"), record("payment.pdf"), record("", "сообщение")]
    selected = select_invoice_records_for_files(records, {"invoice.pdf"})
    assert [row.file_name for row in selected] == ["invoice.pdf", ""]
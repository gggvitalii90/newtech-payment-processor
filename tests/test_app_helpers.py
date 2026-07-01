from pathlib import Path
from datetime import date

from payment_processor.app import PaymentProcessorApp, collect_existing_files, merge_cash_records_for_date, merge_files_by_name, merge_records_for_date
from payment_processor.models import PaymentRecord


def test_collect_existing_files_returns_only_files(tmp_path: Path) -> None:
    file_path = tmp_path / "invoice.pdf"
    file_path.write_bytes(b"pdf")
    (tmp_path / "nested").mkdir()

    assert collect_existing_files(tmp_path) == [file_path]
    assert collect_existing_files(tmp_path / "missing") == []


def test_merge_files_by_name_prefers_later_groups(tmp_path: Path) -> None:
    old_file = tmp_path / "old" / "invoice.pdf"
    new_file = tmp_path / "new" / "invoice.pdf"
    old_file.parent.mkdir()
    new_file.parent.mkdir()
    old_file.write_bytes(b"old")
    new_file.write_bytes(b"new")

    assert merge_files_by_name([[old_file], [new_file]]) == [new_file]


def test_merge_cash_records_for_date_replaces_only_same_day_cash() -> None:
    beznal = _record("pdf-1", "2026-06-11", "Безналичные с НДС", "300000")
    old_cash = _record("mid.cash", "2026-06-11", "Наличные", "100")
    other_day_cash = _record("mid.old", "2026-06-10", "Наличные", "200")
    new_cash = _record("mid.cash", "2026-06-11", "Наличные", "150")

    merged = merge_cash_records_for_date(
        [beznal, old_cash, other_day_cash],
        [new_cash],
        date(2026, 6, 11),
    )

    assert merged == [beznal, other_day_cash, new_cash]


def test_merge_records_for_date_replaces_pdf_rows_without_dropping_cash() -> None:
    old_pdf = _record("old.pdf", "2026-06-15", "Безналичные с НДС", "100")
    old_cash = _record("mid.cash", "2026-06-15", "Наличные", "200")
    other_day_pdf = _record("other.pdf", "2026-06-14", "Безналичные с НДС", "300")
    new_pdf = _record("new.pdf", "2026-06-15", "Безналичные с НДС", "400")

    merged = merge_records_for_date(
        [old_pdf, old_cash, other_day_pdf],
        [new_pdf],
        date(2026, 6, 15),
    )

    assert merged == [old_cash, other_day_pdf, new_pdf]


def test_selected_max_date_for_download_keeps_user_selected_date() -> None:
    class FakeVar:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    class FakeApp:
        app_start_date = date(2026, 6, 22)
        config_data = {"date_folder_format": "%Y.%m.%d"}
        max_date_var = FakeVar("2026.06.22")
        reset_called = False

        def _selected_max_date(self):
            return PaymentProcessorApp._selected_max_date(self)

        def reset_to_today(self) -> None:
            self.reset_called = True

    fake = FakeApp()

    selected = PaymentProcessorApp._selected_max_date_for_download(fake)

    assert selected == date(2026, 6, 22)
    assert fake.reset_called is False

def _record(name: str, payment_date: str, payment_type: str, amount: str) -> PaymentRecord:
    return PaymentRecord(
        name=name,
        date=payment_date,
        operation_type="Расход",
        payment_type=payment_type,
        bank="",
        counterparty="",
        invoice_number="",
        object_name="",
        project="",
        budget_item="",
        responsible="",
        purpose="",
        invoice_link="",
        amount=amount,
    )

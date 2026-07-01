import payment_processor.app as app_module
from payment_processor.app import enrich_payment_records_from_google, google_spreadsheet_url


def test_google_spreadsheet_url_builds_edit_link() -> None:
    assert google_spreadsheet_url("sheet-id") == "https://docs.google.com/spreadsheets/d/sheet-id/edit"


def test_enrich_payment_records_from_google_reads_configured_archive(monkeypatch) -> None:
    settings = type("Settings", (), {"archive_spreadsheet_id": "sheet-id", "archive_sheet_name": "Архив счетов"})()
    monkeypatch.setattr(app_module, "load_google_settings", lambda env: settings)
    monkeypatch.setattr(app_module, "get_credentials", lambda value: "credentials")
    monkeypatch.setattr(app_module, "build_sheets_service", lambda value: "sheets")
    monkeypatch.setattr(app_module, "read_archive_records", lambda *args: ["archive-record"])
    captured = {}

    def fake_enrich(payments, archive_records):
        captured["values"] = (payments, archive_records)
        return 1

    monkeypatch.setattr(app_module, "enrich_payment_records_from_archive", fake_enrich)
    payments = ["payment-record"]

    matched = enrich_payment_records_from_google(payments, {"configured": "yes"})

    assert matched == 1
    assert captured["values"] == (payments, ["archive-record"])
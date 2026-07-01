import payment_processor.app as app_module
from payment_processor.app import sync_payment_sheets
from payment_processor.models import PaymentRecord


def test_sync_payment_sheets_upserts_final_and_archive_without_clearing_history(monkeypatch) -> None:
    settings = type("Settings", (), {"archive_spreadsheet_id": "sheet-id"})()
    monkeypatch.setattr(app_module, "load_google_settings", lambda env: settings)
    monkeypatch.setattr(app_module, "get_credentials", lambda value: "credentials")
    monkeypatch.setattr(app_module, "build_sheets_service", lambda value: "sheets")
    calls = []
    monkeypatch.setattr(app_module, "setup_payment_sheets", lambda *args: calls.append(("setup", args)))
    monkeypatch.setattr(app_module, "upsert_final_rows", lambda service, sheet_id, rows, sheet_name="Итоговая": calls.append(("final", sheet_name, list(rows))) or (1, 1))
    monkeypatch.setattr(app_module, "upsert_payment_archive", lambda service, sheet_id, rows: calls.append(("archive", list(rows))) or (1, 0))
    payment = _record("payment.pdf", "Безналичные без НДС")
    cash = _record("mid.cash", "Наличные")
    result = sync_payment_sheets([payment, cash], [payment], {"configured": "yes"})
    assert result == (2, 1)
    assert calls[1] == ("final", "Итоговая", [payment, cash])
    assert calls[2] == ("archive", [payment])




def test_sync_payment_sheets_writes_is_mode_to_is_final_sheet(monkeypatch) -> None:
    settings = type("Settings", (), {"archive_spreadsheet_id": "sheet-id"})()
    monkeypatch.setattr(app_module, "load_google_settings", lambda env: settings)
    monkeypatch.setattr(app_module, "get_credentials", lambda value: "credentials")
    monkeypatch.setattr(app_module, "build_sheets_service", lambda value: "sheets")
    monkeypatch.setattr(app_module, "setup_payment_sheets", lambda *args: None)
    captured = {}
    monkeypatch.setattr(app_module, "upsert_final_rows", lambda service, sheet_id, rows, sheet_name="Итоговая": (captured.__setitem__("sheet", sheet_name), (0, 1))[1])
    monkeypatch.setattr(app_module, "upsert_payment_archive", lambda service, sheet_id, rows: (0, 0))

    sync_payment_sheets([_record("is.pdf", "Безналичные")], [], {}, mode="\u0418\u0421")

    assert captured["sheet"] == "Итоговая ИС"


def _record(name: str, payment_type: str) -> PaymentRecord:
    return PaymentRecord(name, "2026-06-20", "Расход", payment_type, "", "", "", "", "", "", "", "", "", "100")

def test_sync_payment_sheets_uses_payment_file_link_only_in_archive(monkeypatch, tmp_path) -> None:
    settings = type("Settings", (), {"archive_spreadsheet_id": "sheet-id"})()
    monkeypatch.setattr(app_module, "load_google_settings", lambda env: settings)
    monkeypatch.setattr(app_module, "get_credentials", lambda value: "credentials")
    monkeypatch.setattr(app_module, "build_sheets_service", lambda value: "sheets")
    monkeypatch.setattr(app_module, "build_drive_service", lambda value: "drive")
    monkeypatch.setattr(app_module, "setup_payment_sheets", lambda *args: None)
    monkeypatch.setattr(app_module, "ensure_payment_file", lambda *args, **kwargs: "https://drive/payment")
    captured = {}
    monkeypatch.setattr(app_module, "upsert_final_rows", lambda service, sheet_id, rows, sheet_name="Итоговая": (captured.__setitem__("final", list(rows)), captured.__setitem__("final_sheet", sheet_name), (0, 0))[2])
    monkeypatch.setattr(app_module, "upsert_payment_archive", lambda service, sheet_id, rows: (captured.__setitem__("archive", list(rows)), (0, 0))[1])
    source = tmp_path / "payment.pdf"
    source.write_bytes(b"pdf")
    payment = _record("payment.pdf", "Безналичные без НДС")
    payment.invoice_link = "https://drive/invoice"
    sync_payment_sheets([payment], [payment], {}, archive_file_paths=[source], mode="ПСК")
    assert captured["final"][0].invoice_link == "https://drive/invoice"
    assert captured["archive"][0].invoice_link == "https://drive/payment"
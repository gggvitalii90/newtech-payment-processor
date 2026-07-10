
from payment_processor.models import PaymentRecord
from scripts.fintablo_sync_daily import cash_key_from_record, cash_key_from_tx, manual_line_from_record, parse_day


def test_manual_line_from_record_uses_final_table_fields():
    record = PaymentRecord(
        name="mid.1",
        date="2026-07-03",
        operation_type="??????",
        payment_type="????????",
        bank="",
        counterparty="",
        invoice_number="",
        object_name="??? ??????",
        project="???",
        budget_item="???",
        responsible="???????.?",
        purpose="?? ?????",
        invoice_link="",
        amount="30000",
    )

    line = manual_line_from_record(record)

    assert line.values["date"] == "03.07.2026"
    assert line.values["object"] == "??? ??????"
    assert line.values["project"] == "???"
    assert line.values["budget"] == "???"
    assert line.values["amount"] == "30000"


def test_cash_key_matches_existing_fintablo_cash_transaction():
    record = PaymentRecord(
        name="mid.2",
        date="03.07.2026",
        operation_type="??????",
        payment_type="????????",
        bank="",
        counterparty="",
        invoice_number="",
        object_name="??? ??????",
        project="???",
        budget_item="???",
        responsible="???????.?",
        purpose="?? ????? ????",
        invoice_link="",
        amount="30 000,00",
    )
    tx = {"date": "03.07.2026", "value": "30000", "description": "?? ????? ????"}

    assert cash_key_from_record(record) == cash_key_from_tx(tx)


def test_parse_day_accepts_google_and_iso_dates():
    assert parse_day("2026-07-03").isoformat() == "2026-07-03"
    assert parse_day("03.07.2026").isoformat() == "2026-07-03"

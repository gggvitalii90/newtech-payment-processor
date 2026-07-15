
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



def _ru(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


def test_cash_zero_amount_is_identified_before_creation():
    from scripts.fintablo_sync_daily import amount

    assert amount("") == 0
    assert amount("0") == 0


def test_payload_skips_base_deal_when_deal_requires_stage_and_project_missing():
    from scripts.fintablo_fill_deals_directions import ManualLine, payload_from_manual

    line = ManualLine(
        source_row={},
        values={
            "operation_type": _ru(r"\u0420\u0430\u0441\u0445\u043e\u0434"),
            "object": _ru(r"\u0420\u0438\u0437\u0430\u043b\u0438\u0442"),
            "project": "",
            "budget": _ru(r"\u041c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b"),
        },
    )
    tx = {"group": "outcome", "categoryId": 0, "dealId": 0}
    directions = {}
    deals = {
        _ru(r"\u0440\u0438\u0437\u0430\u043b\u0438\u0442"): {
            "id": 10,
            "name": _ru(r"\u0420\u0438\u0437\u0430\u043b\u0438\u0442"),
            "stages": [{"id": 11, "name": _ru(r"\u041a\u041c ( \u041c )")}],
        }
    }
    categories = {
        _ru(r"\u043c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b"): {
            "id": 20,
            "name": _ru(r"\u041c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b"),
            "group": "outcome",
        }
    }

    payload, notes = payload_from_manual(line, tx, directions, deals, categories, {}, {11})

    assert payload == {"categoryId": 20}
    assert "stage_required_skipped_deal" in notes



def test_fintablo_category_update_overwrites_wrong_existing_category():
    from scripts.fintablo_fill_deals_directions import ManualLine, category_update

    line = ManualLine(
        source_row={},
        values={"operation_type": _ru(r"\u0420\u0430\u0441\u0445\u043e\u0434"), "budget": "wazzup"},
    )
    tx = {"group": "outcome", "categoryId": 1}
    categories = {
        "crm": {"id": 1, "name": "CRM", "group": "outcome"},
        "wazzup": {"id": 2, "name": "wazzup", "group": "outcome"},
    }
    category_by_id = {1: {"id": 1, "name": "CRM", "group": "outcome"}}

    payload, notes = category_update(line, tx, categories, category_by_id)

    assert notes == []
    assert payload == {"categoryId": 2}


def test_conversion_sets_category_but_skips_links():
    from scripts.fintablo_fill_deals_directions import ManualLine, normalize_key, payload_from_manual

    line = ManualLine(
        source_row={},
        values={
            "operation_type": _ru(r"\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"),
            "object": _ru(r"\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"),
            "project": _ru(r"\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"),
            "budget": "",
        },
    )
    tx = {"group": "outcome", "categoryId": 1, "dealId": 0, "directionId": 0}
    categories = {
        normalize_key(_ru(r"\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f")): {
            "id": 2,
            "name": _ru(r"\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"),
            "group": "outcome",
        }
    }

    payload, notes = payload_from_manual(line, tx, {}, {}, categories, {}, set())

    assert payload == {"categoryId": 2}
    assert notes == ["skip_conversion_links"]


def test_return_project_sets_return_category():
    from scripts.fintablo_fill_deals_directions import ManualLine, normalize_key, payload_from_manual

    line = ManualLine(
        source_row={},
        values={
            "operation_type": _ru(r"\u0420\u0430\u0441\u0445\u043e\u0434"),
            "object": _ru(r"\u0426\u0435\u043d\u0442\u0440\u0441\u0442\u0440\u043e\u0439\u043f\u0440\u043e\u0435\u043a\u0442"),
            "project": _ru(r"\u0412\u043e\u0437\u0432\u0440\u0430\u0442"),
            "budget": "",
        },
    )
    tx = {"group": "outcome", "categoryId": 1, "dealId": 0, "directionId": 0}
    return_name = _ru(r"\u0412\u043e\u0437\u0432\u0440\u0430\u0442")
    categories = {
        normalize_key(return_name): {"id": 1, "name": return_name, "group": "income"},
        "__items__": {
            "items": [
                {"id": 1, "name": return_name, "group": "income"},
                {"id": 2, "name": return_name, "group": "outcome"},
            ]
        },
    }
    deals = {
        normalize_key(_ru(r"\u0426\u0435\u043d\u0442\u0440\u0441\u0442\u0440\u043e\u0439\u043f\u0440\u043e\u0435\u043a\u0442")): {
            "id": 10,
            "name": _ru(r"\u0426\u0435\u043d\u0442\u0440\u0441\u0442\u0440\u043e\u0439\u043f\u0440\u043e\u0435\u043a\u0442"),
            "stages": [{"id": 11, "name": _ru(r"\u041a\u041c ( \u041c )")}],
        }
    }

    payload, notes = payload_from_manual(line, tx, {}, deals, categories, {}, {11})

    assert payload == {"categoryId": 2}
    assert "stage_not_found_skipped_deal" in notes


def test_internal_fot_uses_salary_category_and_fot_direction_when_budget_is_person():
    from scripts.fintablo_fill_deals_directions import ManualLine, normalize_key, payload_from_manual

    line = ManualLine(
        source_row={},
        values={
            "operation_type": _ru(r"\u0420\u0430\u0441\u0445\u043e\u0434"),
            "object": _ru(r"\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"),
            "project": _ru(r"\u0424\u041e\u0422"),
            "budget": _ru(r"\u0420\u043e\u0434\u0438\u043d.\u041a"),
        },
    )
    tx = {"group": "outcome", "categoryId": 0, "dealId": 0, "directionId": 0}
    categories = {
        normalize_key(_ru(r"\u0417\u0430\u0440\u043f\u043b\u0430\u0442\u0430")): {"id": 2, "name": _ru(r"\u0417\u0430\u0440\u043f\u043b\u0430\u0442\u0430"), "group": "outcome"}
    }
    directions = {normalize_key(_ru(r"\u0424\u041e\u0422")): {"id": 3, "name": _ru(r"\u0424\u041e\u0422")}}

    payload, notes = payload_from_manual(line, tx, directions, {}, categories, {}, set())

    assert notes == []
    assert payload == {"categoryId": 2, "directionId": 3}


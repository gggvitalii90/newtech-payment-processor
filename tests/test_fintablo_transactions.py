from payment_processor.fintablo_transactions import fintablo_transactions_to_payment_records


def u(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")


def test_fintablo_outcome_transaction_maps_references_and_invoice_number() -> None:
    transactions = [{
        "id": 4696801,
        "date": "02.07.2026",
        "group": "outcome",
        "value": 48000,
        "moneybagId": 1,
        "categoryId": 10,
        "partnerId": 20,
        "dealId": 30,
        "description": u("\\u0427/\\u043e. \\u0423\\u0441\\u043b\\u0443\\u0433\\u0438 \\u044d\\u043a\\u0441\\u043a\\u0430\\u0432\\u0430\\u0442\\u043e\\u0440\\u0430. \\u0421\\u0447\\u0435\\u0442 \\u043d\\u0430 \\u043e\\u043f\\u043b\\u0430\\u0442\\u0443 \\u2116 49 \\u043e\\u0442 18 \\u041c\\u0430\\u044f 2026. \\u0412 \\u0442\\u043e\\u043c \\u0447\\u0438\\u0441\\u043b\\u0435 \\u041d\\u0414\\u0421 22%, 8655.74 \\u0440\\u0443\\u0431."),
    }]
    records = fintablo_transactions_to_payment_records(
        transactions,
        moneybags=[{"id": 1, "name": u("\\u041d\\u042c\\u042e\\u0422\\u0415\\u041a \\u0421\\u0431\\u0435\\u0440 \\u0440/\\u0441 *5967"), "type": "bank"}],
        categories=[{"id": 10, "name": u("\\u0422\\u0435\\u0445\\u043d\\u0438\\u043a\\u0430")}],
        partners=[{"id": 20, "name": u("\\u041e\\u041e\\u041e \\u0411\\u0415\\u041b\\u0422\\u0420\\u0410\\u041d\\u0421\\u0410\\u0412\\u0422\\u041e")}],
        deals=[{"id": 30, "name": u("\\u041f\\u0421\\u041a \\u041d\\u044c\\u044e\\u0442\\u0435\\u043a")}],
        directions=[],
    )

    assert len(records) == 1
    row = records[0]
    assert row.name == "fintablo:4696801"
    assert row.date == "02.07.2026"
    assert row.operation_type == u("\\u0420\\u0430\\u0441\\u0445\\u043e\\u0434")
    assert row.payment_type == u("\\u0411\\u0435\\u0437\\u043d\\u0430\\u043b\\u0438\\u0447\\u043d\\u044b\\u0435 \\u0441 \\u041d\\u0414\\u0421")
    assert row.bank == u("\\u0431/\\u043d \\u0421\\u0431\\u0435\\u0440\\u0431\\u0430\\u043d\\u043a")
    assert row.counterparty == u("\\u041e\\u041e\\u041e \\u0411\\u0415\\u041b\\u0422\\u0420\\u0410\\u041d\\u0421\\u0410\\u0412\\u0422\\u041e")
    assert row.invoice_number == "49"
    assert row.object_name == u("\\u041f\\u0421\\u041a \\u041d\\u044c\\u044e\\u0442\\u0435\\u043a")
    assert row.budget_item == u("\\u0422\\u0435\\u0445\\u043d\\u0438\\u043a\\u0430")
    assert row.purpose.startswith(u("\\u0427/\\u043e. \\u0423\\u0441\\u043b\\u0443\\u0433\\u0438"))
    assert row.amount == "48000"


def test_fintablo_nds_five_percent_is_treated_as_without_nds() -> None:
    records = fintablo_transactions_to_payment_records(
        [{"id": 1, "date": "03.07.2026", "group": "outcome", "value": "100.50", "moneybagId": 1, "description": u("\\u041e\\u043f\\u043b\\u0430\\u0442\\u0430, \\u041d\\u0414\\u0421 5%")}],
        moneybags=[{"id": 1, "name": u("\\u0418\\u041f \\u041c\\u043e\\u0447_\\u0410\\u043b\\u044c\\u0444\\u0430_\\u0420\\u0430\\u0441\\u0447\\u0435\\u0442\\u043d\\u044b\\u0439 *0224"), "type": "bank"}],
        categories=[], partners=[], deals=[], directions=[],
    )

    assert records[0].payment_type == u("\\u0411\\u0435\\u0437\\u043d\\u0430\\u043b\\u0438\\u0447\\u043d\\u044b\\u0435 \\u0431\\u0435\\u0437 \\u041d\\u0414\\u0421")
    assert records[0].bank == u("\\u0431/\\u043d \\u0418\\u041f \\u041c\\u043e\\u0447\\u0430\\u043b\\u043e\\u0432")
    assert records[0].amount == "100.5"


def test_fintablo_transfer_maps_to_conversion_operation() -> None:
    records = fintablo_transactions_to_payment_records(
        [{"id": 2, "date": "03.07.2026", "group": "transfer", "value": 5000, "moneybagId": 1, "moneybag2Id": 2, "categoryId": 10, "description": u("\\u041f\\u0435\\u0440\\u0435\\u0432\\u043e\\u0434")}],
        moneybags=[{"id": 1, "name": u("\\u0422\\u043e\\u0447\\u043a\\u0430_\\u0420\\u0430\\u0441\\u0447\\u0435\\u0442\\u043d\\u044b\\u0439 *3560"), "type": "bank"}, {"id": 2, "name": u("\\u041d\\u0430\\u043b\\u0438\\u0447\\u043d\\u044b\\u0435"), "type": "nal"}],
        categories=[{"id": 10, "name": u("\\u041a\\u043e\\u043d\\u0432\\u0435\\u0440\\u0442\\u0430\\u0446\\u0438\\u044f \\u0432\\u0430\\u043b\\u044e\\u0442")}], partners=[], deals=[], directions=[],
    )

    assert records[0].operation_type == u("\\u041a\\u043e\\u043d\\u0432\\u0435\\u0440\\u0442\\u0430\\u0446\\u0438\\u044f")
    assert records[0].object_name == u("\\u041a\\u043e\\u043d\\u0432\\u0435\\u0440\\u0442\\u0430\\u0446\\u0438\\u044f")
    assert records[0].project == u("\\u041a\\u043e\\u043d\\u0432\\u0435\\u0440\\u0442\\u0430\\u0446\\u0438\\u044f")
    assert records[0].bank == u("\\u0431/\\u043d \\u0422\\u043e\\u0447\\u043a\\u0430")



def test_fetch_fintablo_payment_records_loads_references_and_skips_cash_by_default() -> None:
    from datetime import date
    from payment_processor.fintablo_transactions import fetch_fintablo_payment_records

    class FakeClient:
        def list_moneybags(self):
            return [
                {"id": 1, "name": u("\\u041d\\u042c\\u042e\\u0422\\u0415\\u041a \\u0421\\u0431\\u0435\\u0440"), "type": "bank"},
                {"id": 2, "name": u("\\u041d\\u0430\\u043b\\u0438\\u0447\\u043d\\u044b\\u0435"), "type": "nal"},
            ]
        def list_categories(self):
            return []
        def list_partners(self):
            return []
        def list_deals(self):
            return []
        def list_directions(self):
            return []
        def list_transactions(self, *, date_from, date_to):
            assert date_from == "02.07.2026"
            assert date_to == "03.07.2026"
            return [
                {"id": 1, "date": "02.07.2026", "group": "outcome", "value": 10, "moneybagId": 1},
                {"id": 2, "date": "02.07.2026", "group": "outcome", "value": 20, "moneybagId": 2},
            ]

    records = fetch_fintablo_payment_records(FakeClient(), date(2026, 7, 2), date(2026, 7, 3))

    assert [record.name for record in records] == ["fintablo:1"]


def test_fintablo_alfa_moneybag_maps_to_canonical_bank_name() -> None:
    records = fintablo_transactions_to_payment_records(
        [{"id": 3, "date": "03.07.2026", "group": "outcome", "value": 100, "moneybagId": 1}],
        moneybags=[{"id": 1, "name": u("\\u041f\\u0421\\u041a \\u041d\\u044c\\u044e\\u0442\\u0435\\u043a \\u0440/\\u0441 \\u0410\\u043b\\u044c\\u0444\\u0430-\\u0411\\u0430\\u043d\\u043a *3784"), "type": "bank"}],
        categories=[], partners=[], deals=[], directions=[],
    )

    assert records[0].bank == u("\\u0431/\\u043d \\u0410\\u043b\\u044c\\u0444\\u0430")

def test_fintablo_extracts_invoice_numbers_from_common_july_wordings() -> None:
    transactions = [
        {
            "id": 10,
            "date": "15.07.2026",
            "group": "outcome",
            "value": 30000,
            "moneybagId": 1,
            "description": "\u041e\u043a\u0430\u0437\u0430\u043d\u0438\u0435 \u0443\u0441\u043b\u0443\u0433 \u041a\u041c\u0414. \u0421\u0447\u0451\u0442 \u043d\u0430 \u043e\u043f\u043b\u0430\u0442\u0443 \u211631542130 \u043e\u0442 15 \u0438\u044e\u043b\u044f 2026 \u0433. \u041d\u0414\u0421 \u043d\u0435 \u043e\u0431\u043b\u0430\u0433\u0430\u0435\u0442\u0441\u044f",
        },
        {
            "id": 11,
            "date": "15.07.2026",
            "group": "outcome",
            "value": 16457,
            "moneybagId": 1,
            "description": "\u0422\u041e. \u0421\u0447\u0435\u0442 \u043a \u0437\u0430\u043a\u0430\u0437-\u043d\u0430\u0440\u044f\u0434\u0443 \u21162253882-1 \u043e\u0442 15.07.2026\u0433.",
        },
        {
            "id": 12,
            "date": "15.07.2026",
            "group": "outcome",
            "value": 44000,
            "moneybagId": 1,
            "description": "\u0423\u0441\u043b\u0443\u0433\u0438 \u0438 \u0434\u043e\u0441\u0442\u0430\u0432\u043a\u0430. \u0421\u0447\u0435\u0442 \u043d\u0430 \u043e\u043f\u043b\u0430\u0442\u0443 \u2116 32 \u043e\u0442 15.07.2026. \u041d\u0414\u0421 \u043d\u0435 \u043e\u0431\u043b\u0430\u0433\u0430\u0435\u0442\u0441\u044f",
        },
    ]

    records = fintablo_transactions_to_payment_records(
        transactions,
        moneybags=[{"id": 1, "name": "\u041d\u042c\u042e\u0422\u0415\u041a \u0421\u0431\u0435\u0440 \u0440/\u0441 *5967", "type": "bank"}],
        categories=[],
        partners=[],
        deals=[],
        directions=[],
    )

    assert [record.invoice_number for record in records] == ["31542130", "2253882-1", "32"]

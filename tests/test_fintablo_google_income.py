from payment_processor.fintablo_google_income import find_missing_income_records
from payment_processor.models import PaymentRecord


def u(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")


def income_record(name: str, amount: str = "150000") -> PaymentRecord:
    return PaymentRecord(
        name=name,
        date="14.07.2026",
        operation_type=u(r"\u041f\u0440\u0438\u0445\u043e\u0434"),
        payment_type=u(r"\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421"),
        bank=u(r"\u0431/\u043d \u0418\u041f \u041c\u043e\u0447\u0430\u043b\u043e\u0432"),
        counterparty=u(r"\u041e\u041e\u041e \"\u041c\u0418\u041d\u0422\u0415\u0425\u041f\u0420\u041e\u041c\""),
        invoice_number="y",
        object_name=u(r"\u041c\u0438\u043d\u0442\u0435\u0445\u043f\u0440\u043e\u043c"),
        project="",
        budget_item=u(r"\u0420\u0430\u0431\u043e\u0442\u044b"),
        responsible="",
        purpose=u(r"\u0427\u0430\u0441\u0442\u0438\u0447\u043d\u0430\u044f \u043e\u043f\u043b\u0430\u0442\u0430"),
        invoice_link="",
        amount=amount,
    )


def test_missing_income_skips_existing_fintablo_id() -> None:
    existing = [income_record("fintablo:123")]
    incoming = [income_record("fintablo:123"), income_record("fintablo:124", "200000")]

    missing = find_missing_income_records(incoming, existing)

    assert [record.name for record in missing] == ["fintablo:124"]


def test_missing_income_skips_existing_business_row_even_without_fintablo_id() -> None:
    existing = [income_record("manual-row")]
    incoming = [income_record("fintablo:123")]

    assert find_missing_income_records(incoming, existing) == []

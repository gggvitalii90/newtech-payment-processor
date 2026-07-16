from payment_processor.fintablo_google_income import aggregate_google_expense_records, find_missing_income_records, fintablo_expense_row_numbers, fintablo_income_row_numbers
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



def expense_record(name: str, amount: str, day: str, budget: str | None = None) -> PaymentRecord:
    return PaymentRecord(
        name=name,
        date=day,
        operation_type=u(r"\u0420\u0430\u0441\u0445\u043e\u0434"),
        payment_type=u(r"\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0431\u0435\u0437 \u041d\u0414\u0421"),
        bank=u(r"\u0431/\u043d \u0418\u041f \u041c\u043e\u0447\u0430\u043b\u043e\u0432"),
        counterparty=u(r"\u0410\u041e \"\u0410\u041b\u042c\u0424\u0410-\u0411\u0410\u041d\u041a\""),
        invoice_number="",
        object_name=u(r"\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"),
        project=u(r"\u041e\u0444\u0438\u0441"),
        budget_item=budget or u(r"\u041a\u043e\u043c\u0438\u0441\u0441\u0438\u044f"),
        responsible="",
        purpose=u(r"\u041a\u043e\u043c\u0438\u0441\u0441\u0438\u044f"),
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


def test_fintablo_income_row_numbers_detects_existing_income_rows() -> None:
    income = income_record("fintablo:123").as_row()
    manual_income = income_record("manual-row").as_row()
    expense = income_record("fintablo:124").as_row()
    expense[2] = u(r"\u0420\u0430\u0441\u0445\u043e\u0434")

    assert fintablo_income_row_numbers([income, manual_income, expense]) == [2]


def test_fintablo_expense_row_numbers_detects_existing_expense_rows() -> None:
    income = income_record("fintablo:123").as_row()
    expense = income_record("fintablo:124").as_row()
    expense[2] = u(r"\u0420\u0430\u0441\u0445\u043e\u0434")

    assert fintablo_expense_row_numbers([income, expense]) == [3]



def test_commission_expenses_are_aggregated_to_month_end() -> None:
    records = [
        expense_record("fintablo:1", "1", "15.07.2026"),
        expense_record("fintablo:2", "2", "16.07.2026"),
    ]

    result = aggregate_google_expense_records(records)

    assert len(result) == 1
    summary = result[0]
    assert summary.name.startswith("fintablo:expense-summary:07.2026:")
    assert summary.date == "31.07.2026"
    assert summary.amount == "3"
    assert summary.budget_item == u(r"\u041a\u043e\u043c\u0438\u0441\u0441\u0438\u044f")
    assert "07.2026" in summary.purpose

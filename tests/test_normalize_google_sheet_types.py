from datetime import datetime
from decimal import Decimal

from scripts.normalize_google_sheet_types import (
    ResolvedSheetTypeSpec,
    google_serial,
    parse_amount,
    parse_date_or_datetime,
    typed_cell,
)


def test_parse_date_and_amount_values() -> None:
    assert parse_date_or_datetime("30.06.2026") == datetime(2026, 6, 30)
    assert parse_date_or_datetime("2026-07-10 16:25:00") == datetime(2026, 7, 10, 16, 25)
    assert parse_amount("+1 234,56") == Decimal("1234.56")
    assert parse_amount("-54 956.40") == Decimal("-54956.40")


def test_typed_cell_writes_date_and_amount_as_number_values() -> None:
    spec = ResolvedSheetTypeSpec("Итоговая", 14, {1: "DATE"}, {13})

    date_cell, date_changed, date_skipped = typed_cell("30.06.2026", 1, spec)
    amount_cell, amount_changed, amount_skipped = typed_cell("1 234,56", 13, spec)

    assert date_changed and not date_skipped
    assert date_cell["userEnteredValue"]["numberValue"] == google_serial(datetime(2026, 6, 30))
    assert amount_changed and not amount_skipped
    assert amount_cell["userEnteredValue"]["numberValue"] == 1234.56


def test_typed_cell_keeps_unparseable_typed_value_as_string() -> None:
    spec = ResolvedSheetTypeSpec("Итоговая", 14, {1: "DATE"}, {13})

    cell, changed, skipped = typed_cell("не дата", 1, spec)

    assert not changed
    assert skipped
    assert cell == {"userEnteredValue": {"stringValue": "не дата"}}
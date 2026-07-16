from payment_processor.google_payments import (
    FINAL_COLUMNS,
    PAYMENT_ARCHIVE_COLUMNS,
    payment_archive_row,
    FINAL_SHEET_NAME,
    FINAL_IS_SHEET_NAME,
    PAYMENT_ARCHIVE_SHEET_NAME,
    replace_final_rows,
    replace_payment_archive_rows,
    setup_payment_sheets,
    upsert_final_rows,
    upsert_payment_archive,
    final_sheet_name_for_mode,
    final_row,
)
from payment_processor.models import PaymentRecord


def record(name: str, amount: str = "100", payment_date: str = "2026-06-20") -> PaymentRecord:
    return PaymentRecord(name, payment_date, "Расход", "Безналичные без НДС", "б/н Альфа", 'ООО "ТЕСТ"', "15", "ПСК Ньютек", "Офис", "Расходники", "Родин.К", "Оплата", "https://drive/file", amount)


def test_setup_payment_sheets_creates_final_and_payment_archive() -> None:
    sheets = FakeSheets()
    setup_payment_sheets(sheets, "spreadsheet")
    assert set(sheets.sheet_ids) == {FINAL_SHEET_NAME, FINAL_IS_SHEET_NAME, PAYMENT_ARCHIVE_SHEET_NAME}
    assert FINAL_COLUMNS[0] == "№"
    assert sheets.values_api.updated[f"'{FINAL_SHEET_NAME}'!A1:N1"] == [FINAL_COLUMNS]
    assert sheets.values_api.updated[f"'{FINAL_IS_SHEET_NAME}'!A1:N1"] == [FINAL_COLUMNS]
    assert sheets.values_api.updated[f"'{PAYMENT_ARCHIVE_SHEET_NAME}'!A1:J1"] == [PAYMENT_ARCHIVE_COLUMNS]



def test_setup_payment_sheets_resets_data_rows_to_plain_format() -> None:
    sheets = FakeSheets(existing_titles=[FINAL_SHEET_NAME])
    sheets.values_api.headers[FINAL_SHEET_NAME] = FINAL_COLUMNS

    setup_payment_sheets(sheets, "spreadsheet")

    data_format_requests = [
        request for request in sheets.format_requests
        if request.get("repeatCell", {}).get("range", {}).get("startRowIndex") == 1
    ]
    assert data_format_requests
    assert data_format_requests[0]["repeatCell"]["cell"]["userEnteredFormat"]["textFormat"] == {"bold": False}


def test_replace_final_rows_rewrites_full_history() -> None:
    sheets = FakeSheets(existing_titles=[FINAL_SHEET_NAME])
    replace_final_rows(sheets, "spreadsheet", [record("one.pdf"), record("two.pdf", "200")])
    assert sheets.values_api.cleared == [f"'{FINAL_SHEET_NAME}'!A2:N"]
    assert sheets.values_api.updated[f"'{FINAL_SHEET_NAME}'!A1:N3"] == [FINAL_COLUMNS, final_row(record("one.pdf")), final_row(record("two.pdf", "200"))]




def test_replace_final_rows_can_target_is_final_sheet() -> None:
    sheets = FakeSheets(existing_titles=[FINAL_IS_SHEET_NAME])
    replace_final_rows(sheets, "spreadsheet", [record("is.pdf")], sheet_name=FINAL_IS_SHEET_NAME)
    assert sheets.values_api.cleared == [f"'{FINAL_IS_SHEET_NAME}'!A2:N"]
    assert sheets.values_api.updated[f"'{FINAL_IS_SHEET_NAME}'!A1:N2"] == [FINAL_COLUMNS, final_row(record("is.pdf"))]


def test_final_sheet_name_for_mode_returns_is_sheet() -> None:
    assert final_sheet_name_for_mode("\u0418\u0421") == FINAL_IS_SHEET_NAME
    assert final_sheet_name_for_mode("\u041f\u0421\u041a") == FINAL_SHEET_NAME


def test_replace_payment_archive_rows_rewrites_full_history() -> None:
    sheets = FakeSheets(existing_titles=[PAYMENT_ARCHIVE_SHEET_NAME])
    replace_payment_archive_rows(sheets, "spreadsheet", [record("one.pdf")])
    assert sheets.values_api.cleared == [f"'{PAYMENT_ARCHIVE_SHEET_NAME}'!A2:N"]
    assert sheets.values_api.updated[f"'{PAYMENT_ARCHIVE_SHEET_NAME}'!A1:J2"] == [PAYMENT_ARCHIVE_COLUMNS, payment_archive_row(record("one.pdf"))]


def test_upsert_payment_archive_distinguishes_same_filename_on_different_dates() -> None:
    sheets = FakeSheets(existing_titles=[PAYMENT_ARCHIVE_SHEET_NAME])
    sheets.values_api.headers[PAYMENT_ARCHIVE_SHEET_NAME] = PAYMENT_ARCHIVE_COLUMNS
    sheets.values_api.rows[PAYMENT_ARCHIVE_SHEET_NAME] = [payment_archive_row(record("same.pdf", payment_date="2026-06-19"))]
    updated, appended = upsert_payment_archive(sheets, "spreadsheet", [record("same.pdf", payment_date="2026-06-20")])
    assert (updated, appended) == (0, 1)


def test_upsert_final_rows_updates_existing_and_preserves_history() -> None:
    sheets = FakeSheets(existing_titles=[FINAL_SHEET_NAME])
    sheets.values_api.headers[FINAL_SHEET_NAME] = FINAL_COLUMNS
    sheets.values_api.rows[FINAL_SHEET_NAME] = [record("same.pdf", "100").as_row()]
    updated, appended = upsert_final_rows(sheets, "spreadsheet", [record("same.pdf", "150"), record("new.pdf", "200")])
    assert (updated, appended) == (1, 1)
    assert sheets.values_api.cleared == []
    assert sheets.values_api.batch_updated[f"'{FINAL_SHEET_NAME}'!A2:N2"] == [final_row(record("same.pdf", "150"))]


def test_upsert_final_rows_matches_existing_google_date_format() -> None:
    sheets = FakeSheets(existing_titles=[FINAL_SHEET_NAME])
    sheets.values_api.headers[FINAL_SHEET_NAME] = FINAL_COLUMNS
    existing = record("same.pdf", "100", payment_date="29.06.2026")
    incoming = record("same.pdf", "150", payment_date="2026-06-29")
    sheets.values_api.rows[FINAL_SHEET_NAME] = [existing.as_row()]

    updated, appended = upsert_final_rows(sheets, "spreadsheet", [incoming])

    assert (updated, appended) == (1, 0)
    assert sheets.values_api.batch_updated[f"'{FINAL_SHEET_NAME}'!A2:N2"] == [final_row(incoming)]



def test_upsert_final_rows_accepts_extra_google_columns_without_clearing_history() -> None:
    sheets = FakeSheets(existing_titles=[FINAL_SHEET_NAME])
    sheets.values_api.headers[FINAL_SHEET_NAME] = [*FINAL_COLUMNS, "????? ????"]
    sheets.values_api.rows[FINAL_SHEET_NAME] = [record("old.pdf", "100", payment_date="01.04.2026").as_row()]

    updated, appended = upsert_final_rows(sheets, "spreadsheet", [record("new.pdf", "200", payment_date="2026-07-13")])

    assert (updated, appended) == (0, 1)
    assert sheets.values_api.cleared == []

def test_payment_sheet_writes_use_user_entered_and_type_formats() -> None:
    sheets = FakeSheets(existing_titles=[FINAL_SHEET_NAME])
    replace_final_rows(sheets, "spreadsheet", [record("typed.pdf", "1234,56")])

    assert sheets.values_api.update_options[f"'{FINAL_SHEET_NAME}'!A1:N2"] == "USER_ENTERED"

    setup_payment_sheets(sheets, "spreadsheet")
    number_formats = [
        request["repeatCell"]
        for request in sheets.format_requests
        if request.get("repeatCell", {}).get("fields") == "userEnteredFormat.numberFormat"
    ]
    assert any(item["range"]["startColumnIndex"] == 1 and item["cell"]["userEnteredFormat"]["numberFormat"]["type"] == "DATE" for item in number_formats)
    assert any(item["range"]["startColumnIndex"] == len(FINAL_COLUMNS) - 1 and item["cell"]["userEnteredFormat"]["numberFormat"]["type"] == "NUMBER" for item in number_formats)


def test_upsert_writes_use_user_entered() -> None:
    sheets = FakeSheets(existing_titles=[FINAL_SHEET_NAME])
    sheets.values_api.headers[FINAL_SHEET_NAME] = FINAL_COLUMNS
    sheets.values_api.rows[FINAL_SHEET_NAME] = [final_row(record("same.pdf"))]

    upsert_final_rows(sheets, "spreadsheet", [record("same.pdf", "200"), record("new.pdf", "300")])

    assert sheets.values_api.batch_value_input_option == "USER_ENTERED"
    assert sheets.values_api.append_options[f"'{FINAL_SHEET_NAME}'!A1"] == "USER_ENTERED"

class FakeRequest:
    def __init__(self, result): self.result = result
    def execute(self): return self.result


class FakeValues:
    def __init__(self):
        self.headers = {}; self.rows = {}; self.updated = {}; self.batch_updated = {}; self.appended = {}; self.cleared = []
        self.update_options = {}; self.append_options = {}; self.batch_value_input_option = None
    @staticmethod
    def _sheet(range_name: str) -> str: return range_name.split("'", 2)[1]
    def get(self, spreadsheetId, range):
        title = self._sheet(range)
        values = ([self.headers[title]] if title in self.headers else []) if "A1:" in range else self.rows.get(title, [])
        return FakeRequest({"values": values})
    def update(self, spreadsheetId, range, valueInputOption, body):
        self.update_options[range] = valueInputOption
        self.updated[range] = body["values"]
        title = self._sheet(range)
        if range.endswith(("1:N1", "1:J1")): self.headers[title] = body["values"][0]
        return FakeRequest({})
    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self.append_options[range] = valueInputOption
        self.appended[range] = body["values"]; return FakeRequest({})
    def batchUpdate(self, spreadsheetId, body):
        self.batch_value_input_option = body.get("valueInputOption")
        for item in body["data"]:
            self.batch_updated[item["range"]] = item["values"]
        return FakeRequest({})
    def clear(self, spreadsheetId, range, body):
        self.cleared.append(range); return FakeRequest({})


class FakeSheets:
    def __init__(self, existing_titles=None):
        self.sheet_ids = {title: index + 1 for index, title in enumerate(existing_titles or [])}
        self.values_api = FakeValues(); self.format_requests = []
    def spreadsheets(self): return self
    def values(self): return self.values_api
    def get(self, spreadsheetId):
        return FakeRequest({"sheets": [{"properties": {"title": title, "sheetId": sheet_id}} for title, sheet_id in self.sheet_ids.items()]})
    def batchUpdate(self, spreadsheetId, body):
        for request in body["requests"]:
            if "addSheet" in request:
                title = request["addSheet"]["properties"]["title"]; self.sheet_ids[title] = len(self.sheet_ids) + 1
            else: self.format_requests.append(request)
        return FakeRequest({})

def test_payment_archive_row_contains_only_payment_fields_and_payment_link() -> None:
    source = record("one.pdf")
    assert PAYMENT_ARCHIVE_COLUMNS == [
        "№", "Дата", "Тип операции", "Тип оплаты", "Банк", "Контрагент",
        "Номер счета", "Назначение платежа", "Ссылка на ПП", "Сумма",
    ]
    assert payment_archive_row(source) == [
        source.name, "20.06.2026", source.operation_type, source.payment_type,
        source.bank, source.counterparty, source.invoice_number, source.purpose,
        source.invoice_link, source.amount,
    ]

def test_archive_upsert_keeps_different_operations_with_same_filename_and_date() -> None:
    sheets = FakeSheets(existing_titles=[PAYMENT_ARCHIVE_SHEET_NAME])
    original = record("Платежное_поручение_№133.pdf", payment_date="2026-05-25")
    different = record("Платежное_поручение_№133.pdf", payment_date="2026-05-25", amount="999")
    different.invoice_number = "other"
    sheets.values_api.headers[PAYMENT_ARCHIVE_SHEET_NAME] = PAYMENT_ARCHIVE_COLUMNS
    sheets.values_api.rows[PAYMENT_ARCHIVE_SHEET_NAME] = [payment_archive_row(original)]
    assert upsert_payment_archive(sheets, "spreadsheet", [different]) == (0, 1)


def test_archive_upsert_updates_renamed_copy_of_same_payment_number() -> None:
    sheets = FakeSheets(existing_titles=[PAYMENT_ARCHIVE_SHEET_NAME])
    original = record("Платежное_поручение_№133.pdf", payment_date="2026-05-25")
    renamed = record("Платежное_поручение_№133_25.05.2026.pdf", payment_date="2026-05-25")
    sheets.values_api.headers[PAYMENT_ARCHIVE_SHEET_NAME] = PAYMENT_ARCHIVE_COLUMNS
    sheets.values_api.rows[PAYMENT_ARCHIVE_SHEET_NAME] = [payment_archive_row(original)]
    assert upsert_payment_archive(sheets, "spreadsheet", [renamed]) == (1, 0)


def test_archive_upsert_does_not_append_exact_duplicate_incoming_rows() -> None:
    sheets = FakeSheets(existing_titles=[PAYMENT_ARCHIVE_SHEET_NAME])
    existing = record("payments 7_2.pdf", "70000", payment_date="2026-07-10")
    sheets.values_api.headers[PAYMENT_ARCHIVE_SHEET_NAME] = PAYMENT_ARCHIVE_COLUMNS
    sheets.values_api.rows[PAYMENT_ARCHIVE_SHEET_NAME] = [payment_archive_row(existing)]

    updated, appended = upsert_payment_archive(sheets, "spreadsheet", [existing, existing])

    assert (updated, appended) == (1, 0)
    assert sheets.values_api.appended == {}

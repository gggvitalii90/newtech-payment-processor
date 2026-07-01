import hashlib
from datetime import date
from pathlib import Path

from payment_processor.google_archive import (
    _folder_matches_month,
    append_archive_records,
    cleanup_archive_duplicates,
    prepare_records_for_google_drive,
    read_archive_records,
    resolve_drive_archive_folder,
)
from payment_processor.invoice_archive import INVOICE_ARCHIVE_COLUMNS, InvoiceArchiveRecord


def test_append_archive_records_updates_existing_row_by_max_key() -> None:
    sheets = FakeSheets()
    sheets.values_api.existing_values = [make_record(purpose="old").as_row()]

    append_archive_records(sheets, "sheet", "Архив счетов", [make_record(purpose="new")])

    assert sheets.values_api.append_body is None
    assert sheets.values_api.updated[0]["range"] == "'Архив счетов'!A2:W2"
    updated_row = sheets.values_api.updated[0]["body"]["values"][0]
    assert updated_row[INVOICE_ARCHIVE_COLUMNS.index("Назначение")] == "new"


def test_append_archive_records_updates_same_max_file_when_ocr_adds_invoice_number() -> None:
    sheets = FakeSheets()
    existing = make_record(counterparty="", invoice_number="", invoice_date="").as_row()
    sheets.values_api.existing_values = [existing]

    append_archive_records(
        sheets,
        "sheet",
        "\u0410\u0440\u0445\u0438\u0432 \u0441\u0447\u0435\u0442\u043e\u0432",
        [make_record(counterparty='\u041e\u041e\u041e "\u041d\u042d\u0422\u0421\u0422\u041e\u0420"', invoice_number="\u04249-0007124/\u0423", invoice_date="2026-06-22")],
    )

    assert sheets.values_api.append_body is None
    assert sheets.values_api.updated[0]["range"] == "'\u0410\u0440\u0445\u0438\u0432 \u0441\u0447\u0435\u0442\u043e\u0432'!A2:W2"


def test_prepare_records_reuses_existing_drive_link_by_max_file_id(tmp_path: Path) -> None:
    file_path = tmp_path / "invoice.pdf"
    file_path.write_bytes(b"pdf")
    record = make_record(google_drive_link="")
    existing = make_record(google_drive_link="https://drive.google.com/file/d/existing/view")
    drive = FakeDriveService()

    prepare_records_for_google_drive(
        drive,
        [record],
        {"invoice.pdf": file_path},
        "root",
        {"drive_object_folders": {"\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a": "01_ \u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"}},
        existing_records=[existing],
    )

    assert record.google_drive_link == existing.google_drive_link
    assert drive.uploads == []

def test_append_archive_records_deduplicates_existing_invoice_rows_by_invoice_number() -> None:
    sheets = FakeSheets()
    first = make_record(max_message_id="mid.old.1", max_file_id="file.old.1", purpose="old").as_row()
    second = make_record(max_message_id="mid.old.2", max_file_id="file.old.2", purpose="duplicate").as_row()
    sheets.values_api.existing_values = [first, second]

    append_archive_records(sheets, "sheet", "Архив счетов", [make_record(max_message_id="mid.new", max_file_id="file.new", purpose="new")])

    assert sheets.values_api.append_body is None
    assert sheets.values_api.updated[0]["range"] == "'Архив счетов'!A2:W2"
    updated_row = sheets.values_api.updated[0]["body"]["values"][0]
    assert updated_row[INVOICE_ARCHIVE_COLUMNS.index("Назначение")] == "new"
    assert sheets.batch_requests[0]["deleteDimension"]["range"]["startIndex"] == 2


def test_cleanup_archive_duplicates_removes_partial_message_duplicate() -> None:
    sheets = FakeSheets()
    old_row = make_record(
        file_name="",
        file_type="сообщение",
        invoice_date="",
        counterparty="ИП Мочалов",
        invoice_number="б/сч",
        project="ФОТ",
        budget_item="Официальная ЗП",
        responsible="",
        purpose="Официальная ЗП",
        amount="",
        max_message_id="mid.old",
    ).as_row()
    new_row = make_record(
        file_name="",
        file_type="сообщение",
        invoice_date="2026-06-10",
        counterparty="ИП Мочалов",
        invoice_number="б/сч",
        project="ФОТ",
        budget_item="Официальная ЗП",
        responsible="Мочалов К.",
        purpose="Официальная ЗП",
        amount="20880",
        max_message_id="mid.new",
    ).as_row()
    sheets.values_api.existing_values = [old_row, new_row]

    cleanup_archive_duplicates(sheets, "sheet", "Архив счетов")

    assert sheets.batch_requests[0]["deleteDimension"]["range"]["startIndex"] == 1


def make_record(**kwargs) -> InvoiceArchiveRecord:
    defaults = dict(
        max_date="2026-06-09 12:00:00",
        mode="ПСК",
        chat="-1",
        author="Автор",
        file_name="invoice.pdf",
        file_type="pdf",
        operation_type="",
        payment_type="",
        bank="",
        counterparty="ООО Ромашка",
        invoice_number="15",
        invoice_date="2026-06-01",
        object_name="ПСК Ньютек",
        project="Офис",
        budget_item="Расходники",
        responsible="Родин.К",
        purpose="Оплата",
        amount="1000",
        payment_status="Новый",
        google_drive_link="",
        max_message_id="mid.1",
        max_file_id="file.1",
        analysis_status="",
    )
    defaults.update(kwargs)
    return InvoiceArchiveRecord(**defaults)


def test_invoice_archive_columns_use_payment_and_analysis_statuses() -> None:
    assert "Год счета" not in INVOICE_ARCHIVE_COLUMNS
    assert "Поток" in INVOICE_ARCHIVE_COLUMNS
    assert "Статус оплаты" in INVOICE_ARCHIVE_COLUMNS
    assert "Статус разбора" in INVOICE_ARCHIVE_COLUMNS


def test_folder_matches_month_supports_number_and_name() -> None:
    assert _folder_matches_month("05 май", 5)
    assert _folder_matches_month("Июнь 2026", 6)
    assert not _folder_matches_month("Июнь 2026", 5)


class FakeDriveService:
    def __init__(self):
        self.children = {
            "root": [{"id": "obj", "name": "01_ ПСК Ньютек"}],
            "obj": [{"id": "year", "name": "2026"}],
            "year": [{"id": "month", "name": "Июнь 2026"}],
        }
        self.uploads = []
        self.created_folders = []

    def files(self):
        return self

    def list(self, q, fields, supportsAllDrives, includeItemsFromAllDrives, pageSize):
        parent = q.split("'")[1]
        files = self.children.get(parent, [])
        return FakeExecute({"files": files})

    def create(self, body, media_body=None, fields="", supportsAllDrives=True):
        if body.get("mimeType") == "application/vnd.google-apps.folder":
            folder_id = f"folder-{len(self.created_folders) + 1}"
            self.created_folders.append(body)
            parent = body["parents"][0]
            self.children.setdefault(parent, []).append({"id": folder_id, "name": body["name"]})
            return FakeExecute({"id": folder_id})
        self.uploads.append(body)
        return FakeExecute({"id": "uploaded", "webViewLink": "https://drive.example/uploaded"})


class FakeExecute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


def test_resolve_drive_archive_folder_uses_dictionary_path() -> None:
    folder_id = resolve_drive_archive_folder(
        FakeDriveService(),
        "root",
        make_record(),
        {"drive_object_folders": {"ПСК Ньютек": "01_ ПСК Ньютек"}, "drive_month_folders": {"2026-06": "Июнь 2026"}},
        today=date(2026, 7, 1),
    )

    assert folder_id == "month"


def test_resolve_drive_archive_folder_does_not_create_missing_month_folder() -> None:
    drive = FakeDriveService()
    drive.children["year"] = []
    drive.children["obj"].append({"id": "month", "name": "Июнь 2026"})

    folder_id = resolve_drive_archive_folder(
        drive,
        "root",
        make_record(),
        {"drive_object_folders": {"ПСК Ньютек": "01_ ПСК Ньютек"}, "drive_month_folders": {"2026-06": "Июнь 2026"}},
        today=date(2026, 7, 1),
    )

    assert folder_id == ""
    assert drive.created_folders == []


def test_resolve_drive_archive_folder_does_not_create_missing_configured_month_folder() -> None:
    drive = FakeDriveService()
    drive.children["year"] = []
    record = make_record(invoice_date="2026-05-20", max_date="2026-06-10 08:30:38")

    folder_id = resolve_drive_archive_folder(
        drive,
        "root",
        record,
        {"drive_object_folders": {"ПСК Ньютек": "01_ ПСК Ньютек"}, "drive_month_folders": {"2026-06": "06 Июнь"}},
        today=date(2026, 7, 1),
    )

    assert folder_id == ""
    assert drive.created_folders == []


def test_resolve_drive_archive_folder_does_not_create_missing_object_folder() -> None:
    drive = FakeDriveService()

    folder_id = resolve_drive_archive_folder(
        drive,
        "root",
        make_record(object_name="Влад Русхолод"),
        {"drive_object_folders": {}},
    )

    assert folder_id == ""
    assert drive.created_folders == []


def test_resolve_drive_archive_folder_uses_flat_object_folder() -> None:
    drive = FakeDriveService()
    drive.children["root"].append({"id": "conversion", "name": "Конвертация"})

    folder_id = resolve_drive_archive_folder(
        drive,
        "root",
        make_record(object_name="Конвертация", invoice_date="2026-06-09", max_date="2026-06-10 09:32:18"),
        {"drive_object_folders": {"Конвертация": "Конвертация"}, "drive_flat_objects": ["Конвертация"]},
    )

    assert folder_id == "conversion"



def test_prepare_records_reuses_duplicate_from_any_archive_folder(tmp_path: Path) -> None:
    file_path = tmp_path / "invoice.pdf"
    file_path.write_bytes(b"pdf")
    record = make_record()
    drive = FakeDriveService()
    drive.children["root"].append({"id": "other", "name": "Other object"})
    drive.children["other"] = [
        {
            "id": "existing-file",
            "name": "already-there.pdf",
            "md5Checksum": hashlib.md5(b"pdf").hexdigest(),
            "webViewLink": "https://drive.example/existing",
        }
    ]

    prepare_records_for_google_drive(
        drive,
        [record],
        {"invoice.pdf": file_path},
        "root",
        {"drive_object_folders": {"\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a": "01_ \u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"}},
    )

    assert record.google_drive_link == "https://drive.example/existing"
    assert record.analysis_status == "\u0414\u0443\u0431\u043b\u044c"
    assert drive.uploads == []

def test_prepare_records_uploads_when_folder_found(tmp_path: Path) -> None:
    file_path = tmp_path / "invoice.pdf"
    file_path.write_bytes(b"pdf")
    record = make_record()
    drive = FakeDriveService()

    prepare_records_for_google_drive(
        drive,
        [record],
        {"invoice.pdf": file_path},
        "root",
        {"drive_object_folders": {"ПСК Ньютек": "01_ ПСК Ньютек"}, "drive_month_folders": {"2026-06": "Июнь 2026"}},
    )

    assert record.google_drive_link == "https://drive.example/uploaded"
    assert record.analysis_status == "ОК"
    assert drive.uploads[0]["parents"] in (["obj"], ["month"])


def test_prepare_records_replaces_technical_image_name(tmp_path: Path) -> None:
    file_path = tmp_path / "i.webp"
    file_path.write_bytes(b"webp")
    record = make_record(
        max_date="2026-05-15 14:14:31",
        file_name="i",
        file_type="",
        counterparty='ООО "НАВИГАТОР"',
        invoice_number="24",
        invoice_date="2026-05-12",
    )
    drive = FakeDriveService()
    drive.children["year"] = [{"id": "month", "name": "Май 2026"}]

    prepare_records_for_google_drive(
        drive,
        [record],
        {"i": file_path},
        "root",
        {
            "drive_object_folders": {"ПСК Ньютек": "01_ ПСК Ньютек"},
            "drive_month_folders": {"2026-05": "Май 2026"},
        },
    )

    assert record.file_name == "Счет №24 от 12.05.2026 ООО НАВИГАТОР.webp"
    assert record.file_type == "webp"
    assert drive.uploads[0]["name"] == record.file_name


def test_prepare_records_uploads_unresolved_file_to_review_folder(tmp_path: Path) -> None:
    file_path = tmp_path / "invoice.pdf"
    file_path.write_bytes(b"pdf")
    record = make_record(object_name="", analysis_status="Нужно разобрать")
    drive = FakeDriveService()
    drive.children["root"].append({"id": "review", "name": "__"})

    prepare_records_for_google_drive(
        drive,
        [record],
        {"invoice.pdf": file_path},
        "root",
        {"unresolved_status": "Нужно разобрать"},
    )

    assert record.google_drive_link == "https://drive.example/uploaded"
    assert record.analysis_status == "Нужно разобрать"
    assert drive.created_folders == []
    assert drive.uploads[0]["parents"] == ["review"]


def test_prepare_records_blocks_upload_when_review_folder_is_missing(tmp_path: Path) -> None:
    file_path = tmp_path / "invoice.pdf"
    file_path.write_bytes(b"pdf")
    record = make_record(object_name="", analysis_status="Нужно разобрать")
    drive = FakeDriveService()

    prepare_records_for_google_drive(
        drive,
        [record],
        {"invoice.pdf": file_path},
        "root",
        {"unresolved_status": "Нужно разобрать"},
    )

    assert record.google_drive_link == ""
    assert record.analysis_status == "Нужно разобрать"
    assert drive.created_folders == []
    assert drive.uploads == []


def test_prepare_records_keeps_fileless_message_without_drive_upload() -> None:
    record = make_record(file_name="", file_type="сообщение")
    drive = FakeDriveService()

    prepare_records_for_google_drive(drive, [record], {}, "root", {})

    assert record.analysis_status == "ОК"
    assert record.google_drive_link == ""
    assert drive.uploads == []


class FakeSheetsValues:
    def __init__(self):
        self.append_body = None
        self.updated = []
        self.existing_values = []
        self.headers = list(INVOICE_ARCHIVE_COLUMNS)

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self.append_body = body
        return FakeExecute({})

    def get(self, spreadsheetId, range):
        if range.endswith("!A1:AZ1"):
            return FakeExecute({"values": [self.headers]})
        return FakeExecute({"values": self.existing_values})

    def update(self, spreadsheetId, range, valueInputOption, body):
        self.updated.append({"range": range, "body": body})
        return FakeExecute({})


class FakeSheets:
    def __init__(self):
        self.values_api = FakeSheetsValues()
        self.batch_requests = []

    def spreadsheets(self):
        return self

    def values(self):
        return self.values_api

    def get(self, spreadsheetId):
        return FakeExecute({"sheets": [{"properties": {"title": "Архив счетов", "sheetId": 123}}]})

    def batchUpdate(self, spreadsheetId, body):
        self.batch_requests.extend(body.get("requests", []))
        return FakeExecute({})


def test_append_archive_records_uses_record_rows() -> None:
    sheets = FakeSheets()

    append_archive_records(sheets, "sheet", "Архив счетов", [make_record()])

    assert sheets.values_api.append_body["values"][0][INVOICE_ARCHIVE_COLUMNS.index("Статус оплаты")] == "Новый"
    assert len(sheets.values_api.append_body["values"][0]) == len(INVOICE_ARCHIVE_COLUMNS)


def test_read_archive_records_builds_records_from_sheet_rows() -> None:
    sheets = FakeSheets()
    sheets.values_api.existing_values = [make_record(invoice_number="291").as_row()]

    records = read_archive_records(sheets, "sheet", "Архив счетов")

    assert len(records) == 1
    assert records[0].invoice_number == "291"
    assert records[0].object_name == make_record().object_name

def test_resolve_drive_archive_folder_uses_object_folder_for_current_month() -> None:
    folder_id = resolve_drive_archive_folder(
        FakeDriveService(),
        "root",
        make_record(max_date="2026-06-24 10:00:00"),
        {"drive_object_folders": {"\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a": "01_ \u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"}},
        today=date(2026, 6, 24),
    )

    assert folder_id == "obj"

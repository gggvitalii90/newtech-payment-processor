from pathlib import Path

from payment_processor.archive_reconciliation import (
    build_chat_updated_row,
    build_message_evidence,
    match_by_content,
    sha256_content,
)
from payment_processor.google_api import download_drive_file
from payment_processor.dictionaries import normalize_value
from payment_processor.max_api import FileCandidate
from payment_processor.max_message_matching import match_signatures
from scripts.reconcile_google_archive_chat_fields import (
    apply_cell_updates,
    build_cell_updates,
    candidate_cache_name,
    load_audited_plan_rows,
    match_hash,
    match_missing_drive,
    validate_download_errors,
)


def test_strict_normalization_rejects_unknown_value() -> None:
    result = normalize_value(
        "projects",
        "Кмд корректировка",
        {"unresolved_status": "Нужно разобрать", "projects": {"ПИР": ["кмд"]}},
        strict=True,
    )

    assert result.value == ""
    assert result.status == "Нужно разобрать"
    assert result.matched is False


def test_responsible_falls_back_to_sender_when_current_value_is_edo() -> None:
    headers = ["Объект", "Проект", "Статья бюджета", "Ответственный", "Назначение", "Статус разбора"]
    row = ["ПСК Ньютек", "Офис", "Расходники", "ЭДО", "ремонт", ""]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"ПСК Ньютек": ["пск"]},
        "projects": {"Офис": ["офис"]},
        "budget_items": {"Расходники": ["расходники"]},
        "responsibles": {"Соловцов Н.": ["Николай Соловцов"]},
    }

    updated = build_chat_updated_row(
        headers,
        row,
        {"object_name": "ПСК"},
        "author_block",
        dictionaries,
        fallback_author="Николай Соловцов",
    )

    assert updated[headers.index("Ответственный")] == "Соловцов Н."


def test_document_fields_fill_counterparty_amount_and_missing_purpose() -> None:
    headers = ["Контрагент", "Объект", "Проект", "Статья бюджета", "Ответственный", "Назначение", "Сумма", "Статус разбора"]
    row = ["получатель платежа", "ПСК Ньютек", "Офис", "Расходники", "Соловцов Н.", "", "", ""]
    dictionaries = {
        "objects": {"ПСК Ньютек": ["пск"]},
        "projects": {"Офис": ["офис"]},
        "budget_items": {"Расходники": ["расходники"]},
        "responsibles": {"Соловцов Н.": ["Николай Соловцов"]},
    }

    updated = build_chat_updated_row(
        headers,
        row,
        {"object_name": "ПСК"},
        "exact_link",
        dictionaries,
        document_fields={
            "counterparty": 'ООО "Руспан"',
            "amount": "2236496,61",
            "purpose": "Панели",
        },
    )

    assert updated[headers.index("Контрагент")] == 'ООО "Руспан"'
    assert updated[headers.index("Сумма")] == "2236496,61"
    assert updated[headers.index("Назначение")] == "Панели"


def test_unknown_regulated_value_preserves_existing_cell_and_marks_review() -> None:
    headers = ["Объект", "Проект", "Статья бюджета", "Ответственный", "Назначение", "Статус разбора"]
    row = ["ПСК Ньютек", "Офис", "Расходники", "Соловцов Н.", "ремонт", ""]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"ПСК Ньютек": ["пск"]},
        "projects": {"Офис": ["офис"]},
    }

    updated = build_chat_updated_row(
        headers, row, {"object_name": "неизвестный объект", "project": "неизвестный проект"},
        "author_block", dictionaries,
    )

    assert updated[headers.index("Объект")] == "ПСК Ньютек"
    assert updated[headers.index("Проект")] == "Офис"
    assert updated[headers.index("Статус разбора")] == "Нужно разобрать"


def test_document_counterparty_does_not_replace_different_existing_legal_name() -> None:
    headers = ["Контрагент", "Объект", "Статус разбора"]
    row = ['ООО "Старый"', "ПСК Ньютек", ""]
    dictionaries = {"objects": {"ПСК Ньютек": ["пск"]}}

    updated = build_chat_updated_row(
        headers, row, {"object_name": "ПСК"}, "exact_link", dictionaries,
        document_fields={"counterparty": 'ООО "Другой"'},
    )

    assert updated[0] == 'ООО "Старый"'


def test_document_counterparty_replaces_obvious_garbage_with_legal_name() -> None:
    headers = ["Контрагент", "Объект", "Статус разбора"]
    row = ["вправе отказаться от заключения договора", "ПСК Ньютек", ""]
    dictionaries = {"objects": {"ПСК Ньютек": ["пск"]}}

    updated = build_chat_updated_row(
        headers, row, {"object_name": "ПСК"}, "exact_link", dictionaries,
        document_fields={"counterparty": 'ООО "Профкомплект"'},
    )

    assert updated[0] == 'ООО "Профкомплект"'


def test_document_amount_does_not_replace_different_existing_amount() -> None:
    headers = ["Объект", "Сумма", "Статус разбора"]
    row = ["ПСК Ньютек", "10000", ""]
    dictionaries = {"objects": {"ПСК Ньютек": ["пск"]}}

    updated = build_chat_updated_row(
        headers, row, {"object_name": "ПСК"}, "exact_link", dictionaries,
        document_fields={"amount": "90000,00"},
    )

    assert updated[1] == "10000"


def test_ambiguous_row_still_normalizes_known_existing_aliases() -> None:
    headers = ["Объект", "Проект", "Статья бюджета", "Ответственный", "Назначение", "Статус разбора"]
    row = ["Егунян 2", "КМ изготовление", "Материал", "Соловцов Н.", "металл", ""]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"ИП Егунян": ["егунян 2"]},
        "projects": {"КМ ( ПР )": ["км изготовление"]},
        "budget_items": {"Материалы": ["материал"]},
        "responsibles": {"Соловцов Н.": ["соловцов н"]},
    }

    updated = build_chat_updated_row(headers, row, {}, "ambiguous", dictionaries)

    assert updated[0] == "ИП Егунян"
    assert updated[1] == "КМ ( ПР )"
    assert updated[2] == "Материалы"


def test_existing_counterparty_abbreviates_full_legal_form_when_ambiguous() -> None:
    headers = ["Контрагент", "Объект", "Статус разбора"]
    row = [
        'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ТОРГОВЫЙ ДОМ СВАРЛЕН"',
        "ПСК Ньютек",
        "",
    ]

    updated = build_chat_updated_row(
        headers,
        row,
        {},
        "ambiguous",
        {"objects": {"ПСК Ньютек": ["пск"]}},
        {"Объект": ["ПСК Ньютек"]},
    )

    assert updated[0] == 'ООО "ТОРГОВЫЙ ДОМ СВАРЛЕН"'


def test_existing_counterparty_extracts_legal_name_after_payment_prefix() -> None:
    headers = ["Контрагент", "Объект", "Статус разбора"]
    row = ['платежа ООО "КОМУС"', "ПСК Ньютек", ""]

    updated = build_chat_updated_row(
        headers,
        row,
        {},
        "ambiguous",
        {"objects": {"ПСК Ньютек": ["пск"]}},
        {"Объект": ["ПСК Ньютек"]},
    )

    assert updated[0] == 'ООО "КОМУС"'

def test_document_counterparty_replaces_payment_prefix_garbage() -> None:
    headers = ["Контрагент", "Объект", "Статус разбора"]
    row = ['180 000,00 ₽ Без НДС ООО "Банк Точка"', "ПСК Ньютек", ""]

    updated = build_chat_updated_row(
        headers,
        row,
        {"object_name": "ПСК"},
        "exact_link",
        {"objects": {"ПСК Ньютек": ["пск"]}},
        document_fields={"counterparty": 'ООО "КОМУС"'},
    )

    assert updated[0] == 'ООО "КОМУС"'


def test_existing_unknown_regulated_values_are_cleared_and_marked_for_review() -> None:
    headers = ["Объект", "Проект", "Статья бюджета", "Ответственный", "Статус разбора"]
    row = ["ПСК Ньютек", "неизвестный проект", "Какая?", "Соловцов Н.", ""]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"ПСК Ньютек": ["пск"]},
        "responsibles": {"Соловцов Н.": ["соловцов"]},
    }
    references = {
        "Объект": ["ПСК Ньютек"],
        "Проект": ["Офис"],
        "Статья бюджета": ["Расходники"],
        "Ответственный": ["Соловцов Н."],
    }

    updated = build_chat_updated_row(headers, row, {}, "ambiguous", dictionaries, references)

    assert updated[headers.index("Проект")] == ""
    assert updated[headers.index("Статья бюджета")] == ""
    assert updated[headers.index("Статус разбора")] == "Нужно разобрать"


def test_ambiguous_edo_responsible_falls_back_to_sender() -> None:
    headers = ["Объект", "Ответственный", "Статус разбора"]
    row = ["ПСК Ньютек", "ЭДО", ""]
    dictionaries = {
        "objects": {"ПСК Ньютек": ["пск"]},
        "responsibles": {"Соловцов Н.": ["Николай Соловцов"]},
    }
    references = {"Объект": ["ПСК Ньютек"], "Ответственный": ["Соловцов Н."]}

    updated = build_chat_updated_row(
        headers,
        row,
        {},
        "ambiguous",
        dictionaries,
        references,
        fallback_author="Николай Соловцов",
    )

    assert updated[headers.index("Ответственный")] == "Соловцов Н."

def test_forwarded_file_uses_outer_archive_sequence_for_block_order() -> None:
    messages = [
        {
            "timestamp": 1000,
            "body": {"mid": "outer-file", "seq": 100, "text": ""},
            "sender": {"user_id": "a", "name": "Автор"},
            "link": {
                "type": "forward",
                "message": {
                    "mid": "inner-file",
                    "seq": 1,
                    "attachments": [{"type": "file", "filename": "invoice.pdf", "payload": {"fileId": "f1", "url": "https://files/f1"}}],
                },
            },
        },
        {
            "timestamp": 1100,
            "body": {"mid": "signature", "seq": 101, "text": "Объект: ПСК"},
            "sender": {"user_id": "a", "name": "Автор"},
        },
    ]

    evidence, _ = build_message_evidence(messages)
    linked = next(item for item in evidence if item.message_id == "inner-file")

    assert linked.seq == 100
    assert linked.timestamp == 1000.0


def test_reconciliation_matches_drive_and_max_by_sha256_not_name() -> None:
    drive = b"invoice-a"
    max_files = {"same.pdf": b"invoice-b", "other-name.pdf": b"invoice-a"}

    result = match_by_content(drive, max_files)

    assert result.max_key == "other-name.pdf"
    assert result.confidence == "content_exact"
    assert result.sha256 == sha256_content(drive)


def test_reconciliation_marks_duplicate_content_ambiguous() -> None:
    result = match_by_content(
        b"same-content",
        {"first.pdf": b"same-content", "second.pdf": b"same-content"},
    )

    assert result.max_key == ""
    assert result.confidence == "ambiguous"
    assert result.candidate_keys == ("first.pdf", "second.pdf")


def test_match_hash_uses_existing_max_file_id_to_resolve_duplicate_content() -> None:
    candidates = {
        "first": FileCandidate("same.pdf", "https://files/1", "file-1", "message-1", ""),
        "second": FileCandidate("same.pdf", "https://files/2", "file-2", "message-2", ""),
    }

    result = match_hash(
        "same-hash",
        {"first": "same-hash", "second": "same-hash"},
        candidates,
        preferred_file_id="file-2",
    )

    assert result.max_key == "second"
    assert result.confidence == "content_file_id"


def test_candidate_cache_name_does_not_depend_on_temporary_url() -> None:
    first = FileCandidate("invoice.pdf", "https://files/temporary-1", "file-1", "message-1", "")
    second = FileCandidate("invoice.pdf", "https://files/temporary-2", "file-1", "message-1", "")

    assert candidate_cache_name(first) == candidate_cache_name(second)
    assert candidate_cache_name(first).endswith(".pdf")


def test_missing_drive_file_is_resolved_only_by_unique_max_file_id() -> None:
    candidates = {
        "first": FileCandidate("same.pdf", "https://files/1", "file-1", "message-1", ""),
        "second": FileCandidate("same.pdf", "https://files/2", "file-2", "message-2", ""),
    }

    result = match_missing_drive(candidates, "file-2")

    assert result.max_key == "second"
    assert result.confidence == "max_file_id"


def test_download_errors_block_apply_but_not_read_only_audit() -> None:
    validate_download_errors(["temporary SSL error"], applying=False)

    import pytest
    with pytest.raises(RuntimeError, match="temporary SSL error"):
        validate_download_errors(["temporary SSL error"], applying=True)


class FakeMediaRequest:
    pass


class FakeDriveFiles:
    def __init__(self) -> None:
        self.file_id = ""
        self.supports_all_drives = False

    def get_media(self, *, fileId: str, supportsAllDrives: bool):
        self.file_id = fileId
        self.supports_all_drives = supportsAllDrives
        return FakeMediaRequest()


class FakeDriveService:
    def __init__(self) -> None:
        self.files_api = FakeDriveFiles()

    def files(self) -> FakeDriveFiles:
        return self.files_api


def test_download_drive_file_uses_exact_file_id(monkeypatch, tmp_path: Path) -> None:
    class FakeDownloader:
        def __init__(self, handle, request) -> None:
            assert isinstance(request, FakeMediaRequest)
            self.handle = handle

        def next_chunk(self):
            self.handle.write(b"drive-content")
            return None, True

    monkeypatch.setattr("payment_processor.google_api.MediaIoBaseDownload", FakeDownloader)
    drive = FakeDriveService()
    destination = tmp_path / "downloaded.bin"

    result = download_drive_file(drive, "drive-id-123", destination)

    assert result == destination
    assert destination.read_bytes() == b"drive-content"
    assert drive.files_api.file_id == "drive-id-123"
    assert drive.files_api.supports_all_drives is True


def test_build_message_evidence_matches_signature_in_same_body() -> None:
    messages = [
        {
            "timestamp": 1000,
            "body": {
                "mid": "message-1",
                "seq": 10,
                "text": "Объект: ПСК",
                "attachments": [
                    {"type": "file", "filename": "invoice.pdf", "file_id": "file-1", "url": "https://files/1"}
                ],
            },
        }
    ]

    evidence, candidates = build_message_evidence(messages)
    file_key = next(iter(candidates))
    match = match_signatures(evidence)[file_key]

    assert match.confidence == "exact_body"
    assert match.signature == {"object_name": "ПСК"}


def test_build_message_evidence_matches_reply_to_linked_file() -> None:
    messages = [
        {
            "timestamp": 2000,
            "body": {"mid": "reply-1", "seq": 20, "text": "Объект: ПСК"},
            "link": {
                "type": "reply",
                "message": {
                    "mid": "file-message",
                    "seq": 10,
                    "attachments": [
                        {"type": "file", "filename": "invoice.pdf", "file_id": "file-1", "url": "https://files/1"}
                    ],
                },
            },
        }
    ]

    evidence, candidates = build_message_evidence(messages)
    file_key = next(iter(candidates))
    match = match_signatures(evidence)[file_key]

    assert match.confidence == "exact_link"
    assert match.signature_message_id == "reply-1"


def test_build_message_evidence_prefers_actual_message_timestamp_over_link_copy() -> None:
    actual = {
        "timestamp": 1_780_000_001_000,
        "body": {
            "mid": "file-message",
            "seq": 10,
            "attachments": [
                {"type": "file", "filename": "invoice.pdf", "file_id": "file-1", "url": "https://files/1"}
            ],
        },
    }
    reply = {
        "timestamp": 1_780_000_002_000,
        "body": {"mid": "reply-1", "seq": 20, "text": "Объект: ПСК"},
        "link": {"type": "reply", "message": actual["body"]},
    }

    evidence, _ = build_message_evidence([reply, actual])
    file_message = next(item for item in evidence if item.message_id == "file-message")

    assert file_message.timestamp == 1_780_000_001.0
    assert file_message.seq == 10


def test_build_chat_updated_row_changes_only_chat_columns() -> None:
    headers = [
        "Дата MAX", "Контрагент", "Номер счета", "Объект", "Проект", "Статья бюджета",
        "Ответственный", "Назначение", "Сумма", "Статус оплаты", "Google Drive ссылка", "Статус разбора",
    ]
    original = [
        "2026-06-01", "ООО Документ", "42", "Старый объект", "Старый проект", "Старая статья",
        "Старый ответственный", "Старое назначение", "1000", "Оплачен", "https://drive/123", "Старый статус",
    ]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"ПСК Ньютек": ["ПСК"]},
        "projects": {"Офис": ["офис"]},
        "budget_items": {"Топливо": ["топливо"]},
        "responsibles": {"Родин.К": ["родин"]},
    }

    updated = build_chat_updated_row(
        headers,
        original,
        {"object_name": "ПСК", "project": "офис", "budget_item": "топливо", "responsible": "родин", "purpose": "Счёт"},
        "exact_body",
        dictionaries,
    )

    assert updated[headers.index("Объект")] == "ПСК Ньютек"
    assert updated[headers.index("Проект")] == "Офис"
    assert updated[headers.index("Статья бюджета")] == "Топливо"
    assert updated[headers.index("Ответственный")] == "Родин.К"
    assert updated[headers.index("Назначение")] == "Счёт"
    assert updated[headers.index("Статус разбора")] == ""
    for column in ("Дата MAX", "Контрагент", "Номер счета", "Сумма", "Статус оплаты", "Google Drive ссылка"):
        assert updated[headers.index(column)] == original[headers.index(column)]


def test_build_chat_updated_row_accepts_sequence_unique_confidence() -> None:
    headers = ["Объект", "Проект", "Статья бюджета", "Ответственный", "Назначение", "Статус разбора"]
    original = ["Старый", "", "", "", "", ""]
    dictionaries = {"objects": {"ПСК Ньютек": ["ПСК"]}}

    updated = build_chat_updated_row(
        headers,
        original,
        {"object_name": "ПСК"},
        "sequence_unique",
        dictionaries,
    )

    assert updated[0] == "ПСК Ньютек"


def test_build_chat_updated_row_preserves_fields_missing_from_confirmed_signature() -> None:
    headers = ["Объект", "Проект", "Статья бюджета", "Ответственный", "Назначение", "Статус разбора"]
    original = ["Старый объект", "Старый проект", "Старая статья", "Старый ответственный", "Старое назначение", ""]
    dictionaries = {"objects": {"ПСК Ньютек": ["ПСК"]}}

    updated = build_chat_updated_row(
        headers,
        original,
        {"object_name": "ПСК", "counterparty": "ООО Ромашка"},
        "exact_link",
        dictionaries,
    )

    assert updated == ["ПСК Ньютек", "Старый проект", "Старая статья", "Старый ответственный", "Старое назначение", ""]


def test_build_chat_updated_row_preserves_chat_columns_when_ambiguous() -> None:
    headers = ["Объект", "Проект", "Статья бюджета", "Ответственный", "Назначение", "Сумма", "Статус разбора"]
    original = ["A", "B", "C", "D", "E", "100", ""]

    updated = build_chat_updated_row(headers, original, {"object_name": "ПСК"}, "ambiguous", {"unresolved_status": "Нужно разобрать"})

    assert updated == original


def test_build_cell_updates_targets_only_allowed_changed_cells() -> None:
    headers = ["Дата MAX", "Объект", "Проект", "Сумма", "Статус разбора"]
    old = ["2026-06-01", "Старый", "Проект", "100", ""]
    new = ["2026-06-01", "Новый", "Проект", "100", "Нужно разобрать"]

    updates = build_cell_updates("Архив счетов", headers, [(7, old, new)])

    assert updates == [
        {"range": "'Архив счетов'!B7", "values": [["Новый"]]},
        {"range": "'Архив счетов'!E7", "values": [["Нужно разобрать"]]},
    ]


class FakeBatchRequest:
    def __init__(self, owner) -> None:
        self.owner = owner

    def execute(self):
        self.owner.executed = True
        return {"totalUpdatedCells": 2}


class FakeValuesApi:
    def __init__(self) -> None:
        self.call = None
        self.executed = False

    def batchUpdate(self, **kwargs):
        self.call = kwargs
        return FakeBatchRequest(self)


class FakeSpreadsheetsApi:
    def __init__(self) -> None:
        self.values_api = FakeValuesApi()

    def values(self):
        return self.values_api


class FakeSheetsService:
    def __init__(self) -> None:
        self.api = FakeSpreadsheetsApi()

    def spreadsheets(self):
        return self.api


def test_apply_cell_updates_uses_raw_batch_values() -> None:
    sheets = FakeSheetsService()
    updates = [{"range": "'Архив счетов'!B7", "values": [["Новый"]]}]

    apply_cell_updates(sheets, "spreadsheet-id", updates)

    assert sheets.api.values_api.call == {
        "spreadsheetId": "spreadsheet-id",
        "body": {"valueInputOption": "RAW", "data": updates},
    }
    assert sheets.api.values_api.executed is True


def test_load_audited_plan_rows_rejects_stale_old_values(tmp_path: Path) -> None:
    plan = tmp_path / "plan.csv"
    plan.write_text(
        "row_number,old_Объект,new_Объект,old_Проект,new_Проект,old_Статья бюджета,new_Статья бюджета,"
        "old_Ответственный,new_Ответственный,old_Назначение,new_Назначение,old_Статус разбора,new_Статус разбора\n"
        "2,Другое,Новое,,,,,,,,,,,\n",
        encoding="utf-8-sig",
    )

    headers = ["Объект", "Проект", "Статья бюджета", "Ответственный", "Назначение", "Статус разбора"]

    try:
        load_audited_plan_rows(plan, headers, [["Текущее", "", "", "", "", ""]])
    except RuntimeError as exc:
        assert "stale" in str(exc).lower()
    else:
        raise AssertionError("stale plan must be rejected")


def test_build_restore_rows_restores_only_editable_columns() -> None:
    from scripts.reconcile_google_archive_chat_fields import build_restore_rows

    headers = ["Контрагент", "Объект", "Проект", "Статус разбора"]
    backup = [headers, ["ООО Документ", "ПСК", "Офис", ""]]
    current = [headers, ["ООО Документ", "", "", "Нужно разобрать"]]

    rows = build_restore_rows(backup, current, headers)

    assert rows == [(2, current[1], backup[1])]

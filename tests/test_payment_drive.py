from datetime import date

from payment_processor.payment_drive import (
    parse_day_folder_name,
    plan_folder_moves,
    move_day_folder,
    find_payment_file_link,
    ensure_payment_file,
)


def test_parse_day_folder_name_preserves_psk_and_is_names() -> None:
    assert parse_day_folder_name("2026.06.19") == ("2026", "06")
    assert parse_day_folder_name("2026.06.19 ИС") == ("2026", "06")
    assert parse_day_folder_name("2026.02.30") is None
    assert parse_day_folder_name("2026") is None


def test_plan_folder_moves_ignores_non_day_folders() -> None:
    children = [
        {"id": "day-1", "name": "2026.06.19"},
        {"id": "day-2", "name": "2025.12.01 ИС"},
        {"id": "year", "name": "2026"},
        {"id": "misc", "name": "Нужно разобрать"},
    ]
    assert plan_folder_moves(children) == [
        {"id": "day-2", "name": "2025.12.01 ИС", "year": "2025", "month": "12"},
        {"id": "day-1", "name": "2026.06.19", "year": "2026", "month": "06"},
    ]


class FakeRequest:
    def __init__(self, result): self.result = result
    def execute(self): return self.result


class FakeFiles:
    def __init__(self): self.updates = []
    def update(self, **kwargs):
        self.updates.append(kwargs)
        return FakeRequest({"id": kwargs["fileId"], "parents": [kwargs["addParents"]]})


class FakeDrive:
    def __init__(self): self.files_api = FakeFiles()
    def files(self): return self.files_api


def test_move_day_folder_changes_parent_without_copying_or_changing_id() -> None:
    drive = FakeDrive()
    result = move_day_folder(drive, "day-id", "root-id", "month-id")
    assert result == {"id": "day-id", "parents": ["month-id"]}
    assert drive.files_api.updates == [{
        "fileId": "day-id",
        "addParents": "month-id",
        "removeParents": "root-id",
        "fields": "id,parents",
        "supportsAllDrives": True,
    }]

def test_find_payment_file_link_reuses_same_content(monkeypatch, tmp_path) -> None:
    source = tmp_path / "payment.pdf"
    source.write_bytes(b"same payment")
    monkeypatch.setattr("payment_processor.payment_drive.resolve_day_folder_id", lambda *args, **kwargs: "day")
    monkeypatch.setattr("payment_processor.payment_drive.list_child_files", lambda *args: [{
        "id": "existing", "name": "old-name.pdf",
        "md5Checksum": "dfc0c70837b9673e7b364dc3e6022a5d",
        "webViewLink": "https://drive/existing",
    }])
    assert find_payment_file_link(object(), "root", source, date(2026, 6, 20)) == "https://drive/existing"


def test_ensure_payment_file_uploads_only_when_content_is_missing(monkeypatch, tmp_path) -> None:
    source = tmp_path / "payment.pdf"
    source.write_bytes(b"new payment")
    monkeypatch.setattr("payment_processor.payment_drive.find_payment_file_link", lambda *args, **kwargs: "")
    monkeypatch.setattr("payment_processor.payment_drive.resolve_day_folder_id", lambda *args, **kwargs: "day")
    calls = []
    monkeypatch.setattr(
        "payment_processor.payment_drive.upload_file_to_folder",
        lambda drive, path, folder, file_name="": calls.append((path, folder, file_name)) or "https://drive/new",
    )
    assert ensure_payment_file(object(), "root", source, date(2026, 6, 20)) == "https://drive/new"
    assert calls == [(source, "day", "payment.pdf")]
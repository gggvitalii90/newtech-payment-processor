from pathlib import Path

import pytest

from payment_processor.google_api import GoogleConfigError, extract_google_id, load_google_settings


def test_extract_google_id_from_spreadsheet_and_folder_urls() -> None:
    assert (
        extract_google_id("https://docs.google.com/spreadsheets/d/1mxA-J89EOZVTXcAuAFSXL4hlbEee2h2stC5djOG1hBU/edit")
        == "1mxA-J89EOZVTXcAuAFSXL4hlbEee2h2stC5djOG1hBU"
    )
    assert (
        extract_google_id("https://drive.google.com/drive/folders/1Z5cfBgH70qIeblbA9TDvKPxJbYUdX6xR")
        == "1Z5cfBgH70qIeblbA9TDvKPxJbYUdX6xR"
    )
    assert extract_google_id("https://drive.google.com/file/d/file-id/view?usp=sharing") == "file-id"
    assert extract_google_id("https://docs.google.com/document/d/document-id/edit") == "document-id"
    assert extract_google_id("raw-id") == "raw-id"


def test_load_google_settings_requires_client_secret() -> None:
    with pytest.raises(GoogleConfigError):
        load_google_settings({})


def test_load_google_settings_normalizes_ids() -> None:
    settings = load_google_settings(
        {
            "GOOGLE_CLIENT_SECRET_FILE": r"C:\secret.json",
            "GOOGLE_TOKEN_FILE": "token.json",
            "GOOGLE_ARCHIVE_SPREADSHEET_ID": "https://docs.google.com/spreadsheets/d/sheet-id/edit",
            "GOOGLE_ARCHIVE_ROOT_FOLDER_ID": "https://drive.google.com/drive/folders/folder-id",
        }
    )

    assert settings.client_secret_file == Path(r"C:\secret.json")
    assert settings.token_file == Path("token.json")
    assert settings.archive_spreadsheet_id == "sheet-id"
    assert settings.archive_root_folder_id == "folder-id"
    assert settings.archive_sheet_name == "Архив счетов"

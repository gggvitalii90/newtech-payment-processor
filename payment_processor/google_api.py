from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from .env import APP_DIR


GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]
GOOGLE_OAUTH_PORT = 0
DEFAULT_GOOGLE_TOKEN_PATH = APP_DIR / ".google_token.json"


@dataclass(frozen=True)
class GoogleSettings:
    client_secret_file: Path
    token_file: Path = DEFAULT_GOOGLE_TOKEN_PATH
    archive_spreadsheet_id: str = ""
    archive_sheet_name: str = "Архив счетов"
    archive_root_folder_id: str = ""


class GoogleConfigError(RuntimeError):
    pass


def load_google_settings(env: dict[str, str]) -> GoogleSettings:
    client_secret = env.get("GOOGLE_CLIENT_SECRET_FILE", "").strip()
    if not client_secret:
        raise GoogleConfigError("Не найден GOOGLE_CLIENT_SECRET_FILE в .env")
    token_file = Path(env.get("GOOGLE_TOKEN_FILE", str(DEFAULT_GOOGLE_TOKEN_PATH)).strip())
    archive_spreadsheet = env.get("GOOGLE_ARCHIVE_SPREADSHEET_ID", "").strip()
    archive_sheet_name = env.get("GOOGLE_ARCHIVE_SHEET_NAME", "Архив счетов").strip() or "Архив счетов"
    archive_root_folder = env.get("GOOGLE_ARCHIVE_ROOT_FOLDER_ID", "").strip()
    return GoogleSettings(
        client_secret_file=Path(client_secret),
        token_file=token_file,
        archive_spreadsheet_id=extract_google_id(archive_spreadsheet),
        archive_sheet_name=archive_sheet_name,
        archive_root_folder_id=extract_google_id(archive_root_folder),
    )


def get_credentials(settings: GoogleSettings, scopes: list[str] | None = None) -> Credentials:
    scopes = scopes or GOOGLE_SCOPES
    credentials = _load_existing_credentials(settings.token_file, scopes)
    if credentials and credentials.valid:
        return credentials
    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            _save_credentials(settings.token_file, credentials)
            return credentials
        except RefreshError:
            credentials = None
    if not settings.client_secret_file.exists():
        raise GoogleConfigError(f"Файл OAuth client secret не найден: {settings.client_secret_file}")
    flow = InstalledAppFlow.from_client_secrets_file(str(settings.client_secret_file), scopes)
    credentials = flow.run_local_server(port=GOOGLE_OAUTH_PORT)
    _save_credentials(settings.token_file, credentials)
    return credentials


def build_drive_service(credentials: Credentials):
    return build("drive", "v3", credentials=credentials)


def build_sheets_service(credentials: Credentials):
    return build("sheets", "v4", credentials=credentials)


def read_drive_folder_name(drive_service, folder_id: str) -> str:
    metadata = drive_service.files().get(fileId=folder_id, fields="id,name,mimeType", supportsAllDrives=True).execute()
    return str(metadata.get("name", ""))


def read_spreadsheet_title(sheets_service, spreadsheet_id: str) -> str:
    metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="properties(title)").execute()
    return str(metadata.get("properties", {}).get("title", ""))


def list_child_folders(drive_service, parent_id: str) -> list[dict[str, Any]]:
    query = (
        f"'{parent_id}' in parents and "
        "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    response = drive_service.files().list(
        q=query,
        fields="files(id,name,webViewLink)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        pageSize=1000,
    ).execute()
    return response.get("files", [])


def list_child_files(drive_service, parent_id: str) -> list[dict[str, Any]]:
    query = (
        f"'{parent_id}' in parents and "
        "mimeType != 'application/vnd.google-apps.folder' and trashed = false"
    )
    response = drive_service.files().list(
        q=query,
        fields="files(id,name,webViewLink,md5Checksum)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        pageSize=1000,
    ).execute()
    return response.get("files", [])


def find_child_folder_id(drive_service, parent_id: str, folder_name: str) -> str:
    for folder in list_child_folders(drive_service, parent_id):
        if str(folder.get("name", "")).strip().lower() == folder_name.strip().lower():
            return str(folder.get("id", ""))
    return ""


def find_child_file_link(drive_service, parent_id: str, file_name: str, md5_checksum: str = "") -> str:
    for file in list_child_files(drive_service, parent_id):
        same_name = str(file.get("name", "")).strip().lower() == file_name.strip().lower()
        same_content = not md5_checksum or str(file.get("md5Checksum", "")).lower() == md5_checksum.lower()
        if same_name and same_content:
            return str(file.get("webViewLink", ""))
    return ""


def create_child_folder(drive_service, parent_id: str, folder_name: str) -> str:
    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    created = drive_service.files().create(
        body=metadata,
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return str(created.get("id", ""))


def ensure_child_folder_id(drive_service, parent_id: str, folder_name: str) -> str:
    existing = find_child_folder_id(drive_service, parent_id, folder_name)
    if existing:
        return existing
    return create_child_folder(drive_service, parent_id, folder_name)


def upload_file_to_folder(drive_service, file_path: Path, folder_id: str, file_name: str = "") -> str:
    drive_file_name = file_name.strip() or file_path.name
    digest = hashlib.md5(file_path.read_bytes()).hexdigest()
    existing_link = find_child_file_link(drive_service, folder_id, drive_file_name, digest)
    if existing_link:
        return existing_link
    media = MediaFileUpload(str(file_path), resumable=False)
    metadata = {"name": drive_file_name, "parents": [folder_id]}
    created = drive_service.files().create(
        body=metadata,
        media_body=media,
        fields="id,webViewLink",
        supportsAllDrives=True,
    ).execute()
    return str(created.get("webViewLink", ""))


def download_drive_file(drive_service, file_id: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = drive_service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with destination.open("wb") as handle:
        downloader = MediaIoBaseDownload(handle, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return destination


def extract_google_id(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    patterns = [
        r"/spreadsheets/d/([A-Za-z0-9_-]+)",
        r"/document/d/([A-Za-z0-9_-]+)",
        r"/file/d/([A-Za-z0-9_-]+)",
        r"/drive/folders/([A-Za-z0-9_-]+)",
        r"[?&]id=([A-Za-z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return match.group(1)
    return value


def _load_existing_credentials(token_file: Path, scopes: list[str]) -> Credentials | None:
    if not token_file.exists():
        return None
    return Credentials.from_authorized_user_file(str(token_file), scopes)


def _save_credentials(token_file: Path, credentials: Credentials) -> None:
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json(), encoding="utf-8")

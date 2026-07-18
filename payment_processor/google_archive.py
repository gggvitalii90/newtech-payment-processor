from __future__ import annotations

import hashlib
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from googleapiclient.errors import HttpError

from .dictionaries import DEFAULT_UNRESOLVED_STATUS, normalize_key
from .google_api import (
    create_child_folder,
    find_child_folder_id,
    list_child_files,
    list_child_folders,
    upload_file_to_folder,
)
from .invoice_archive import INVOICE_ARCHIVE_COLUMNS, InvoiceArchiveRecord


PAYMENT_STATUSES = ["Новый", "Оплачен", "Подтвержден"]
ANALYSIS_STATUSES = ["ОК", "Нужно разобрать", "Дубль", "Нет файла", "Ошибка загрузки"]
MONTH_NAMES = {
    1: ("РЎРЏР Р…Р Р†Р В°РЎР‚РЎРЉ", "РЎРЏР Р…Р Р†Р В°РЎР‚РЎРЏ", "РЎРЏР Р…Р Р†"),
    2: ("РЎвЂћР ВµР Р†РЎР‚Р В°Р В»РЎРЉ", "РЎвЂћР ВµР Р†РЎР‚Р В°Р В»РЎРЏ", "РЎвЂћР ВµР Р†"),
    3: ("Р СР В°РЎР‚РЎвЂљ", "Р СР В°РЎР‚РЎвЂљР В°", "Р СР В°РЎР‚"),
    4: ("Р В°Р С—РЎР‚Р ВµР В»РЎРЉ", "Р В°Р С—РЎР‚Р ВµР В»РЎРЏ", "Р В°Р С—РЎР‚"),
    5: ("Р СР В°Р в„–", "Р СР В°РЎРЏ"),
    6: ("Р С‘РЎР‹Р Р…РЎРЉ", "Р С‘РЎР‹Р Р…РЎРЏ", "Р С‘РЎР‹Р Р…"),
    7: ("Р С‘РЎР‹Р В»РЎРЉ", "Р С‘РЎР‹Р В»РЎРЏ", "Р С‘РЎР‹Р В»"),
    8: ("Р В°Р Р†Р С–РЎС“РЎРѓРЎвЂљ", "Р В°Р Р†Р С–РЎС“РЎРѓРЎвЂљР В°", "Р В°Р Р†Р С–"),
    9: ("РЎРѓР ВµР Р…РЎвЂљРЎРЏР В±РЎР‚РЎРЉ", "РЎРѓР ВµР Р…РЎвЂљРЎРЏР В±РЎР‚РЎРЏ", "РЎРѓР ВµР Р…"),
    10: ("Р С•Р С”РЎвЂљРЎРЏР В±РЎР‚РЎРЉ", "Р С•Р С”РЎвЂљРЎРЏР В±РЎР‚РЎРЏ", "Р С•Р С”РЎвЂљ"),
    11: ("Р Р…Р С•РЎРЏР В±РЎР‚РЎРЉ", "Р Р…Р С•РЎРЏР В±РЎР‚РЎРЏ", "Р Р…Р С•РЎРЏ"),
    12: ("Р Т‘Р ВµР С”Р В°Р В±РЎР‚РЎРЉ", "Р Т‘Р ВµР С”Р В°Р В±РЎР‚РЎРЏ", "Р Т‘Р ВµР С”"),
}


def setup_archive_sheet(sheets_service, spreadsheet_id: str, sheet_name: str) -> None:
    metadata = _execute_google_request(sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id))
    sheet = _find_sheet(metadata, sheet_name)
    requests: list[dict[str, Any]] = []
    if sheet is None:
        requests.append({"addSheet": {"properties": {"title": sheet_name}}})
        _execute_google_request(sheets_service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}))
        metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheet = _find_sheet(metadata, sheet_name)
        requests = []
    if sheet is None:
        raise RuntimeError(f"Р СњР Вµ РЎС“Р Т‘Р В°Р В»Р С•РЎРѓРЎРЉ РЎРѓР С•Р В·Р Т‘Р В°РЎвЂљРЎРЉ Р В»Р С‘РЎРѓРЎвЂљ {sheet_name}")
    sheet_id = sheet["properties"]["sheetId"]
    headers = _ensure_headers(sheets_service, spreadsheet_id, sheet_name)
    requests.extend(
        [
            {"updateSheetProperties": {"properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}}, "fields": "gridProperties.frozenRowCount"}},
            {"setBasicFilter": {"filter": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": len(headers)}}}},
            _validation_request(sheet_id, headers, "Статус оплаты", PAYMENT_STATUSES),
            _validation_request(sheet_id, headers, "Статус разбора", ANALYSIS_STATUSES),
            *_archive_type_format_requests(sheet_id, headers),
        ]
    )
    _execute_google_request(sheets_service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}))


def read_archive_records(
    sheets_service,
    spreadsheet_id: str,
    sheet_name: str,
) -> list[InvoiceArchiveRecord]:
    headers = _read_headers(sheets_service, spreadsheet_id, sheet_name)
    if not headers:
        return []
    rows = _read_existing_archive_rows(sheets_service, spreadsheet_id, sheet_name, len(headers))
    records: list[InvoiceArchiveRecord] = []
    for row in rows:
        values = {header: str(row[index]).strip() for index, header in enumerate(headers) if index < len(row)}
        record_values = [values.get(column, "") for column in INVOICE_ARCHIVE_COLUMNS]
        if any(record_values):
            records.append(InvoiceArchiveRecord(*record_values))
    return records

def append_archive_records(sheets_service, spreadsheet_id: str, sheet_name: str, records: list[InvoiceArchiveRecord]) -> None:
    if not records:
        return
    headers = _ensure_headers(sheets_service, spreadsheet_id, sheet_name)
    existing_rows = _read_existing_archive_rows(sheets_service, spreadsheet_id, sheet_name, len(headers))
    existing_by_key: dict[tuple[str, str, str, str, str], list[int]] = {}
    incoming_scope = _records_scope(records)
    for index, row in enumerate(existing_rows):
        for key in _row_keys(row, headers):
            existing_by_key.setdefault(key, []).append(index + 2)
    rows_to_append: list[list[str]] = []
    duplicate_rows_to_delete: set[int] = set()
    duplicate_rows_to_delete.update(
        index + 2
        for index, row in enumerate(existing_rows)
        if _is_stale_empty_file_row(row, headers, incoming_scope)
    )
    for record in records:
        row = _record_row_for_headers(record, headers)
        row_numbers = []
        for key in _row_keys(row, headers):
            row_numbers = existing_by_key.get(key, [])
            if row_numbers:
                break
        if row_numbers:
            row_number = row_numbers[0]
            duplicate_rows_to_delete.update(row_numbers[1:])
            _execute_google_request(sheets_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"'{sheet_name}'!A{row_number}:{_column_letter(len(headers))}{row_number}",
                valueInputOption="USER_ENTERED",
                body={"values": [row]},
            ))
            continue
        rows_to_append.append(row)
    if duplicate_rows_to_delete:
        _delete_sheet_rows(sheets_service, spreadsheet_id, sheet_name, sorted(duplicate_rows_to_delete, reverse=True))
    if rows_to_append:
        body = {"values": rows_to_append}
        _execute_google_request(sheets_service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body,
        ))
    cleanup_archive_duplicates(sheets_service, spreadsheet_id, sheet_name, incoming_scope)


def cleanup_archive_duplicates(
    sheets_service,
    spreadsheet_id: str,
    sheet_name: str,
    scope: set[tuple[str, str, str]] | None = None,
) -> None:
    headers = _ensure_headers(sheets_service, spreadsheet_id, sheet_name)
    rows = _read_existing_archive_rows(sheets_service, spreadsheet_id, sheet_name, len(headers))
    grouped: dict[tuple[str, ...], list[tuple[int, list[str]]]] = {}
    for index, row in enumerate(rows):
        if scope and not _row_in_scope(row, headers, scope):
            continue
        key = _cleanup_row_key(row, headers)
        if not key:
            continue
        grouped.setdefault(key, []).append((index + 2, row))
    rows_to_delete: list[int] = []
    for group in grouped.values():
        if len(group) < 2:
            continue
        keep_row_number = max(group, key=lambda item: _sheet_row_quality(item[1], headers))[0]
        rows_to_delete.extend(row_number for row_number, _row in group if row_number != keep_row_number)

    exact_grouped: dict[tuple[str, ...], list[tuple[int, list[str]]]] = {}
    for index, row in enumerate(rows):
        if index + 2 in rows_to_delete:
            continue
        key = _exact_duplicate_row_key(row, headers)
        if key:
            exact_grouped.setdefault(key, []).append((index + 2, row))
    for group in exact_grouped.values():
        if len(group) < 2:
            continue
        keep_row_number = max(group, key=lambda item: _sheet_row_quality(item[1], headers))[0]
        rows_to_delete.extend(row_number for row_number, _row in group if row_number != keep_row_number)

    if rows_to_delete:
        _delete_sheet_rows(sheets_service, spreadsheet_id, sheet_name, sorted(set(rows_to_delete), reverse=True))


def _execute_google_request(request, retries: int = 5):
    for attempt in range(retries):
        try:
            return request.execute()
        except HttpError as exc:
            if exc.resp.status not in {429, 500, 502, 503, 504} or attempt == retries - 1:
                raise
            time.sleep(65 if exc.resp.status == 429 else 2 ** attempt)
    return request.execute()


def _delete_sheet_rows(sheets_service, spreadsheet_id: str, sheet_name: str, row_numbers: list[int]) -> None:
    metadata = _execute_google_request(sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id))
    sheet = _find_sheet(metadata, sheet_name)
    if sheet is None:
        return
    sheet_id = sheet["properties"]["sheetId"]
    requests = [
        {
            "deleteDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": row_number - 1,
                    "endIndex": row_number,
                }
            }
        }
        for row_number in row_numbers
    ]
    _execute_google_request(sheets_service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}))


def _descriptive_image_file_name(record: InvoiceArchiveRecord) -> str:
    if not re.fullmatch(r"i(?:_\d+)?", record.file_name.strip(), flags=re.IGNORECASE):
        return ""

    invoice_date = record.invoice_date.strip()
    try:
        invoice_date = datetime.strptime(invoice_date, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        pass

    counterparty = re.sub(r'[Р’В«Р’В»"РІР‚СљРІР‚Сњ]', "", record.counterparty)
    counterparty = re.sub(r"\s+", " ", counterparty).strip()
    parts = ["Р РЋРЎвЂЎР ВµРЎвЂљ"]
    if record.invoice_number.strip():
        parts.append(f"РІвЂћвЂ“{record.invoice_number.strip()}")
    if invoice_date:
        parts.extend(["Р С•РЎвЂљ", invoice_date])
    if counterparty:
        parts.append(counterparty)
    if len(parts) == 1:
        fallback = re.sub(r"[^A-Za-z0-9]+", "", record.max_message_id)[-12:] or "Р С‘Р В· MAX"
        parts.append(fallback)

    stem = " ".join(parts)
    stem = re.sub(r'[<>:"/\\|?*]', "_", stem).strip().rstrip(". ")
    return f"{stem[:180].rstrip()}.webp"

def prepare_records_for_google_drive(
    drive_service,
    records: list[InvoiceArchiveRecord],
    local_files_by_name: dict[str, Path],
    root_folder_id: str,
    dictionaries: dict[str, Any],
    existing_records: list[InvoiceArchiveRecord] | None = None,
) -> None:
    existing_links = {
        item.max_file_id: item.google_drive_link
        for item in (existing_records or [])
        if item.max_file_id and item.google_drive_link
    }
    for record in records:
        if not record.file_name:
            if not record.analysis_status:
                record.analysis_status = "Р С›Р С™"
            continue
        source_file_name = record.file_name
        file_path = local_files_by_name.get(source_file_name)
        if not file_path or not file_path.exists():
            record.analysis_status = "Р СњР ВµРЎвЂљ РЎвЂћР В°Р в„–Р В»Р В°"
            continue
        descriptive_name = _descriptive_image_file_name(record)
        if descriptive_name:
            record.file_name = descriptive_name
            record.file_type = "webp"
        if not record.google_drive_link and record.max_file_id:
            record.google_drive_link = existing_links.get(record.max_file_id, "")
        if record.google_drive_link:
            if not record.analysis_status:
                record.analysis_status = "\u041e\u041a"
            continue
        existing_link = find_existing_file_link_by_name_and_md5(drive_service, file_path, record.file_name, root_folder_id)
        if existing_link:
            record.google_drive_link = existing_link
            if not record.analysis_status:
                record.analysis_status = "\u0414\u0443\u0431\u043b\u044c"
            continue
        folder_id = resolve_drive_archive_folder(drive_service, root_folder_id, record, dictionaries)
        if not folder_id:
            record.analysis_status = dictionaries.get("unresolved_status", DEFAULT_UNRESOLVED_STATUS)
            folder_id = find_child_folder_id(drive_service, root_folder_id, "__")
            if not folder_id:
                continue
        try:
            record.google_drive_link = upload_file_to_folder(drive_service, file_path, folder_id, file_name=record.file_name)
            _touch_drive_folder(drive_service, folder_id)
            if not record.analysis_status:
                record.analysis_status = "Р С›Р С™"
        except Exception:
            record.analysis_status = "Р С›РЎв‚¬Р С‘Р В±Р С”Р В° Р В·Р В°Р С–РЎР‚РЎС“Р В·Р С”Р С‘"


def _touch_drive_folder(drive_service, folder_id: str) -> None:
    files_resource = drive_service.files()
    if not hasattr(files_resource, "update"):
        return
    modified_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    files_resource.update(
        fileId=folder_id,
        body={"modifiedTime": modified_time},
        fields="id,modifiedTime",
    ).execute()




def resolve_drive_object_folder_id(
    drive_service,
    root_folder_id: str,
    record: InvoiceArchiveRecord,
    dictionaries: dict[str, Any],
) -> str:
    object_folder_name = _mapped_value(dictionaries.get("drive_object_folders", {}), record.object_name) or record.object_name
    if not object_folder_name:
        return ""
    return find_child_folder_id(drive_service, root_folder_id, object_folder_name)


def find_existing_file_link_in_folder_tree(drive_service, folder_id: str, file_path: Path) -> str:
    digest = _file_md5(file_path)
    if not digest:
        return ""
    stack = [folder_id]
    while stack:
        current = stack.pop()
        for item in list_child_files(drive_service, current):
            if str(item.get("md5Checksum", "")).lower() == digest:
                return str(item.get("webViewLink", ""))
        stack.extend(str(folder.get("id", "")) for folder in list_child_folders(drive_service, current) if folder.get("id"))
    return ""



def find_existing_file_link_by_name_and_md5(drive_service, file_path: Path, file_name: str = "", root_folder_id: str = "") -> str:
    if root_folder_id and hasattr(drive_service, "children"):
        return find_existing_file_link_in_folder_tree(drive_service, root_folder_id, file_path)
    digest = _file_md5(file_path)
    if not digest:
        return ""
    drive_file_name = (file_name or file_path.name).replace("'", "\\'")
    response = drive_service.files().list(
        q=f"name = '{drive_file_name}' and trashed = false",
        fields="files(id,name,webViewLink,md5Checksum)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        pageSize=100,
    ).execute()
    for item in response.get("files", []):
        if str(item.get("md5Checksum", "")).lower() == digest:
            return str(item.get("webViewLink", ""))
    return ""

def _file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_drive_archive_folder(
    drive_service,
    root_folder_id: str,
    record: InvoiceArchiveRecord,
    dictionaries: dict[str, Any],
    today: date | None = None,
) -> str:
    object_folder_name = _mapped_value(dictionaries.get("drive_object_folders", {}), record.object_name) or record.object_name
    if not object_folder_name:
        return ""
    object_folder_id = find_child_folder_id(drive_service, root_folder_id, object_folder_name)
    if not object_folder_id and _known_drive_object(record.object_name, dictionaries):
        object_folder_id = create_child_folder(drive_service, root_folder_id, object_folder_name)
    if not object_folder_id:
        return ""
    today = today or date.today()
    record_date = _record_date(record)
    if record_date and (record_date.year, record_date.month) == (today.year, today.month):
        return object_folder_id
    flat_objects = dictionaries.get("drive_flat_objects", [])
    if any(normalize_key(record.object_name) == normalize_key(str(value)) for value in flat_objects):
        return object_folder_id
    year = _record_year(record)
    if not year:
        return ""
    year_folder_id = find_child_folder_id(drive_service, object_folder_id, year)
    if not year_folder_id:
        return ""
    return ensure_month_folder_id(drive_service, year_folder_id, record, dictionaries)



def _known_drive_object(object_name: str, dictionaries: dict[str, Any]) -> bool:
    key = normalize_key(object_name)
    objects = dictionaries.get("objects", {})
    if isinstance(objects, dict):
        return any(normalize_key(str(value)) == key for value in objects)
    if isinstance(objects, list):
        return any(normalize_key(str(value)) == key for value in objects)
    return False

def find_month_folder_id(
    drive_service,
    year_folder_id: str,
    record: InvoiceArchiveRecord,
    dictionaries: dict[str, Any],
) -> str:
    year_month = _record_year_month(record)
    if year_month:
        configured = _mapped_value(dictionaries.get("drive_month_folders", {}), year_month)
        if configured:
            folder_id = find_child_folder_id(drive_service, year_folder_id, configured)
            if folder_id:
                return folder_id
    month = _record_month(record)
    if not month:
        return ""
    for folder in list_child_folders(drive_service, year_folder_id):
        if _folder_matches_month(str(folder.get("name", "")), month):
            return str(folder.get("id", ""))
    return ""


def ensure_month_folder_id(
    drive_service,
    year_folder_id: str,
    record: InvoiceArchiveRecord,
    dictionaries: dict[str, Any],
) -> str:
    return find_month_folder_id(drive_service, year_folder_id, record, dictionaries)


def _write_headers(sheets_service, spreadsheet_id: str, sheet_name: str) -> None:
    _execute_google_request(sheets_service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A1:{_column_letter(len(INVOICE_ARCHIVE_COLUMNS))}1",
        valueInputOption="USER_ENTERED",
        body={"values": [INVOICE_ARCHIVE_COLUMNS]},
    ))


def _ensure_headers(sheets_service, spreadsheet_id: str, sheet_name: str) -> list[str]:
    headers = _read_headers(sheets_service, spreadsheet_id, sheet_name)
    if not headers:
        _write_headers(sheets_service, spreadsheet_id, sheet_name)
        return list(INVOICE_ARCHIVE_COLUMNS)
    missing = [column for column in INVOICE_ARCHIVE_COLUMNS if column not in headers]
    if missing:
        headers = headers + missing
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'!A1:{_column_letter(len(headers))}1",
            valueInputOption="USER_ENTERED",
            body={"values": [headers]},
        ).execute()
    return headers


def _read_headers(sheets_service, spreadsheet_id: str, sheet_name: str) -> list[str]:
    response = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A1:AZ1",
    )
    response = _execute_google_request(response)
    rows = response.get("values", [])
    if not rows:
        return []
    return [str(value).strip() for value in rows[0]]


def _read_existing_archive_rows(sheets_service, spreadsheet_id: str, sheet_name: str, column_count: int) -> list[list[str]]:
    response = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A2:{_column_letter(column_count)}",
    )
    response = _execute_google_request(response)
    return response.get("values", [])


def _record_row_for_headers(record: InvoiceArchiveRecord, headers: list[str]) -> list[str]:
    values = dict(zip(INVOICE_ARCHIVE_COLUMNS, record.as_row(), strict=False))
    return [values.get(header, "") for header in headers]


def _row_key(row: list[str], headers: list[str]) -> tuple[str, str, str, str, str] | None:
    def value(column_name: str) -> str:
        if column_name not in headers:
            return ""
        index = headers.index(column_name)
        return str(row[index]).strip() if len(row) > index else ""

    message_id = value("MAX message_id")
    file_id = value("MAX file_id")
    file_name = value("Р ВР СРЎРЏ РЎвЂћР В°Р в„–Р В»Р В°")
    purpose = value("Р СњР В°Р В·Р Р…Р В°РЎвЂЎР ВµР Р…Р С‘Р Вµ")
    amount = value("Р РЋРЎС“Р СР СР В°")
    chat = value("Р В§Р В°РЎвЂљ")
    max_date = value("Р вЂќР В°РЎвЂљР В° MAX")
    counterparty = value("Р С™Р С•Р Р…РЎвЂљРЎР‚Р В°Р С–Р ВµР Р…РЎвЂљ")
    invoice_number = value("Р СњР С•Р СР ВµРЎР‚ РЎРѓРЎвЂЎР ВµРЎвЂљР В°")
    invoice_date = value("Р вЂќР В°РЎвЂљР В° РЎРѓРЎвЂЎР ВµРЎвЂљР В°")
    if not message_id and not file_id and not file_name:
        return None
    if file_name and invoice_number:
        return (chat, _normalize_sheet_key(invoice_number), invoice_date, "", "")
    if file_id:
        return (message_id, file_id, file_name, "", "")
    if file_name:
        return (message_id, "", _normalize_sheet_key(file_name), "", "")
    return (chat, max_date, _normalize_sheet_key(counterparty), _normalize_sheet_key(purpose), _normalize_amount(amount))


def _row_keys(row: list[str], headers: list[str]) -> list[tuple[str, ...]]:
    def value(column_name: str) -> str:
        if column_name not in headers:
            return ""
        index = headers.index(column_name)
        return str(row[index]).strip() if len(row) > index else ""

    keys: list[tuple[str, ...]] = []
    file_id = value("MAX file_id")
    if file_id:
        keys.append(("max_file", value("MAX message_id"), file_id, _normalize_sheet_key(value("\u0418\u043c\u044f \u0444\u0430\u0439\u043b\u0430"))))
    fallback = _row_key(row, headers)
    if fallback and fallback not in keys:
        keys.append(fallback)
    return keys

def _records_scope(records: list[InvoiceArchiveRecord]) -> set[tuple[str, str, str]]:
    scope = set()
    for record in records:
        scope.add((record.mode, record.chat, (record.max_date or "")[:10]))
    return scope


def _is_stale_empty_file_row(row: list[str], headers: list[str], incoming_scope: set[tuple[str, str, str]]) -> bool:
    def value(column_name: str) -> str:
        if column_name not in headers:
            return ""
        index = headers.index(column_name)
        return str(row[index]).strip() if len(row) > index else ""

    if not value("Р ВР СРЎРЏ РЎвЂћР В°Р в„–Р В»Р В°"):
        return False
    scope_key = (value("Р СџР С•РЎвЂљР С•Р С”"), value("Р В§Р В°РЎвЂљ"), value("Р вЂќР В°РЎвЂљР В° MAX")[:10])
    if scope_key not in incoming_scope:
        return False
    business_fields = [
        "Р С™Р С•Р Р…РЎвЂљРЎР‚Р В°Р С–Р ВµР Р…РЎвЂљ",
        "Р СњР С•Р СР ВµРЎР‚ РЎРѓРЎвЂЎР ВµРЎвЂљР В°",
        "Р вЂќР В°РЎвЂљР В° РЎРѓРЎвЂЎР ВµРЎвЂљР В°",
        "Р С›Р В±РЎР‰Р ВµР С”РЎвЂљ",
        "Р СџРЎР‚Р С•Р ВµР С”РЎвЂљ",
        "Р РЋРЎвЂљР В°РЎвЂљРЎРЉРЎРЏ Р В±РЎР‹Р Т‘Р В¶Р ВµРЎвЂљР В°",
        "Р СњР В°Р В·Р Р…Р В°РЎвЂЎР ВµР Р…Р С‘Р Вµ",
        "Р РЋРЎС“Р СР СР В°",
    ]
    return not any(value(column) for column in business_fields)


def _row_in_scope(row: list[str], headers: list[str], scope: set[tuple[str, str, str]]) -> bool:
    def value(column_name: str) -> str:
        if column_name not in headers:
            return ""
        index = headers.index(column_name)
        return str(row[index]).strip() if len(row) > index else ""

    return (value("Р СџР С•РЎвЂљР С•Р С”"), value("Р В§Р В°РЎвЂљ"), value("Р вЂќР В°РЎвЂљР В° MAX")[:10]) in scope


def _cleanup_row_key(row: list[str], headers: list[str]) -> tuple[str, ...] | None:
    def value(column_name: str) -> str:
        if column_name not in headers:
            return ""
        index = headers.index(column_name)
        return str(row[index]).strip() if len(row) > index else ""

    chat = value("Р В§Р В°РЎвЂљ")
    file_name = value("Р ВР СРЎРЏ РЎвЂћР В°Р в„–Р В»Р В°")
    max_date = value("Р вЂќР В°РЎвЂљР В° MAX")
    counterparty = _normalize_sheet_key(value("Р С™Р С•Р Р…РЎвЂљРЎР‚Р В°Р С–Р ВµР Р…РЎвЂљ"))
    invoice_number = _normalize_sheet_key(value("Р СњР С•Р СР ВµРЎР‚ РЎРѓРЎвЂЎР ВµРЎвЂљР В°"))
    invoice_date = value("Р вЂќР В°РЎвЂљР В° РЎРѓРЎвЂЎР ВµРЎвЂљР В°")
    project = _normalize_sheet_key(value("Р СџРЎР‚Р С•Р ВµР С”РЎвЂљ"))
    budget_item = _normalize_sheet_key(value("Р РЋРЎвЂљР В°РЎвЂљРЎРЉРЎРЏ Р В±РЎР‹Р Т‘Р В¶Р ВµРЎвЂљР В°"))
    purpose = _normalize_sheet_key(value("Р СњР В°Р В·Р Р…Р В°РЎвЂЎР ВµР Р…Р С‘Р Вµ"))
    if file_name and invoice_number:
        return ("invoice", chat, invoice_number, invoice_date)
    if not file_name and (counterparty or invoice_number or project or budget_item or purpose):
        return ("message", chat, max_date, counterparty, invoice_number, project, budget_item, purpose)
    return None


def _exact_duplicate_row_key(row: list[str], headers: list[str]) -> tuple[str, ...] | None:
    width = max(len(headers), len(row), 23)
    padded = [str(row[index]).strip() if index < len(row) else "" for index in range(width)]
    max_file_index = headers.index("MAX file_id") if "MAX file_id" in headers else 21
    max_message_index = headers.index("MAX message_id") if "MAX message_id" in headers else 20
    link_index = 17
    if len(headers) > 17:
        for index, header in enumerate(headers):
            if "Drive" in header or "РЎРѓРЎРѓРЎвЂ№Р В»Р С”Р В°" in header.lower():
                link_index = index
                break
    if not padded[max_message_index] or not padded[link_index]:
        return None
    return tuple(value for index, value in enumerate(padded) if index != max_file_index)

def _sheet_row_quality(row: list[str], headers: list[str]) -> int:
    def value(column_name: str) -> str:
        if column_name not in headers:
            return ""
        index = headers.index(column_name)
        return str(row[index]).strip() if len(row) > index else ""

    fields = [
        "Р вЂќР В°РЎвЂљР В° РЎРѓРЎвЂЎР ВµРЎвЂљР В°",
        "Р СћР С‘Р С— Р С•Р С—Р ВµРЎР‚Р В°РЎвЂ Р С‘Р С‘",
        "Р СћР С‘Р С— Р С•Р С—Р В»Р В°РЎвЂљРЎвЂ№",
        "Р вЂР В°Р Р…Р С”",
        "Р С™Р С•Р Р…РЎвЂљРЎР‚Р В°Р С–Р ВµР Р…РЎвЂљ",
        "Р СњР С•Р СР ВµРЎР‚ РЎРѓРЎвЂЎР ВµРЎвЂљР В°",
        "Р С›Р В±РЎР‰Р ВµР С”РЎвЂљ",
        "Р СџРЎР‚Р С•Р ВµР С”РЎвЂљ",
        "Р РЋРЎвЂљР В°РЎвЂљРЎРЉРЎРЏ Р В±РЎР‹Р Т‘Р В¶Р ВµРЎвЂљР В°",
        "Р С›РЎвЂљР Р†Р ВµРЎвЂљРЎРѓРЎвЂљР Р†Р ВµР Р…Р Р…РЎвЂ№Р в„–",
        "Р СњР В°Р В·Р Р…Р В°РЎвЂЎР ВµР Р…Р С‘Р Вµ",
        "Google Drive РЎРѓРЎРѓРЎвЂ№Р В»Р С”Р В°",
        "Р РЋРЎС“Р СР СР В°",
        "Р РЋРЎвЂљР В°РЎвЂљРЎС“РЎРѓ Р С•Р С—Р В»Р В°РЎвЂљРЎвЂ№",
        "Р РЋРЎвЂљР В°РЎвЂљРЎС“РЎРѓ РЎР‚Р В°Р В·Р В±Р С•РЎР‚Р В°",
    ]
    return sum(1 for field in fields if value(field))


def _normalize_sheet_key(value: str) -> str:
    value = (value or "").lower().replace("РЎвЂ", "Р Вµ")
    value = re.sub(r"[\"'Р’В«Р’В».,;:()РІвЂћвЂ“#]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _normalize_amount(value: str) -> str:
    return re.sub(r"\D+", "", value or "")


def _find_sheet(metadata: dict[str, Any], sheet_name: str) -> dict[str, Any] | None:
    for sheet in metadata.get("sheets", []):
        if sheet.get("properties", {}).get("title") == sheet_name:
            return sheet
    return None



def _archive_type_format_requests(sheet_id: int, headers: list[str]) -> list[dict[str, Any]]:
    formats = {
        "Р вЂќР В°РЎвЂљР В° MAX": {"type": "DATE_TIME", "pattern": "dd.mm.yyyy hh:mm:ss"},
        "Р вЂќР В°РЎвЂљР В° РЎРѓРЎвЂЎР ВµРЎвЂљР В°": {"type": "DATE", "pattern": "dd.mm.yyyy"},
        "Р РЋРЎС“Р СР СР В°": {"type": "NUMBER", "pattern": "#,##0.00"},
    }
    return [
        _number_format_request(sheet_id, headers.index(column_name), number_format)
        for column_name, number_format in formats.items()
        if column_name in headers
    ]


def _number_format_request(sheet_id: int, column_index: int, number_format: dict[str, str]) -> dict[str, Any]:
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "startColumnIndex": column_index,
                "endColumnIndex": column_index + 1,
            },
            "cell": {"userEnteredFormat": {"numberFormat": number_format}},
            "fields": "userEnteredFormat.numberFormat",
        }
    }

def _validation_request(sheet_id: int, headers: list[str], column_name: str, values: list[str]) -> dict[str, Any]:
    column_idx = headers.index(column_name)
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "endRowIndex": 5000,
                "startColumnIndex": column_idx,
                "endColumnIndex": column_idx + 1,
            },
            "rule": {
                "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": value} for value in values]},
                "strict": False,
                "showCustomUi": True,
            },
        }
    }


def _record_year(record: InvoiceArchiveRecord) -> str:
    parsed = _record_date(record)
    return str(parsed.year) if parsed else ""


def _record_year_month(record: InvoiceArchiveRecord) -> str:
    parsed = _record_date(record)
    return parsed.strftime("%Y-%m") if parsed else ""


def _record_month(record: InvoiceArchiveRecord) -> int:
    parsed = _record_date(record)
    return parsed.month if parsed else 0


def _record_date(record: InvoiceArchiveRecord) -> datetime | None:
    for value in [record.max_date, record.invoice_date]:
        for fmt in ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y", "%d.%m.%y"]:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def default_drive_month_folder_name(month: int) -> str:
    names = {
        1: "01 \u044f\u043d\u0432\u0430\u0440\u044c",
        2: "02 \u0444\u0435\u0432\u0440\u0430\u043b\u044c",
        3: "03 \u043c\u0430\u0440\u0442",
        4: "04 \u0430\u043f\u0440\u0435\u043b\u044c",
        5: "05 \u043c\u0430\u0439",
        6: "06 \u0438\u044e\u043d\u044c",
        7: "07 \u0438\u044e\u043b\u044c",
        8: "08 \u0430\u0432\u0433\u0443\u0441\u0442",
        9: "09 \u0441\u0435\u043d\u0442\u044f\u0431\u0440\u044c",
        10: "10 \u043e\u043a\u0442\u044f\u0431\u0440\u044c",
        11: "11 \u043d\u043e\u044f\u0431\u0440\u044c",
        12: "12 \u0434\u0435\u043a\u0430\u0431\u0440\u044c",
    }
    return names.get(month, f"{month:02d}")


def _folder_matches_month(folder_name: str, month: int) -> bool:
    key = normalize_key(folder_name)
    if re.search(rf"(^|\D){month:02d}(\D|$)", key):
        return True
    return any(name in key for name in MONTH_NAMES.get(month, ()))


def _mapped_value(mapping: dict[str, str], key: str) -> str:
    if not isinstance(mapping, dict):
        return ""
    if key in mapping:
        return mapping[key]
    normalized_key = normalize_key(key)
    for source, target in mapping.items():
        if normalize_key(source) == normalized_key:
            return str(target)
    return ""


def _column_letter(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


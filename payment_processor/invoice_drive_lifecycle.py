from __future__ import annotations

import re
from datetime import date
from typing import Any

from .dictionaries import normalize_key
from .google_api import create_child_folder, extract_google_id, find_child_folder_id, list_child_files
from .google_archive import default_drive_month_folder_name
from .invoice_archive import InvoiceArchiveRecord
from .models import PaymentRecord

FOLDER_MIME = "application/vnd.google-apps.folder"


def move_drive_file(drive_service, file_id: str, destination_folder_id: str) -> dict[str, Any]:
    metadata = drive_service.files().get(fileId=file_id, fields="id,parents", supportsAllDrives=True).execute()
    parents = [str(value) for value in metadata.get("parents", [])]
    if destination_folder_id in parents:
        return {"id": file_id, "parents": parents, "status": "already_in_destination"}
    result = drive_service.files().update(
        fileId=file_id,
        addParents=destination_folder_id,
        removeParents=",".join(parents),
        fields="id,parents",
        supportsAllDrives=True,
    ).execute()
    return {**result, "status": "moved"}


def resolve_payment_month_folder(
    drive_service,
    root_folder_id: str,
    object_name: str,
    payment_date: date,
    dictionaries: dict[str, Any],
    today: date | None = None,
) -> str:
    mapped = _mapped_value(dictionaries.get("drive_object_folders", {}), object_name) or object_name
    object_folder_id = find_child_folder_id(drive_service, root_folder_id, mapped)
    if not object_folder_id:
        return ""
    today = today or date.today()
    if (payment_date.year, payment_date.month) == (today.year, today.month):
        parent_id = object_folder_id
    else:
        year_name = str(payment_date.year)
        parent_id = find_child_folder_id(drive_service, object_folder_id, year_name)
        if not parent_id:
            parent_id = create_child_folder(drive_service, object_folder_id, year_name)
    month_name = default_drive_month_folder_name(payment_date.month)
    month_folder_id = find_child_folder_id(drive_service, parent_id, month_name)
    if not month_folder_id:
        month_folder_id = create_child_folder(drive_service, parent_id, month_name)
    return month_folder_id


def archive_paid_invoice(
    drive_service,
    root_folder_id: str,
    invoice: InvoiceArchiveRecord,
    payment: PaymentRecord,
    final_row_confirmed: bool,
    dictionaries: dict[str, Any],
    today: date | None = None,
) -> dict[str, str]:
    if invoice.payment_status != "\u041e\u043f\u043b\u0430\u0447\u0435\u043d":
        return {"status": "skipped_not_paid"}
    if not final_row_confirmed:
        return {"status": "skipped_final_not_confirmed"}
    if not invoice.google_drive_link or not payment.invoice_link:
        return {"status": "skipped_missing_link"}
    if extract_google_id(invoice.google_drive_link) != extract_google_id(payment.invoice_link):
        return {"status": "skipped_link_mismatch"}
    if _norm(invoice.invoice_number) != _norm(payment.invoice_number):
        return {"status": "skipped_invoice_mismatch"}
    try:
        paid_on = date.fromisoformat(payment.date[:10])
    except ValueError:
        return {"status": "skipped_invalid_payment_date"}
    folder_id = resolve_payment_month_folder(
        drive_service, root_folder_id, invoice.object_name, paid_on, dictionaries, today=today,
    )
    if not folder_id:
        return {"status": "skipped_missing_destination"}
    file_id = extract_google_id(invoice.google_drive_link)
    result = move_drive_file(drive_service, file_id, folder_id)
    return {"status": str(result.get("status", "moved")), "file_id": file_id, "folder_id": folder_id}


def migrate_legacy_review_folder(drive_service, root_folder_id: str) -> dict[str, int | bool]:
    legacy_id = find_child_folder_id(drive_service, root_folder_id, "\u041d\u0443\u0436\u043d\u043e \u0440\u0430\u0437\u043e\u0431\u0440\u0430\u0442\u044c")
    if not legacy_id:
        return {"moved": 0, "legacy_folder_trashed": False}
    unresolved_id = find_child_folder_id(drive_service, root_folder_id, "__")
    if not unresolved_id:
        unresolved_id = create_child_folder(drive_service, root_folder_id, "__")
    files = list_child_files(drive_service, legacy_id)
    for item in files:
        move_drive_file(drive_service, str(item["id"]), unresolved_id)
    try:
        drive_service.files().update(
            fileId=legacy_id,
            body={"trashed": True},
            fields="id,trashed",
            supportsAllDrives=True,
        ).execute()
        trashed = True
    except Exception:
        trashed = False
    return {"moved": len(files), "legacy_folder_trashed": trashed}


def _mapped_value(mapping: dict[str, str], value: str) -> str:
    key = normalize_key(value)
    for source, target in mapping.items():
        if normalize_key(str(source)) == key:
            return str(target)
    return ""


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").lower().replace("\u2116", "").replace("\u0451", "\u0435")).strip(".,")

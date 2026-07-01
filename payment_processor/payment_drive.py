from __future__ import annotations

import csv
import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from .google_api import ensure_child_folder_id, list_child_files, list_child_folders, upload_file_to_folder


DAY_FOLDER_RE = re.compile(r"^(\d{4})[.](\d{2})[.](\d{2})(?: ИС)?$")


def parse_day_folder_name(name: str) -> tuple[str, str] | None:
    match = DAY_FOLDER_RE.fullmatch(str(name).strip())
    if not match:
        return None
    year, month, day = match.groups()
    try:
        date(int(year), int(month), int(day))
    except ValueError:
        return None
    return year, month


def plan_folder_moves(children: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    moves: list[dict[str, str]] = []
    for child in children:
        parsed = parse_day_folder_name(str(child.get("name", "")))
        if not parsed:
            continue
        year, month = parsed
        moves.append({
            "id": str(child.get("id", "")),
            "name": str(child.get("name", "")),
            "year": year,
            "month": month,
        })
    return sorted(moves, key=lambda item: (item["year"], item["month"], item["name"], item["id"]))


def move_day_folder(drive_service, day_folder_id: str, root_id: str, month_folder_id: str) -> dict:
    return drive_service.files().update(
        fileId=day_folder_id,
        addParents=month_folder_id,
        removeParents=root_id,
        fields="id,parents",
        supportsAllDrives=True,
    ).execute()


def resolve_day_folder_id(
    drive_service,
    root_id: str,
    folder_date: date,
    mode: str = "ПСК",
    create: bool = True,
) -> str:
    year_name = f"{folder_date:%Y}"
    month_name = f"{folder_date:%m}"
    day_name = f"{folder_date:%Y.%m.%d}" + (" ИС" if mode.strip().upper() == "ИС" else "")
    if create:
        year_id = ensure_child_folder_id(drive_service, root_id, year_name)
        month_id = ensure_child_folder_id(drive_service, year_id, month_name)
        return ensure_child_folder_id(drive_service, month_id, day_name)
    year_id = _find_folder_id(list_child_folders(drive_service, root_id), year_name)
    if not year_id:
        return ""
    month_id = _find_folder_id(list_child_folders(drive_service, year_id), month_name)
    if not month_id:
        return ""
    return _find_folder_id(list_child_folders(drive_service, month_id), day_name)


def find_payment_file_link(
    drive_service,
    root_id: str,
    file_path: Path,
    payment_date: date,
    mode: str = "ПСК",
    folder_cache: dict[tuple[str, str], str] | None = None,
    file_cache: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    folder_cache = folder_cache if folder_cache is not None else {}
    file_cache = file_cache if file_cache is not None else {}
    cache_key = (payment_date.isoformat(), mode.strip().upper())
    if cache_key not in folder_cache:
        folder_cache[cache_key] = resolve_day_folder_id(
            drive_service, root_id, payment_date, mode, create=False,
        )
    day_folder_id = folder_cache[cache_key]
    if not day_folder_id:
        return ""
    if day_folder_id not in file_cache:
        file_cache[day_folder_id] = list_child_files(drive_service, day_folder_id)
    digest = _md5(file_path)
    for item in file_cache[day_folder_id]:
        if str(item.get("md5Checksum", "")).casefold() == digest:
            return str(item.get("webViewLink", ""))
    return ""


def ensure_payment_file(
    drive_service,
    root_id: str,
    file_path: Path,
    payment_date: date,
    mode: str = "ПСК",
    folder_cache: dict[tuple[str, str], str] | None = None,
    file_cache: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    folder_cache = folder_cache if folder_cache is not None else {}
    file_cache = file_cache if file_cache is not None else {}
    existing = find_payment_file_link(
        drive_service, root_id, file_path, payment_date, mode, folder_cache, file_cache,
    )
    if existing:
        return existing
    cache_key = (payment_date.isoformat(), mode.strip().upper())
    day_folder_id = resolve_day_folder_id(drive_service, root_id, payment_date, mode, create=True)
    folder_cache[cache_key] = day_folder_id
    link = upload_file_to_folder(drive_service, file_path, day_folder_id, file_name=file_path.name)
    file_cache.pop(day_folder_id, None)
    return link

def reorganize_day_folders(drive_service, root_id: str, apply: bool = False) -> list[dict[str, str]]:
    moves = plan_folder_moves(list_child_folders(drive_service, root_id))
    if not apply:
        return moves
    year_ids: dict[str, str] = {}
    month_ids: dict[tuple[str, str], str] = {}
    for move in moves:
        year = move["year"]
        month = move["month"]
        year_id = year_ids.setdefault(year, ensure_child_folder_id(drive_service, root_id, year))
        month_key = (year, month)
        month_id = month_ids.setdefault(month_key, ensure_child_folder_id(drive_service, year_id, month))
        result = move_day_folder(drive_service, move["id"], root_id, month_id)
        move["new_parent_id"] = str((result.get("parents") or [month_id])[0])
    return moves


def add_file_counts(drive_service, moves: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    rows = []
    for move in moves:
        row = dict(move)
        row["file_count"] = str(len(list_child_files(drive_service, move["id"])))
        rows.append(row)
    return rows


def write_move_report(path: Path, rows: Iterable[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["id", "name", "year", "month", "new_parent_id", "file_count"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _find_folder_id(folders: Iterable[dict[str, Any]], name: str) -> str:
    wanted = name.strip().casefold()
    for folder in folders:
        if str(folder.get("name", "")).strip().casefold() == wanted:
            return str(folder.get("id", ""))
    return ""

def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

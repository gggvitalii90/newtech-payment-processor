from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from payment_processor.env import load_env
from payment_processor.google_api import build_drive_service, get_credentials, load_google_settings


REPORT_DIR = PROJECT_ROOT / "reports"
FULL_REPORT = REPORT_DIR / "drive_root_folders_2026-06-16.csv"
CANDIDATE_REPORT = REPORT_DIR / "drive_folder_cleanup_candidates_2026-06-16.csv"
FOLDER_MIME = "application/vnd.google-apps.folder"
RECENT_CUTOFF = datetime(2026, 6, 15, tzinfo=timezone.utc)
CHAT_MARKERS = (
    "Проект",
    "Статья",
    "Ответственный",
    "Контрагент",
    "Назначение",
    "В долг",
)


def main() -> None:
    settings = load_google_settings(load_env())
    drive_service = build_drive_service(get_credentials(settings))
    folders = list_root_folders(drive_service, settings.archive_root_folder_id)
    groups = group_similar_names(folders)
    candidate_rows = build_candidate_rows(folders, groups)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(FULL_REPORT, folders)
    write_csv(CANDIDATE_REPORT, candidate_rows)
    print(f"root_folders={len(folders)}")
    print(f"cleanup_candidates={len(candidate_rows)}")
    print(f"full_report={FULL_REPORT}")
    print(f"candidate_report={CANDIDATE_REPORT}")
    for row in candidate_rows[:60]:
        print(f"{row['name']} | {row['reason']} | {row['group']}")


def list_root_folders(drive_service, root_folder_id: str) -> list[dict[str, str]]:
    query = f"'{root_folder_id}' in parents and mimeType = '{FOLDER_MIME}' and trashed = false"
    folders: list[dict[str, str]] = []
    page_token = None
    while True:
        response = (
            drive_service.files()
            .list(
                q=query,
                fields="nextPageToken,files(id,name,createdTime,modifiedTime,webViewLink)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                pageSize=1000,
                pageToken=page_token,
                orderBy="name",
            )
            .execute()
        )
        for folder in response.get("files", []):
            folders.append(
                {
                    "name": str(folder.get("name", "")),
                    "id": str(folder.get("id", "")),
                    "createdTime": str(folder.get("createdTime", "")),
                    "modifiedTime": str(folder.get("modifiedTime", "")),
                    "webViewLink": str(folder.get("webViewLink", "")),
                }
            )
        page_token = response.get("nextPageToken")
        if not page_token:
            return folders


def group_similar_names(folders: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for folder in folders:
        groups[normalize_name(folder["name"])].append(folder)
    return {key: value for key, value in groups.items() if key and len(value) > 1}


def build_candidate_rows(folders: list[dict[str, str]], groups: dict[str, list[dict[str, str]]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    group_by_id = {
        folder["id"]: " / ".join(item["name"] for item in group)
        for group in groups.values()
        for folder in group
    }
    for folder in folders:
        reasons = candidate_reasons(folder, group_by_id)
        if not reasons:
            continue
        row = dict(folder)
        row["reason"] = "; ".join(reasons)
        row["group"] = group_by_id.get(folder["id"], "")
        rows.append(row)
    return sorted(rows, key=lambda row: (row["group"], row["modifiedTime"], row["name"]))


def candidate_reasons(folder: dict[str, str], group_by_id: dict[str, str]) -> list[str]:
    reasons: list[str] = []
    name = folder["name"]
    modified = parse_time(folder.get("modifiedTime", ""))
    created = parse_time(folder.get("createdTime", ""))
    if modified and modified >= RECENT_CUTOFF:
        reasons.append("modified_on_or_after_2026-06-15")
    if created and created >= RECENT_CUTOFF:
        reasons.append("created_on_or_after_2026-06-15")
    if folder["id"] in group_by_id:
        reasons.append("similar_name_group")
    if any(marker.lower() in name.lower() for marker in CHAT_MARKERS):
        reasons.append("chat_signature_fragment_in_name")
    if len(name) >= 45:
        reasons.append("long_folder_name")
    return reasons


def parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def normalize_name(value: str) -> str:
    return re.sub(r"[^0-9a-zа-яё]+", "", value.lower().replace("ё", "е"))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = ["name", "id", "createdTime", "modifiedTime", "webViewLink", "reason", "group"]
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


if __name__ == "__main__":
    main()

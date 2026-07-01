from __future__ import annotations

import argparse
import csv
import hashlib
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from payment_processor.archive_reconciliation import (
    CHAT_COLUMNS,
    ContentMatch,
    build_chat_updated_row,
    build_message_evidence,
    sha256_content,
)
from payment_processor.config import load_config
from payment_processor.dictionaries import load_dictionaries
from payment_processor.env import load_env
from payment_processor.google_api import (
    build_drive_service,
    build_sheets_service,
    download_drive_file,
    extract_google_id,
    get_credentials,
    load_google_settings,
)
from payment_processor.invoice_archive import INVOICE_ARCHIVE_COLUMNS, extract_invoice_details_from_file
from payment_processor.max_api import MaxApiClient, get_messages_between_dates
from payment_processor.max_message_matching import SignatureMatch, match_signatures_by_sequence
from payment_processor.payment_history import reference_lists_from_dictionaries


EDITABLE_COLUMNS = ("Контрагент", *CHAT_COLUMNS.values(), "Сумма", "Статус разбора")
REPORT_DIR = PROJECT_ROOT / "reports"
POLICY_VERSION = "safe_archive_v4"


@dataclass
class RowDecision:
    row_number: int
    drive_id: str
    drive_sha256: str
    content_confidence: str
    signature_confidence: str
    max_key: str
    max_message_id: str
    max_file_id: str
    candidates: tuple[str, ...]
    reason: str
    old_row: list[str]
    new_row: list[str]


def main() -> None:
    args = parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        raise SystemExit("end must be >= start")

    env = load_env()
    config = load_config()
    dictionaries = load_dictionaries(prefer_google=True)
    reference_lists = reference_lists_from_dictionaries(dictionaries)
    settings = load_google_settings(env)
    if not settings.archive_spreadsheet_id:
        raise SystemExit("Missing GOOGLE_ARCHIVE_SPREADSHEET_ID")
    credentials = get_credentials(settings)
    drive_service = build_drive_service(credentials)
    sheets_service = build_sheets_service(credentials)
    metadata = read_sheet_metadata(sheets_service, settings.archive_spreadsheet_id)
    require_sheet(metadata, settings.archive_sheet_name)

    snapshot = read_sheet_values(sheets_service, settings.archive_spreadsheet_id, settings.archive_sheet_name)
    if not snapshot:
        raise SystemExit("Archive sheet is empty")
    headers = snapshot[0]
    require_columns(headers)
    rows = snapshot[1:]
    target_rows = [
        (row_number, row)
        for row_number, row in enumerate(rows, start=2)
        if _row_in_period(headers, row, start, end)
    ]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = f"{date.today().isoformat()}_{POLICY_VERSION}"
    backup_path = REPORT_DIR / f"archive_chat_reconciliation_backup_{stamp}.csv"
    plan_path = REPORT_DIR / f"archive_chat_reconciliation_plan_{stamp}.csv"
    applied_path = REPORT_DIR / f"archive_chat_reconciliation_applied_{stamp}.csv"
    restored_path = REPORT_DIR / f"archive_chat_reconciliation_restored_{stamp}.csv"
    if args.restore_backup and not backup_path.exists():
        raise RuntimeError(f"Backup not found: {backup_path}")
    if not backup_path.exists():
        write_csv_rows(backup_path, snapshot)

    if args.restore_backup:
        backup = read_csv_rows(backup_path)
        restore_rows = build_restore_rows(backup, snapshot, headers)
        updates = build_cell_updates(settings.archive_sheet_name, headers, restore_rows)
        for offset in range(0, len(updates), 500):
            apply_cell_updates(sheets_service, settings.archive_spreadsheet_id, updates[offset : offset + 500])
        after = read_sheet_values(sheets_service, settings.archive_spreadsheet_id, settings.archive_sheet_name)
        verify_planned_rows(snapshot, after, headers, restore_rows)
        write_applied_report(restored_path, updates)
        print(f"restored_from_backup={backup_path}")
        print(f"restored_cells={len(updates)}")
        print(f"verified_rows={len(restore_rows)}")
        print(f"restore_report={restored_path}")
        return

    if args.apply and plan_path.exists():
        backup = read_csv_rows(backup_path)
        verify_protected_snapshot(backup, snapshot, headers)
        planned_rows = load_audited_plan_rows(plan_path, headers, rows)
        updates = build_cell_updates(settings.archive_sheet_name, headers, planned_rows)
        for offset in range(0, len(updates), 500):
            apply_cell_updates(sheets_service, settings.archive_spreadsheet_id, updates[offset : offset + 500])
        after = read_sheet_values(sheets_service, settings.archive_spreadsheet_id, settings.archive_sheet_name)
        verify_planned_rows(snapshot, after, headers, planned_rows)
        write_applied_report(applied_path, updates)
        print(f"applied_from_plan={plan_path}")
        print(f"applied_cells={len(updates)}")
        print(f"verified_rows={len(planned_rows)}")
        print(f"applied_report={applied_path}")
        return

    max_client = MaxApiClient(_require_env(env, "MAX_BOT_TOKEN"))
    chat_id = _require_env(env, "MAX_CHAT_ID")
    count = max(1, min(int(env.get("MAX_MESSAGE_COUNT", "100") or "100"), 100))
    messages = get_messages_between_dates(max_client, chat_id, start, end, count=count)

    evidence, candidates = build_message_evidence(messages)
    download_errors: list[str] = []

    cache_dir = Path(tempfile.gettempdir()) / "codex_new_archive_reconciliation_cache"
    max_paths = download_max_candidates(max_client, candidates, cache_dir / "max", download_errors)
    validate_download_errors(download_errors, applying=args.apply)
    if download_errors:
        print(f"max_download_errors={len(download_errors)}", flush=True)
    max_hashes = {key: sha256_content(path) for key, path in max_paths.items()}
    drive_paths: dict[str, Path] = {}
    drive_errors: dict[str, str] = {}
    for _, row in target_rows:
        drive_id = extract_google_id(_cell(headers, row, "Google Drive ссылка"))
        if not drive_id or drive_id in drive_paths or drive_id in drive_errors:
            continue
        destination = cache_dir / "drive" / f"{drive_id}.bin"
        try:
            drive_paths[drive_id] = destination if destination.exists() else download_drive_file(drive_service, drive_id, destination)
            if len(drive_paths) % 50 == 0:
                print(f"drive_files_ready={len(drive_paths)}", flush=True)
        except Exception as exc:
            drive_errors[drive_id] = str(exc)
    if drive_errors and args.apply:
        details = [f"{drive_id}: {error}" for drive_id, error in drive_errors.items()]
        raise RuntimeError("Drive downloads failed; apply blocked:\n" + "\n".join(details))
    if drive_errors:
        print(f"missing_drive_files={len(drive_errors)}", flush=True)

    drive_hashes = {
        drive_id: sha256_content(path)
        for drive_id, path in drive_paths.items()
    }
    content_matches: dict[int, ContentMatch] = {}
    document_fields_by_file: dict[str, dict[str, str]] = {}
    for row_number, row in target_rows:
        drive_id = extract_google_id(_cell(headers, row, "Google Drive ссылка"))
        drive_hash = drive_hashes.get(drive_id, "")
        content_match = (
            match_hash(
                drive_hash,
                max_hashes,
                candidates,
                preferred_file_id=_cell(headers, row, "MAX file_id"),
            )
            if drive_hash
            else match_missing_drive(candidates, _cell(headers, row, "MAX file_id"))
        )
        content_matches[row_number] = content_match
        if content_match.max_key:
            existing = document_fields_from_row(headers, row)
            extracted = document_fields_by_file.get(content_match.max_key)
            if extracted is None:
                extracted = extract_invoice_details_from_file(max_paths[content_match.max_key])
            document_fields_by_file[content_match.max_key] = {
                key: extracted.get(key, "") or existing.get(key, "")
                for key in {"counterparty", "invoice_number", "invoice_date", "amount", "purpose"}
            }
    signature_matches = match_signatures_by_sequence(evidence, document_fields_by_file)
    authors_by_message = {item.message_id: item.author_id for item in evidence}
    decisions = [
        decide_row(
            row_number=row_number,
            row=row,
            headers=headers,
            content_match=content_matches[row_number],
            candidates=candidates,
            signature_matches=signature_matches,
            dictionaries=dictionaries,
            reference_lists=reference_lists,
            authors_by_message=authors_by_message,
            document_fields_by_file=document_fields_by_file,
        )
        for row_number, row in target_rows
    ]

    write_plan(plan_path, headers, decisions)
    print_summary(metadata, settings.archive_sheet_name, target_rows, decisions, backup_path, plan_path)
    if not args.apply:
        return

    updates = build_cell_updates(
        settings.archive_sheet_name,
        headers,
        [(item.row_number, item.old_row, item.new_row) for item in decisions],
    )
    for offset in range(0, len(updates), 500):
        apply_cell_updates(sheets_service, settings.archive_spreadsheet_id, updates[offset : offset + 500])

    after = read_sheet_values(sheets_service, settings.archive_spreadsheet_id, settings.archive_sheet_name)
    verify_applied(snapshot, after, headers, decisions)
    write_applied_report(applied_path, updates)
    print(f"applied_cells={len(updates)}")
    print(f"verified_rows={len(decisions)}")
    print(f"applied_report={applied_path}")


def match_hash(
    drive_hash: str,
    max_hashes: dict[str, str],
    candidate_details=None,
    preferred_file_id: str = "",
) -> ContentMatch:
    content_candidates = tuple(sorted(key for key, value in max_hashes.items() if value == drive_hash))
    if len(content_candidates) == 1:
        return ContentMatch(drive_hash, content_candidates[0], "content_exact", content_candidates)
    if content_candidates and preferred_file_id and candidate_details:
        matching_file_ids = tuple(
            key
            for key in content_candidates
            if key in candidate_details and candidate_details[key].file_id == preferred_file_id
        )
        if len(matching_file_ids) == 1:
            return ContentMatch(drive_hash, matching_file_ids[0], "content_file_id", content_candidates)
    if content_candidates:
        return ContentMatch(drive_hash, "", "ambiguous", content_candidates)
    return ContentMatch(drive_hash, "", "not_found", ())


def match_missing_drive(candidates, preferred_file_id: str) -> ContentMatch:
    matches = tuple(
        key for key, candidate in candidates.items()
        if preferred_file_id and candidate.file_id == preferred_file_id
    )
    if len(matches) == 1:
        return ContentMatch("", matches[0], "max_file_id", matches)
    return ContentMatch("", "", "missing_drive_file", matches)


def validate_download_errors(errors: list[str], applying: bool) -> None:
    if errors and applying:
        raise RuntimeError("MAX downloads failed; apply blocked:\n" + "\n".join(errors))


def decide_row(
    *,
    row_number: int,
    row: list[str],
    headers: list[str],
    content_match: ContentMatch,
    candidates,
    signature_matches: dict[str, SignatureMatch],
    dictionaries: dict[str, Any],
    reference_lists: dict[str, list[str]],
    authors_by_message: dict[str, str] | None = None,
    document_fields_by_file: dict[str, dict[str, str]] | None = None,
) -> RowDecision:
    authors_by_message = authors_by_message or {}
    document_fields_by_file = document_fields_by_file or {}
    drive_id = extract_google_id(_cell(headers, row, "Google Drive ссылка"))
    signature_match = signature_matches.get(content_match.max_key) if content_match.max_key else None
    signature_confidence = signature_match.confidence if signature_match else "ambiguous"
    signature = signature_match.signature if signature_match else {}
    candidate = candidates.get(content_match.max_key) if content_match.max_key else None
    reason = _decision_reason(drive_id, content_match, signature_match)
    new_row = build_chat_updated_row(
        headers,
        row,
        signature,
        signature_confidence,
        dictionaries,
        reference_lists,
        fallback_author=authors_by_message.get(candidate.message_id, "") if candidate else "",
        document_fields=document_fields_by_file.get(content_match.max_key, {}),
    )
    return RowDecision(
        row_number=row_number,
        drive_id=drive_id,
        drive_sha256=content_match.sha256,
        content_confidence=content_match.confidence,
        signature_confidence=signature_confidence,
        max_key=content_match.max_key,
        max_message_id=candidate.message_id if candidate else "",
        max_file_id=candidate.file_id if candidate else "",
        candidates=content_match.candidate_keys,
        reason=reason,
        old_row=[*row],
        new_row=new_row,
    )


def document_fields_from_row(headers: list[str], row: list[str]) -> dict[str, str]:
    return {
        "counterparty": _cell(headers, row, "Контрагент"),
        "invoice_number": _cell(headers, row, "Номер счета"),
        "invoice_date": _cell(headers, row, "Дата счета"),
        "amount": _cell(headers, row, "Сумма"),
    }


def _decision_reason(drive_id: str, content_match: ContentMatch, signature_match: SignatureMatch | None) -> str:
    if not drive_id:
        return "missing_drive_id"
    if content_match.confidence == "not_found":
        return "drive_content_not_found_in_max"
    if content_match.confidence == "ambiguous":
        return "multiple_max_files_with_same_content"
    if signature_match is None or signature_match.confidence == "ambiguous":
        return "signature_not_uniquely_matched"
    return "matched"


def download_max_candidates(client, candidates, target_dir: Path, errors: list[str]) -> dict[str, Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    path_by_url: dict[str, Path] = {}
    for key, candidate in candidates.items():
        if not candidate.url:
            continue
        if candidate.url in path_by_url:
            paths[key] = path_by_url[candidate.url]
            continue
        destination = target_dir / candidate_cache_name(candidate)
        if destination.exists():
            path_by_url[candidate.url] = destination
            paths[key] = destination
            continue
        try:
            client.download_url(candidate.url, destination)
        except Exception as exc:
            errors.append(f"{candidate.message_id}/{candidate.file_id}/{candidate.filename}: {exc}")
            continue
        path_by_url[candidate.url] = destination
        paths[key] = destination
        if len(paths) % 100 == 0:
            print(f"max_files_ready={len(paths)}", flush=True)
    return paths


def candidate_cache_name(candidate) -> str:
    identity = "|".join((candidate.message_id, candidate.file_id, candidate.filename))
    suffix = Path(candidate.filename).suffix.lower() or ".bin"
    return f"stable_{hashlib.sha1(identity.encode('utf-8')).hexdigest()}{suffix}"


def build_cell_updates(
    sheet_name: str,
    headers: list[str],
    rows: list[tuple[int, list[str], list[str]]],
) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    quoted_sheet = sheet_name.replace("'", "''")
    for row_number, old_row, new_row in rows:
        old_values = _pad_row(old_row, len(headers))
        new_values = _pad_row(new_row, len(headers))
        for column in EDITABLE_COLUMNS:
            if column not in headers:
                continue
            index = headers.index(column)
            if old_values[index] == new_values[index]:
                continue
            updates.append(
                {
                    "range": f"'{quoted_sheet}'!{column_letter(index + 1)}{row_number}",
                    "values": [[new_values[index]]],
                }
            )
    return updates


def apply_cell_updates(sheets_service, spreadsheet_id: str, updates: list[dict[str, Any]]) -> dict[str, Any]:
    if not updates:
        return {"totalUpdatedCells": 0}
    return sheets_service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": "RAW", "data": updates},
    ).execute()


def load_audited_plan_rows(
    plan_path: Path,
    headers: list[str],
    current_rows: list[list[str]],
) -> list[tuple[int, list[str], list[str]]]:
    result: list[tuple[int, list[str], list[str]]] = []
    seen_rows: set[int] = set()
    with plan_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for item in csv.DictReader(handle):
            if item.get("policy_version") != POLICY_VERSION:
                raise RuntimeError("Audited plan is stale or unsafe: policy version does not preserve ambiguous rows")
            row_number = int(item["row_number"])
            if row_number in seen_rows or row_number < 2 or row_number - 2 >= len(current_rows):
                raise RuntimeError(f"Invalid or duplicate row in audited plan: {row_number}")
            seen_rows.add(row_number)
            old_row = _pad_row(current_rows[row_number - 2], len(headers))
            new_row = [*old_row]
            for column in EDITABLE_COLUMNS:
                expected_old = item.get(f"old_{column}")
                expected_new = item.get(f"new_{column}")
                if expected_old is None or expected_new is None:
                    raise RuntimeError(f"Audited plan is missing columns for {column!r}")
                index = headers.index(column)
                if old_row[index] != expected_old:
                    raise RuntimeError(f"Audited plan is stale at row {row_number}, column {column!r}")
                new_row[index] = expected_new
            result.append((row_number, old_row, new_row))
    return result


def build_restore_rows(
    backup: list[list[str]],
    current: list[list[str]],
    headers: list[str],
) -> list[tuple[int, list[str], list[str]]]:
    verify_protected_snapshot(backup, current, headers)
    result: list[tuple[int, list[str], list[str]]] = []
    for row_number in range(2, len(current) + 1):
        current_row = _pad_row(current[row_number - 1], len(headers))
        backup_row = _pad_row(backup[row_number - 1], len(headers))
        restored_row = [*current_row]
        for column in EDITABLE_COLUMNS:
            if column not in headers:
                continue
            index = headers.index(column)
            restored_row[index] = backup_row[index]
        if restored_row != current_row:
            result.append((row_number, current_row, restored_row))
    return result


def verify_applied(
    before: list[list[str]],
    after: list[list[str]],
    headers: list[str],
    decisions: list[RowDecision],
) -> None:
    if not after or after[0] != before[0]:
        raise RuntimeError("Sheet headers changed during apply")
    if len(after) != len(before):
        raise RuntimeError("Sheet row count changed during apply")
    expected_by_row = {item.row_number: _pad_row(item.new_row, len(headers)) for item in decisions}
    for row_number in range(2, len(before) + 1):
        before_row = _pad_row(before[row_number - 1], len(headers))
        actual_row = _pad_row(after[row_number - 1], len(headers))
        expected_row = expected_by_row.get(row_number, before_row)
        for index, column in enumerate(headers):
            expected = expected_row[index] if column in EDITABLE_COLUMNS else before_row[index]
            if actual_row[index] != expected:
                raise RuntimeError(f"Verification failed at row {row_number}, column {column!r}")


def verify_planned_rows(
    before: list[list[str]],
    after: list[list[str]],
    headers: list[str],
    planned_rows: list[tuple[int, list[str], list[str]]],
) -> None:
    if not after or after[0] != before[0] or len(after) != len(before):
        raise RuntimeError("Sheet structure changed during apply")
    expected_by_row = {row_number: _pad_row(new_row, len(headers)) for row_number, _, new_row in planned_rows}
    for row_number in range(2, len(before) + 1):
        before_row = _pad_row(before[row_number - 1], len(headers))
        actual_row = _pad_row(after[row_number - 1], len(headers))
        expected_row = expected_by_row.get(row_number, before_row)
        for index, column in enumerate(headers):
            expected = expected_row[index] if column in EDITABLE_COLUMNS else before_row[index]
            if actual_row[index] != expected:
                raise RuntimeError(f"Verification failed at row {row_number}, column {column!r}")


def verify_protected_snapshot(backup: list[list[str]], current: list[list[str]], headers: list[str]) -> None:
    if not backup or backup[0] != headers or len(backup) != len(current):
        raise RuntimeError("Backup no longer matches sheet structure")
    for row_number in range(2, len(current) + 1):
        backup_row = _pad_row(backup[row_number - 1], len(headers))
        current_row = _pad_row(current[row_number - 1], len(headers))
        for index, column in enumerate(headers):
            if column not in EDITABLE_COLUMNS and backup_row[index] != current_row[index]:
                raise RuntimeError(f"Protected data changed since audit at row {row_number}, column {column!r}")


def read_sheet_metadata(sheets_service, spreadsheet_id: str) -> dict[str, Any]:
    return sheets_service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="properties(title),sheets(properties(sheetId,title,gridProperties))",
    ).execute()


def require_sheet(metadata: dict[str, Any], sheet_name: str) -> None:
    titles = [str(item.get("properties", {}).get("title", "")) for item in metadata.get("sheets", [])]
    if sheet_name not in titles:
        raise RuntimeError(f"Sheet {sheet_name!r} not found; available: {titles}")


def read_sheet_values(sheets_service, spreadsheet_id: str, sheet_name: str) -> list[list[str]]:
    quoted = sheet_name.replace("'", "''")
    response = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{quoted}'!A1:W",
    ).execute()
    return response.get("values", [])


def require_columns(headers: list[str]) -> None:
    required = {"Дата MAX", "Google Drive ссылка", *EDITABLE_COLUMNS}
    missing = sorted(required - set(headers))
    if missing:
        raise RuntimeError(f"Missing archive columns: {missing}")


def write_csv_rows(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows(rows)


def read_csv_rows(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


def write_plan(path: Path, headers: list[str], decisions: list[RowDecision]) -> None:
    fields = [
        "policy_version", "row_number", "drive_id", "drive_sha256", "content_confidence", "signature_confidence",
        "max_key", "max_message_id", "max_file_id", "candidate_keys", "reason",
    ]
    for column in EDITABLE_COLUMNS:
        fields.extend((f"old_{column}", f"new_{column}"))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in decisions:
            old_row = _pad_row(item.old_row, len(headers))
            new_row = _pad_row(item.new_row, len(headers))
            data = {
                "policy_version": POLICY_VERSION,
                "row_number": item.row_number,
                "drive_id": item.drive_id,
                "drive_sha256": item.drive_sha256,
                "content_confidence": item.content_confidence,
                "signature_confidence": item.signature_confidence,
                "max_key": item.max_key,
                "max_message_id": item.max_message_id,
                "max_file_id": item.max_file_id,
                "candidate_keys": "|".join(item.candidates),
                "reason": item.reason,
            }
            for column in EDITABLE_COLUMNS:
                index = headers.index(column)
                data[f"old_{column}"] = old_row[index]
                data[f"new_{column}"] = new_row[index]
            writer.writerow(data)


def write_applied_report(path: Path, updates: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["range", "value", "verified"])
        for update in updates:
            writer.writerow([update["range"], update["values"][0][0], "yes"])


def print_summary(metadata, sheet_name, target_rows, decisions, backup_path, plan_path) -> None:
    title = metadata.get("properties", {}).get("title", "")
    counts: dict[str, int] = {}
    for item in decisions:
        counts[item.reason] = counts.get(item.reason, 0) + 1
    print(f"spreadsheet={title}")
    print(f"sheet={sheet_name}")
    print(f"target_rows={len(target_rows)}")
    for reason, count in sorted(counts.items()):
        print(f"{reason}={count}")
    print(f"backup={backup_path}")
    print(f"plan={plan_path}")


def _row_in_period(headers: list[str], row: list[str], start: date, end: date) -> bool:
    value = _row_date(headers, row)
    return value is not None and start <= value <= end


def _row_date(headers: list[str], row: list[str]) -> date | None:
    raw = _cell(headers, row, "Дата MAX").strip()
    for pattern in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y", "%d.%m.%Y %H:%M:%S"):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
    return None


def _cell(headers: list[str], row: list[str], column: str) -> str:
    if column not in headers:
        return ""
    index = headers.index(column)
    return str(row[index]) if index < len(row) else ""


def _pad_row(row: list[str], length: int) -> list[str]:
    return [*row, *([""] * max(0, length - len(row)))]


def column_letter(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _require_env(env: dict[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise SystemExit(f"Missing {key} in .env")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-04-01")
    parser.add_argument("--end", default="2026-06-15")
    parser.add_argument("--audit", action="store_true", help="read-only audit (default behavior)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore-backup", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    main()

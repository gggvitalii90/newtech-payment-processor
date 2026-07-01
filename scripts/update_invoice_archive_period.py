from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payment_processor.config import load_config, load_rules
from payment_processor.dictionaries import load_dictionaries
from payment_processor.env import load_env
from payment_processor.google_api import build_drive_service, build_sheets_service, get_credentials, load_google_settings
from payment_processor.google_archive import append_archive_records, prepare_records_for_google_drive, setup_archive_sheet
from payment_processor.invoice_archive import (
    create_invoice_archive_records,
    enrich_invoice_records_from_files,
    mark_paid_records,
    write_invoice_archive_xlsx,
)
from payment_processor.max_api import build_client_from_env, get_messages_between_dates
from payment_processor.payment_history import collect_payment_pdfs, parse_payment_history, reference_lists_from_dictionaries
from payment_processor.period_update import select_invoice_records_for_files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Обновление Архива счетов из staging MAX")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--staging-root", default=".staging/payment-history")
    parser.add_argument("--mode", choices=["\u041f\u0421\u041a", "\u0418\u0421", "PSK", "IS"], default="\u041f\u0421\u041a")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def dates_between(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def main() -> int:
    args = parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        raise ValueError("Дата окончания раньше даты начала")
    staging = Path(args.staging_root)
    env = load_env()
    config = load_config()
    mode = _normalize_mode(args.mode)
    rules = load_rules()
    dictionaries = load_dictionaries(prefer_google=True)
    references = reference_lists_from_dictionaries(dictionaries)
    client, chat_id, count = build_client_from_env(env, mode)
    messages = get_messages_between_dates(client, chat_id, start, end, count=count)

    local_files: dict[str, Path] = {}
    for day in dates_between(start, end):
        folder = staging / "other" / (f"{day:%Y.%m.%d}" + config["modes"][mode].get("folder_suffix", ""))
        if folder.exists():
            local_files.update({path.name: path for path in folder.iterdir() if path.is_file()})

    records = create_invoice_archive_records(messages, mode, chat_id, dictionaries, references)
    records = select_invoice_records_for_files(records, set(local_files))
    enrich_invoice_records_from_files(records, local_files)

    payment_paths = _filter_paths_for_mode(collect_payment_pdfs(staging / "payments", start, end), mode)
    payments, payment_issues = parse_payment_history(payment_paths, rules)
    mark_paid_records(records, payments)

    report = ROOT / "reports" / f"invoice_archive_{start.isoformat()}_{end.isoformat()}.xlsx"
    write_invoice_archive_xlsx(report, records)
    summary = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "messages": len(messages),
        "downloaded_invoice_files": len(local_files),
        "archive_records": len(records),
        "file_records": sum(1 for record in records if record.file_name),
        "text_records": sum(1 for record in records if not record.file_name),
        "payment_parse_issues": len(payment_issues),
        "dry_run": args.dry_run,
        "mode": mode,
        "report": str(report),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if payment_issues:
        print("Google не обновлен: есть ошибки чтения ПП", flush=True)
        return 2
    if args.dry_run:
        print("DRY RUN: Архив счетов не изменялся", flush=True)
        return 0

    settings = load_google_settings(env)
    credentials = get_credentials(settings)
    drive = build_drive_service(credentials)
    sheets = build_sheets_service(credentials)
    setup_archive_sheet(sheets, settings.archive_spreadsheet_id, settings.archive_sheet_name)
    prepare_records_for_google_drive(drive, records, local_files, settings.archive_root_folder_id, dictionaries)
    append_archive_records(sheets, settings.archive_spreadsheet_id, settings.archive_sheet_name, records)
    print(f"Архив счетов обновлен: {len(records)} строк", flush=True)
    return 0


def _filter_paths_for_mode(paths: list[Path], mode: str) -> list[Path]:
    is_mode = (mode or "").strip().upper() == "ИС"
    return [path for path in paths if path.parent.name.upper().endswith(" ИС") == is_mode]

def _normalize_mode(value: str) -> str:
    mode = (value or "").strip().upper()
    if mode == "IS":
        return "\u0418\u0421"
    if mode == "PSK":
        return "\u041f\u0421\u041a"
    return mode or "\u041f\u0421\u041a"


if __name__ == "__main__":
    raise SystemExit(main())

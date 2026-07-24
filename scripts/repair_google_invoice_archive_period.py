from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from payment_processor.config import load_config, load_rules
from payment_processor.dictionaries import load_dictionaries
from payment_processor.env import load_env
from payment_processor.google_api import build_drive_service, build_sheets_service, get_credentials, load_google_settings, verify_drive_account
from payment_processor.google_archive import (
    _delete_sheet_rows,
    append_archive_records,
    prepare_records_for_google_drive,
    setup_archive_sheet,
)
from payment_processor.invoice_archive import (
    INVOICE_ARCHIVE_COLUMNS,
    create_invoice_archive_records,
    enrich_invoice_records_from_files,
    mark_paid_records,
    write_invoice_archive_xlsx,
)
from payment_processor.max_api import MaxApiClient, get_messages_for_date, sort_downloaded_files
from payment_processor.payment_classifier import is_payment_order_pdf
from payment_processor.payment_history import reference_lists_from_dictionaries
from payment_processor.workflow import process_folder


OTHER_MAX_DIR = "_" + "\u043f\u0440\u043e\u0447\u0435\u0435_MAX"
INVOICE_DRAFT_DIR = "_" + "\u0430\u0440\u0445\u0438\u0432_\u0441\u0447\u0435\u0442\u043e\u0432_\u0447\u0435\u0440\u043d\u043e\u0432\u0438\u043a"


@dataclass
class DayStats:
    day: date
    other_files: int
    moved_payment_orders: int
    archive_files: int
    records: int
    paid: int
    duplicates: int


def main() -> None:
    args = parse_args()
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    if end < start:
        raise SystemExit("end must be >= start")

    env = load_env()
    config = load_config()
    rules = load_rules()
    dictionaries = load_dictionaries(prefer_google=True)
    references = reference_lists_from_dictionaries(dictionaries)
    root = Path(config["root_folder"])
    date_format = config["date_folder_format"]
    mode = args.mode
    mode_config = config["modes"][mode]

    settings = load_google_settings(env)
    credentials = get_credentials(settings)
    drive_service = build_drive_service(credentials)
    verify_drive_account(drive_service, env.get("GOOGLE_ALLOWED_EMAIL", "pcknew.tech@gmail.com"))
    sheets_service = build_sheets_service(credentials)
    setup_archive_sheet(sheets_service, settings.archive_spreadsheet_id, settings.archive_sheet_name)

    current_values = read_sheet_values(sheets_service, settings.archive_spreadsheet_id, settings.archive_sheet_name)
    headers = current_values[0] if current_values else list(INVOICE_ARCHIVE_COLUMNS)
    existing_rows = current_values[1:] if current_values else []
    link_by_file_name = collect_existing_links(headers, existing_rows)
    rows_to_delete = row_numbers_in_period(headers, existing_rows, start, end)

    max_client = MaxApiClient(env["MAX_BOT_TOKEN"].strip())
    chat_id = env["MAX_CHAT_ID"].strip()
    count = int(env.get("MAX_MESSAGE_COUNT", "100") or "100")

    all_records = []
    stats = []
    for day in iter_dates(start, end):
        folder = root / f"{day.strftime(date_format)}{mode_config.get('folder_suffix', '')}"
        other = root / OTHER_MAX_DIR / folder.name
        other.mkdir(parents=True, exist_ok=True)
        folder.mkdir(parents=True, exist_ok=True)

        other_files_before = [path for path in other.iterdir() if path.is_file()]
        payment_orders_in_other = [path for path in other_files_before if is_payment_order_pdf(path)]
        moved_payment_orders, _ = sort_downloaded_files(payment_orders_in_other, folder, other, is_payment_order_pdf)

        other_files = [path for path in other.iterdir() if path.is_file()]
        archive_files = [path for path in other_files if not is_payment_order_pdf(path)]
        local_files_by_name = {path.name: path for path in archive_files}

        messages = get_messages_for_date(max_client, chat_id, day, count=count)
        records = create_invoice_archive_records(messages, mode, chat_id, dictionaries, references)
        allowed_names = set(local_files_by_name)
        records = [record for record in records if record.file_name and record.file_name in allowed_names]
        enrich_invoice_records_from_files(records, local_files_by_name)

        payments = process_folder(folder, rules) if folder.exists() else []
        mark_paid_records(records, payments)
        for record in records:
            record.google_drive_link = link_by_file_name.get(record.file_name, record.google_drive_link)

        draft_path = root / INVOICE_DRAFT_DIR / f"{day.strftime(date_format)}.xlsx"
        write_invoice_archive_xlsx(draft_path, records)
        all_records.extend(records)
        file_names = [record.file_name for record in records]
        stats.append(
            DayStats(
                day=day,
                other_files=len(other_files_before),
                moved_payment_orders=len(moved_payment_orders),
                archive_files=len(archive_files),
                records=len(records),
                paid=sum(1 for record in records if record.payment_status == "Оплачен"),
                duplicates=len(file_names) - len(set(file_names)),
            )
        )
        print(
            f"{day.isoformat()} records={len(records)} archive_files={len(archive_files)} "
            f"moved_pp={len(moved_payment_orders)} paid={stats[-1].paid}",
            flush=True,
        )

    duplicate_records_before = len(all_records) - len({record.file_name for record in all_records if record.file_name})
    all_records = dedupe_records_by_file_name(all_records)
    missing_links = [record for record in all_records if record.file_name and not record.google_drive_link]
    if missing_links:
        all_files_by_name = {}
        for day in iter_dates(start, end):
            folder = root / f"{day.strftime(date_format)}{mode_config.get('folder_suffix', '')}"
            other = root / OTHER_MAX_DIR / folder.name
            if other.exists():
                all_files_by_name.update({path.name: path for path in other.iterdir() if path.is_file() and not is_payment_order_pdf(path)})
        prepare_records_for_google_drive(drive_service, missing_links, all_files_by_name, settings.archive_root_folder_id, dictionaries)

    current_values = read_sheet_values(sheets_service, settings.archive_spreadsheet_id, settings.archive_sheet_name)
    existing_rows = current_values[1:] if current_values else []
    rows_to_delete = row_numbers_in_period(headers, existing_rows, start, end)
    if rows_to_delete:
        _delete_sheet_rows(sheets_service, settings.archive_spreadsheet_id, settings.archive_sheet_name, sorted(rows_to_delete, reverse=True))
    append_archive_records(sheets_service, settings.archive_spreadsheet_id, settings.archive_sheet_name, all_records)

    print("deleted_rows=", len(rows_to_delete))
    print("appended_records=", len(all_records))
    print("unique_file_names=", len({record.file_name for record in all_records if record.file_name}))
    print("duplicate_file_names_before_dedupe=", duplicate_records_before)
    print("duplicate_file_names=", len(all_records) - len({record.file_name for record in all_records if record.file_name}))
    print("payment_like_records=", sum(1 for record in all_records if "поручение" in record.file_name.lower() or "Платежное_поручение" in record.file_name))
    print("drive_links=", sum(1 for record in all_records if record.google_drive_link))
    print("paid=", sum(1 for record in all_records if record.payment_status == "Оплачен"))
    print("days=", len(stats))
    print("days_with_moved_pp=", sum(1 for item in stats if item.moved_payment_orders))


def dedupe_records_by_file_name(records):
    best_by_name = {}
    passthrough = []
    for record in records:
        if not record.file_name:
            passthrough.append(record)
            continue
        current = best_by_name.get(record.file_name)
        if current is None or record_quality(record) > record_quality(current):
            best_by_name[record.file_name] = record
    return [*passthrough, *best_by_name.values()]


def record_quality(record) -> int:
    fields = [
        record.google_drive_link,
        record.payment_status == "Оплачен",
        record.counterparty,
        record.invoice_number,
        record.invoice_date,
        record.object_name,
        record.project,
        record.budget_item,
        record.responsible,
        record.purpose,
        record.amount,
        not record.analysis_status,
    ]
    return sum(1 for value in fields if value)


def read_sheet_values(sheets_service, spreadsheet_id: str, sheet_name: str) -> list[list[str]]:
    response = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A1:W",
    ).execute()
    return response.get("values", [])


def collect_existing_links(headers: list[str], rows: list[list[str]]) -> dict[str, str]:
    file_idx = headers.index("Имя файла") if "Имя файла" in headers else -1
    link_idx = headers.index("Google Drive ссылка") if "Google Drive ссылка" in headers else -1
    result = {}
    if file_idx < 0 or link_idx < 0:
        return result
    for row in rows:
        file_name = row[file_idx].strip() if len(row) > file_idx else ""
        link = row[link_idx].strip() if len(row) > link_idx else ""
        if file_name and link and file_name not in result:
            result[file_name] = link
    return result


def row_numbers_in_period(headers: list[str], rows: list[list[str]], start: date, end: date) -> list[int]:
    date_idx = headers.index("Дата MAX") if "Дата MAX" in headers else -1
    if date_idx < 0:
        return []
    result = []
    for index, row in enumerate(rows, start=2):
        value = row[date_idx][:10] if len(row) > date_idx else ""
        try:
            row_date = date.fromisoformat(value)
        except ValueError:
            continue
        if start <= row_date <= end:
            result.append(index)
    return result


def iter_dates(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-04-01")
    parser.add_argument("--end", default="2026-06-15")
    parser.add_argument("--mode", default="ПСК")
    return parser.parse_args()


if __name__ == "__main__":
    main()

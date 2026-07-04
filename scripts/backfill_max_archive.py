from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from payment_processor.cash_archive import (
    cash_records_to_payment_records,
    create_cash_archive_records,
    write_cash_archive_xlsx,
)
from payment_processor.config import load_config, load_rules
from payment_processor.dictionaries import load_dictionaries
from payment_processor.env import load_env
from payment_processor.google_api import (
    build_drive_service,
    build_sheets_service,
    get_credentials,
    load_google_settings,
)
from payment_processor.google_archive import (
    append_archive_records,
    prepare_records_for_google_drive,
    read_archive_records,
    setup_archive_sheet,
)
from payment_processor.invoice_archive import (
    create_invoice_archive_records,
    enrich_invoice_records_from_files,
    invoice_text_operation_records_to_payment_records,
    mark_paid_records,
    write_invoice_archive_xlsx,
)
from payment_processor.max_api import (
    MaxApiClient,
    build_client_from_env,
    cash_chat_id_from_env,
    download_chat_files_for_date,
    get_messages_for_date,
    sort_downloaded_files,
)
from payment_processor.payment_classifier import is_payment_order_pdf
from payment_processor.parser import parse_payment_pdf
from payment_processor.payment_history import reference_lists_from_dictionaries
from payment_processor.workflow import read_records_from_workbook, write_records_to_workbook


OTHER_MAX_DIR = "_" + "\u043f\u0440\u043e\u0447\u0435\u0435_MAX"
INVOICE_DRAFT_DIR = "_" + "\u0430\u0440\u0445\u0438\u0432_\u0441\u0447\u0435\u0442\u043e\u0432_\u0447\u0435\u0440\u043d\u043e\u0432\u0438\u043a"
CASH_DRAFT_DIR = "_" + "\u0430\u0440\u0445\u0438\u0432_\u043d\u0430\u043b\u0438\u0447\u043a\u0438_\u0447\u0435\u0440\u043d\u043e\u0432\u0438\u043a"


@dataclass
class DayResult:
    day: date
    status: str
    downloaded: int = 0
    skipped: int = 0
    payment_orders: int = 0
    archive_files: int = 0
    invoice_rows: int = 0
    google_rows: int = 0
    cash_rows: int = 0
    payment_rows: int = 0
    error: str = ""


def main() -> None:
    args = parse_args()
    start = parse_date(args.start)
    end = parse_date(args.end) if args.end else date.today()
    if end < start:
        raise SystemExit("end date must be >= start date")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    env = load_env()
    config = load_config()
    rules = load_rules()
    dictionaries = load_dictionaries(prefer_google=not args.local_only)
    references = reference_lists_from_dictionaries(dictionaries)

    root = Path(config["root_folder"])
    output = Path(config["output_file"])
    mode = normalize_mode(args.mode or next(iter(config["modes"])))
    mode_config = config["modes"][mode]
    sheet_name = mode_config["sheet_name"]
    date_format = config["date_folder_format"]

    max_client, chat_id, count = build_client_from_env(env, mode)
    cash_chat_id = cash_chat_id_from_env(env, mode)

    drive_service = None
    sheets_service = None
    google_settings = None
    if not args.local_only:
        google_settings = load_google_settings(env)
        credentials = get_credentials(google_settings)
        drive_service = build_drive_service(credentials)
        sheets_service = build_sheets_service(credentials)
        if google_settings.archive_spreadsheet_id:
            setup_archive_sheet(sheets_service, google_settings.archive_spreadsheet_id, google_settings.archive_sheet_name)

    existing_payment_records = read_records_from_workbook(output, sheet_name)
    payment_records_by_key = {payment_record_key(record): record for record in existing_payment_records}
    results: list[DayResult] = []

    for day in iter_dates(start, end):
        try:
            result, day_payment_records = process_day(
                day=day,
                root=root,
                mode=mode,
                mode_config=mode_config,
                sheet_name=sheet_name,
                date_format=date_format,
                rules=rules,
                dictionaries=dictionaries,
                references=references,
                max_client=max_client,
                chat_id=chat_id,
                cash_chat_id=cash_chat_id,
                count=count,
                drive_service=drive_service,
                sheets_service=sheets_service,
                google_settings=google_settings,
                local_only=args.local_only,
            )
            for record in day_payment_records:
                payment_records_by_key[payment_record_key(record)] = record
            results.append(result)
            logging.info(
                "%s ok: downloaded=%s invoices=%s google=%s cash=%s payments=%s",
                day.isoformat(),
                result.downloaded,
                result.invoice_rows,
                result.google_rows,
                result.cash_rows,
                result.payment_rows,
            )
        except Exception as exc:
            logging.exception("%s failed", day.isoformat())
            results.append(DayResult(day=day, status="error", error=str(exc)))

    write_records_to_workbook(output, sheet_name, list(payment_records_by_key.values()), references)
    report_path = write_report(results, Path("reports"), start, end)
    print_summary(results, report_path, output, sheet_name)


def process_day(
    *,
    day: date,
    root: Path,
    mode: str,
    mode_config: dict,
    sheet_name: str,
    date_format: str,
    rules: dict,
    dictionaries: dict,
    references: dict,
    max_client: MaxApiClient,
    chat_id: str,
    cash_chat_id: str,
    count: int,
    drive_service,
    sheets_service,
    google_settings,
    local_only: bool,
):
    folder = root / f"{day.strftime(date_format)}{mode_config.get('folder_suffix', '')}"
    inbox = root / ".max_inbox" / folder.name
    other = root / OTHER_MAX_DIR / folder.name

    summary = download_chat_files_for_date(max_client, chat_id, inbox, day, count=count)
    payment_orders, other_files = sort_downloaded_files(summary.downloaded, folder, other, is_payment_order_pdf)
    restored_payment_orders, _ = sort_downloaded_files(
        [path for path in collect_existing_files(other) if is_payment_order_pdf(path)],
        folder,
        other,
        is_payment_order_pdf,
    )
    existing_archive_files = [path for path in collect_existing_files(other) if not is_payment_order_pdf(path)]
    payment_orders = [
        path
        for path in merge_files_by_name([collect_existing_files(folder), restored_payment_orders, payment_orders])
        if path.exists()
    ]
    archive_files = [
        path
        for path in merge_files_by_name([existing_archive_files, other_files])
        if path.exists() and not is_payment_order_pdf(path)
    ]

    messages = get_messages_for_date(max_client, chat_id, day, count=count)
    invoice_records = create_invoice_archive_records(messages, mode, chat_id, dictionaries, references)
    other_names = {path.name for path in archive_files}
    invoice_records = [record for record in invoice_records if not record.file_name or record.file_name in other_names]
    archive_records = [record for record in invoice_records if record.file_name]
    enrich_invoice_records_from_files(archive_records, {path.name: path for path in archive_files})

    payment_records = parse_payment_order_files(payment_orders, rules)
    mark_paid_records(archive_records, payment_records)

    google_rows = 0
    if not local_only and google_settings and google_settings.archive_spreadsheet_id and google_settings.archive_root_folder_id:
        existing_google_records = read_archive_records(
            sheets_service,
            google_settings.archive_spreadsheet_id,
            google_settings.archive_sheet_name,
        )
        prepare_records_for_google_drive(
            drive_service=drive_service,
            records=archive_records,
            local_files_by_name={path.name: path for path in archive_files},
            root_folder_id=google_settings.archive_root_folder_id,
            dictionaries=dictionaries,
            existing_records=existing_google_records,
        )
        append_archive_records(
            sheets_service,
            google_settings.archive_spreadsheet_id,
            google_settings.archive_sheet_name,
            archive_records,
        )
        google_rows = len(archive_records)

    invoice_draft_path = root / INVOICE_DRAFT_DIR / f"{day.strftime(date_format)}.xlsx"
    write_invoice_archive_xlsx(invoice_draft_path, archive_records)

    day_payment_records = list(payment_records)
    day_payment_records.extend(invoice_text_operation_records_to_payment_records(invoice_records))

    cash_rows = 0
    if cash_chat_id:
        cash_messages = get_messages_for_date(max_client, cash_chat_id, day, count=count)
        cash_records = create_cash_archive_records(cash_messages, cash_chat_id, dictionaries, references)
        cash_draft_path = root / CASH_DRAFT_DIR / f"{day.strftime(date_format)}.xlsx"
        write_cash_archive_xlsx(cash_draft_path, cash_records)
        cash_rows = len(cash_records)
        day_payment_records.extend(cash_records_to_payment_records(cash_records))

    return (
        DayResult(
            day=day,
            status="ok",
            downloaded=len(summary.downloaded),
            skipped=len(summary.skipped),
            payment_orders=len(payment_orders),
            archive_files=len(archive_files),
            invoice_rows=len(archive_records),
            google_rows=google_rows,
            cash_rows=cash_rows,
            payment_rows=len(day_payment_records),
        ),
        day_payment_records,
    )


def parse_payment_order_files(payment_orders: list[Path], rules: dict) -> list:
    records = []
    seen: set[tuple[str, str, str]] = set()
    for path in payment_orders:
        try:
            record = parse_payment_pdf(path, rules)
        except Exception:
            logging.exception("Payment order parse failed: %s", path)
            continue
        key = (record.counterparty, record.invoice_number, record.amount)
        if key in seen:
            continue
        seen.add(key)
        records.append(record)
    return records


def collect_existing_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return [path for path in folder.iterdir() if path.is_file()]


def merge_files_by_name(file_groups: list[list[Path]]) -> list[Path]:
    merged: dict[str, Path] = {}
    for files in file_groups:
        for path in files:
            merged[path.name] = path
    return list(merged.values())


def payment_record_key(record) -> tuple[str, str, str, str, str]:
    return (
        record.name or "",
        record.date or "",
        record.payment_type or "",
        record.invoice_number or "",
        record.amount or "",
    )


def iter_dates(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def require_env(env: dict[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise SystemExit(f"Missing {key} in .env")
    return value


def write_report(results: list[DayResult], report_dir: Path, start: date, end: date) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"max_backfill_{start.isoformat()}_{end.isoformat()}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "date",
                "status",
                "downloaded",
                "skipped",
                "payment_orders",
                "archive_files",
                "invoice_rows",
                "google_rows",
                "cash_rows",
                "payment_rows",
                "error",
            ]
        )
        for result in results:
            writer.writerow(
                [
                    result.day.isoformat(),
                    result.status,
                    result.downloaded,
                    result.skipped,
                    result.payment_orders,
                    result.archive_files,
                    result.invoice_rows,
                    result.google_rows,
                    result.cash_rows,
                    result.payment_rows,
                    result.error,
                ]
            )
    return path


def print_summary(results: list[DayResult], report_path: Path, output: Path, sheet_name: str) -> None:
    ok = [result for result in results if result.status == "ok"]
    errors = [result for result in results if result.status != "ok"]
    print("days_ok=", len(ok))
    print("days_error=", len(errors))
    print("downloaded=", sum(result.downloaded for result in ok))
    print("payment_orders=", sum(result.payment_orders for result in ok))
    print("archive_files=", sum(result.archive_files for result in ok))
    print("invoice_rows=", sum(result.invoice_rows for result in ok))
    print("google_rows=", sum(result.google_rows for result in ok))
    print("cash_rows=", sum(result.cash_rows for result in ok))
    print("payment_rows=", sum(result.payment_rows for result in ok))
    print("result_workbook=", output)
    print("result_sheet=", sheet_name)
    print("report=", report_path)
    if errors:
        print("error_dates=", ",".join(result.day.isoformat() for result in errors))



def normalize_mode(value: str) -> str:
    mode = (value or "").strip().upper()
    if mode == "PSK":
        return "\u041f\u0421\u041a"
    if mode == "IS":
        return "\u0418\u0421"
    return value or "\u041f\u0421\u041a"

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-04-01")
    parser.add_argument("--end", default="")
    parser.add_argument("--mode", default="")
    parser.add_argument("--local-only", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from datetime import date, datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payment_processor.cash_archive import cash_records_to_payment_records, create_cash_archive_records
from payment_processor.config import load_config, load_rules
from payment_processor.dictionaries import load_dictionaries
from payment_processor.env import load_env
from payment_processor.google_api import build_drive_service, build_sheets_service, get_credentials, load_google_settings
from payment_processor.google_archive import read_archive_records
from payment_processor.google_payments import final_sheet_name_for_mode
from payment_processor.fintablo_client import FinTabloClient, load_fintablo_settings
from payment_processor.fintablo_transactions import fetch_fintablo_payment_records
from payment_processor.history_backfill import BackfillState, run_resumable_days
from payment_processor.history_runner import write_google_history
from payment_processor.invoice_archive import create_invoice_archive_records, invoice_text_operation_records_to_payment_records
from payment_processor.logging_setup import configure_logging
from payment_processor.max_api import (
    MaxApiClient,
    build_client_from_env,
    cash_chat_id_from_env,
    download_chat_files_for_date,
    get_messages_between_dates,
    sort_downloaded_files,
)
from payment_processor.payment_classifier import is_payment_order_pdf
from payment_processor.payment_drive import ensure_payment_file, find_payment_file_link
from payment_processor.payment_history import (
    HistoryIssue,
    apply_mode_defaults,
    build_final_history,
    collect_payment_pdfs,
    dedupe_paths_by_sha256,
    dedupe_payment_records_by_identity,
    parse_payment_history,
    reference_lists_from_dictionaries,
    unmatched_invoice_issues,
    validate_payment_records,
    write_history_issues_csv,
    write_payment_records_csv,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Историческая сборка Архива ПП и Итоговой")
    parser.add_argument("--start", default="2026-04-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--mode", choices=["\u041f\u0421\u041a", "\u0418\u0421", "PSK", "IS"], default="\u041f\u0421\u041a")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--upsert", action="store_true", help="???????? ?????? ?????? ??????? ??? ??????? ???????")
    parser.add_argument("--reset-state", action="store_true")
    parser.add_argument("--staging-root", default=r"C:\tmp\newtech-payment-history")
    parser.add_argument("--payment-source", choices=["max", "fintablo"], default="max", help="???????? ????? ??? ????????; ????? ?? ???????? ?? PDF")
    return parser.parse_args()


def _normalize_mode(value: str) -> str:
    mode = (value or "").strip().upper()
    if mode == "IS":
        return "ИС"
    if mode == "PSK":
        return "ПСК"
    return mode or "ПСК"


def _state_path_for_mode(staging_root: Path, mode: str) -> Path:
    mode_key = (mode or "").strip().upper()
    suffix = "is" if mode_key in {"IS", "\u0418\u0421"} else "psk"
    return staging_root / f"state_{suffix}.json"


def main() -> int:
    args = parse_args()
    start_date = date.fromisoformat(args.start)
    end_date = date.fromisoformat(args.end)
    if end_date < start_date:
        raise ValueError("Дата окончания раньше даты начала")

    config = load_config()
    rules = load_rules()
    dictionaries = load_dictionaries(prefer_google=not args.dry_run)
    references = reference_lists_from_dictionaries(dictionaries)
    env = load_env()
    mode = _normalize_mode(args.mode)
    root = Path(config["root_folder"])
    staging_root = Path(args.staging_root)
    payment_staging = staging_root / "payments"
    reports = ROOT / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    state_path = _state_path_for_mode(staging_root, mode)
    if args.reset_state and state_path.exists():
        state_path.unlink()
    state = BackfillState.load(state_path)
    logger = logging.getLogger("payment_history_backfill")
    configure_logging()

    client, chat_id, count = build_client_from_env(env, mode)
    if not args.skip_download:
        def download_day(day: date) -> dict[str, int]:
            folder_name = day.strftime(config["date_folder_format"]) + config["modes"][mode].get("folder_suffix", "")
            payment_dir = payment_staging / folder_name
            inbox = staging_root / "inbox" / folder_name
            other_dir = staging_root / "other" / folder_name
            summary = download_chat_files_for_date(client, chat_id, inbox, day, count=count)
            payment_files, other_files = sort_downloaded_files(
                summary.downloaded,
                payment_dir,
                other_dir,
                is_payment_order_pdf,
            )
            details = {
                "downloaded": len(summary.downloaded),
                "skipped": len(summary.skipped),
                "payment_files": len(payment_files),
                "other_files": len(other_files),
                "missing_urls": len(summary.no_url),
            }
            print(day.isoformat(), details, flush=True)
            return details

        run_resumable_days(start_date, end_date, state, download_day)

    print("Чтение истории основного MAX-чата...", flush=True)
    invoice_messages = get_messages_between_dates(client, chat_id, start_date, end_date, count=count)
    cash_chat_id = cash_chat_id_from_env(env, mode)
    if not cash_chat_id:
        raise RuntimeError("Не найден MAX_CASH_CHAT_ID")
    print("Чтение истории чата налички...", flush=True)
    cash_messages = get_messages_between_dates(
        MaxApiClient(env["MAX_BOT_TOKEN"]),
        cash_chat_id,
        start_date,
        end_date,
        count=count,
    )

    paths = _filter_paths_for_mode(collect_payment_pdfs(payment_staging, start_date, end_date), mode)
    unique_paths, duplicates = dedupe_paths_by_sha256(paths)
    print(f"PDF найдено: {len(paths)}, уникальных SHA-256: {len(unique_paths)}", flush=True)
    payment_records, parse_issues = parse_payment_history(unique_paths, rules)

    google_settings = load_google_settings(env)
    credentials = get_credentials(google_settings)
    sheets = build_sheets_service(credentials)
    drive = build_drive_service(credentials)
    payment_root_id = env.get(
        "GOOGLE_PAYMENT_ROOT_FOLDER_ID",
        "1jB4mkAxrfykCC_N5BO4P-jx0QSEsiQhX",
    ).strip()
    payment_folder_cache = {}
    payment_file_cache = {}
    source_path_by_record_id: dict[int, Path] = {}
    if not parse_issues and len(unique_paths) == len(payment_records):
        for file_path, record in zip(unique_paths, payment_records):
            source_path_by_record_id[id(record)] = file_path
            if not record.date:
                continue
            payment_date = date.fromisoformat(record.date)
            mode = "ИС" if file_path.parent.name.upper().endswith(" ИС") else "ПСК"
            record.invoice_link = find_payment_file_link(
                drive,
                payment_root_id,
                file_path,
                payment_date,
                mode,
                payment_folder_cache,
                payment_file_cache,
            )

    raw_payment_record_count = len(payment_records)
    payment_records = dedupe_payment_records_by_identity(payment_records)
    semantic_duplicates = raw_payment_record_count - len(payment_records)
    final_payment_records = payment_records
    fintablo_payment_count = 0
    if args.payment_source == "fintablo":
        final_payment_records = dedupe_payment_records_by_identity(
            fetch_fintablo_payment_records(
                FinTabloClient(load_fintablo_settings(env)),
                start_date,
                end_date,
            )
        )
        fintablo_payment_count = len(final_payment_records)
    if not args.dry_run:
        for record in payment_records:
            if record.invoice_link or not record.date:
                continue
            file_path = source_path_by_record_id.get(id(record))
            if file_path is None:
                continue
            payment_date = date.fromisoformat(record.date)
            mode = "ИС" if file_path.parent.name.upper().endswith(" ИС") else "ПСК"
            record.invoice_link = ensure_payment_file(
                drive,
                payment_root_id,
                file_path,
                payment_date,
                mode,
                payment_folder_cache,
                payment_file_cache,
            )
    linked_payments = sum(1 for record in payment_records if record.invoice_link)
    missing_payment_links = sum(1 for record in payment_records if not record.invoice_link)
    invoice_records = read_archive_records(
        sheets,
        google_settings.archive_spreadsheet_id,
        google_settings.archive_sheet_name,
    )

    message_archive_records = create_invoice_archive_records(
        messages=invoice_messages,
        mode=mode,
        chat_id=chat_id,
        dictionaries=dictionaries,
        reference_lists=references,
    )
    direct_records = invoice_text_operation_records_to_payment_records(message_archive_records)
    cash_archive_records = create_cash_archive_records(
        messages=cash_messages,
        chat_id=cash_chat_id,
        dictionaries=dictionaries,
        reference_lists=references,
    )
    cash_records = cash_records_to_payment_records(cash_archive_records)
    final_records, matched_count = build_final_history(
        final_payment_records,
        invoice_records,
        cash_records,
        direct_records,
    )
    final_records = apply_mode_defaults(final_records, mode)

    duplicate_issues = [
        HistoryIssue(str(first), "duplicate_pdf_sha256", details=" | ".join(str(path) for path in copies))
        for first, copies in duplicates.items()
    ]
    issues = [
        *parse_issues,
        *validate_payment_records(payment_records),
        *validate_payment_records(final_payment_records),
        *unmatched_invoice_issues(final_payment_records, invoice_records),
        *duplicate_issues,
    ]

    payment_csv = reports / "payment_archive_full.csv"
    final_csv = reports / "payment_final_full.csv"
    issues_csv = reports / "payment_history_issues.csv"
    summary_json = reports / "payment_history_summary.json"
    write_payment_records_csv(payment_csv, payment_records)
    write_payment_records_csv(final_csv, final_records)
    write_history_issues_csv(issues_csv, issues)

    issue_counts = Counter(issue.issue_type for issue in issues)
    summary = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "invoice_chat_messages": len(invoice_messages),
        "cash_chat_messages": len(cash_messages),
        "pdf_paths": len(paths),
        "unique_pdf_sha256": len(unique_paths),
        "payment_records": len(payment_records),
        "payment_source": args.payment_source,
        "fintablo_payment_records": fintablo_payment_count,
        "semantic_duplicates": semantic_duplicates,
        "payment_links": linked_payments,
        "missing_payment_links": missing_payment_links,
        "matched_invoices": matched_count,
        "direct_operations": len(direct_records),
        "cash_operations": len(cash_records),
        "final_records": len(final_records),
        "issues": dict(sorted(issue_counts.items())),
        "dry_run": bool(args.dry_run),
        "staging_root": str(staging_root),
        "mode": mode,
        "final_sheet": final_sheet_name_for_mode(mode),
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"Отчеты: {payment_csv}, {final_csv}, {issues_csv}", flush=True)

    if parse_issues:
        print("Google не обновлен: есть ошибки чтения PDF.", flush=True)
        return 2

    payment_count, final_count = write_google_history(
        sheets,
        google_settings.archive_spreadsheet_id,
        payment_records,
        final_records,
        dry_run=args.dry_run,
        final_sheet_name=final_sheet_name_for_mode(mode),
        replace_payment_archive=(mode == "ПСК"),
        upsert=args.upsert,
    )
    if args.dry_run:
        print("DRY RUN: Google-листы не изменялись.", flush=True)
    else:
        print(f"Google \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d: \u0410\u0440\u0445\u0438\u0432 \u041f\u041f={payment_count}, {final_sheet_name_for_mode(mode)}={final_count}", flush=True)
    return 0


def _filter_paths_for_mode(paths: list[Path], mode: str) -> list[Path]:
    is_mode = (mode or "").strip().upper() == "ИС"
    result = []
    for path in paths:
        folder_is = path.parent.name.upper().endswith(" ИС")
        if folder_is == is_mode:
            result.append(path)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

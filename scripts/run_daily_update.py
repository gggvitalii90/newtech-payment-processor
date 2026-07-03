from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payment_processor.config import load_config
from payment_processor.dictionaries import load_dictionaries
from payment_processor.env import load_env
from payment_processor.google_api import build_drive_service, build_sheets_service, extract_google_id, get_credentials, load_google_settings
from payment_processor.google_archive import append_archive_records, read_archive_records
from payment_processor.google_payments import FINAL_IS_SHEET_NAME, FINAL_SHEET_NAME
from payment_processor.invoice_drive_lifecycle import archive_paid_invoice, migrate_legacy_review_folder
from payment_processor.invoice_archive import InvoiceArchiveRecord
from payment_processor.models import PaymentRecord
from payment_processor.telegram_notify import format_update_notification, send_telegram_message


def build_daily_commands(day: date, staging_root: Path, dry_run: bool, payment_source: str = "max") -> list[list[str]]:
    return build_period_commands(day, day, staging_root, dry_run, payment_source)


def build_period_commands(start: date, end: date, staging_root: Path, dry_run: bool, payment_source: str = "max") -> list[list[str]]:
    start_text = start.isoformat()
    end_text = end.isoformat()
    commands: list[list[str]] = []
    for mode in ("\u041f\u0421\u041a", "\u0418\u0421"):
        command = [sys.executable, "scripts/backfill_max_archive.py", "--start", start_text, "--end", end_text]
        if dry_run:
            command.append("--local-only")
        command.extend(["--mode", mode])
        commands.append(command)
    for mode in ("PSK", "IS"):
        command = [
            sys.executable, "scripts/backfill_payment_history.py", "--start", start_text, "--end", end_text,
            "--mode", mode, "--upsert", "--staging-root", str(staging_root),
            "--payment-source", payment_source,
        ]
        if dry_run:
            command.append("--dry-run")
        commands.append(command)
    return commands



def find_confirmed_final_payment(
    invoice: InvoiceArchiveRecord,
    final_records: list[PaymentRecord],
    day: date,
) -> PaymentRecord | None:
    invoice_file_id = extract_google_id(invoice.google_drive_link)
    if not invoice_file_id:
        return None
    matches = [
        record for record in final_records
        if _same_day(record.date, day)
        and extract_google_id(record.invoice_link) == invoice_file_id
        and _norm_invoice(record.invoice_number) == _norm_invoice(invoice.invoice_number)
    ]
    return matches[0] if len(matches) == 1 else None


def finalize_drive(day: date) -> dict:
    env = load_env()
    settings = load_google_settings(env)
    credentials = get_credentials(settings)
    sheets = build_sheets_service(credentials)
    drive = build_drive_service(credentials)
    dictionaries = load_dictionaries(prefer_google=True)
    migration = migrate_legacy_review_folder(drive, settings.archive_root_folder_id)
    invoices = read_archive_records(sheets, settings.archive_spreadsheet_id, settings.archive_sheet_name)
    final_records = [
        *_read_final_records(sheets, settings.archive_spreadsheet_id, FINAL_SHEET_NAME, day),
        *_read_final_records(sheets, settings.archive_spreadsheet_id, FINAL_IS_SHEET_NAME, day),
    ]
    paid_records = []
    confirmed_pairs = []
    for invoice in invoices:
        payment = find_confirmed_final_payment(invoice, final_records, day)
        if payment is None:
            continue
        invoice.payment_status = "\u041e\u043f\u043b\u0430\u0447\u0435\u043d"
        paid_records.append(invoice)
        confirmed_pairs.append((invoice, payment))
    if paid_records:
        append_archive_records(
            sheets, settings.archive_spreadsheet_id, settings.archive_sheet_name, paid_records,
        )
    moves = [
        archive_paid_invoice(
            drive, settings.archive_root_folder_id, invoice, payment, True, dictionaries, today=day,
        )
        for invoice, payment in confirmed_pairs
    ]
    return {
        "legacy_review": migration,
        "confirmed_final_rows": len(final_records),
        "paid_invoices": len(paid_records),
        "moved": sum(item.get("status") == "moved" for item in moves),
        "already_archived": sum(item.get("status") == "already_in_destination" for item in moves),
        "move_results": moves,
    }


def _read_final_records(sheets, spreadsheet_id: str, sheet_name: str, day: date) -> list[PaymentRecord]:
    rows = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{sheet_name}'!A2:N",
    ).execute().get("values", [])
    return [PaymentRecord.from_row(row) for row in rows if len(row) > 1 and _same_day(row[1], day)]


def _same_day(value: str, day: date) -> bool:
    text = (value or "").strip()
    if text == day.isoformat():
        return True
    try:
        return date.fromisoformat(text) == day
    except ValueError:
        pass
    for fmt in ("%d.%m.%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date() == day
        except ValueError:
            continue
    return False


def _norm_invoice(value: str) -> str:
    return "".join((value or "").lower().replace("\u2116", "").split()).strip(".,")

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="?????? ?????????? ??????? ? ???????? ?????? ?? ???? ??? ??????")
    parser.add_argument("--date", default="", help="???? ???? YYYY-MM-DD; ??????????? ??? ?????????????")
    parser.add_argument("--start", default="", help="?????? ??????? YYYY-MM-DD")
    parser.add_argument("--end", default="", help="????? ??????? YYYY-MM-DD")
    parser.add_argument("--staging-root", default=r"C:\tmp\newtech-payment-history")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--payment-source", choices=["max", "fintablo"], default="max")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start, end = _selected_period(args)
    report = {"start_date": start.isoformat(), "end_date": end.isoformat(), "dry_run": args.dry_run, "payment_source": args.payment_source, "steps": []}
    exit_code = 0
    for command in build_period_commands(start, end, Path(args.staging_root), args.dry_run, args.payment_source):
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        report["steps"].append({
            "command": command[1:], "returncode": result.returncode,
            "stdout": stdout[-4000:], "stderr": stderr[-4000:],
        })
        if result.returncode != 0:
            report["status"] = "error"
            exit_code = result.returncode
            break
    if exit_code == 0:
        if args.dry_run:
            report["status"] = "dry_run_ok"
        else:
            report["drive_lifecycle"] = finalize_drive(end)
            report["status"] = "ok"
    _write_report(start, end, report)
    if not args.no_telegram:
        _send_telegram_report(report)
    return exit_code


def _selected_period(args: argparse.Namespace) -> tuple[date, date]:
    start_text = args.start or args.date or date.today().isoformat()
    end_text = args.end or args.date or start_text
    start = date.fromisoformat(start_text)
    end = date.fromisoformat(end_text)
    if end < start:
        raise SystemExit("--end ?? ????? ???? ?????? --start")
    return start, end


def _send_telegram_report(report: dict) -> bool:
    env = load_env()
    settings = load_google_settings(env)
    message = format_update_notification(report, settings.archive_spreadsheet_id)
    return send_telegram_message(env, message)


def _write_report(start: date, end: date, report: dict) -> None:
    suffix = start.isoformat() if start == end else f"{start.isoformat()}_{end.isoformat()}"
    path = ROOT / "reports" / f"daily_update_{suffix}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payment_processor.env import load_env
from payment_processor.fintablo_client import FinTabloClient, load_fintablo_settings
from payment_processor.google_api import build_sheets_service, get_credentials, load_google_settings
from payment_processor.google_payments import FINAL_IS_SHEET_NAME, FINAL_SHEET_NAME
from payment_processor.models import PaymentRecord
from scripts.fintablo_fill_deals_directions import (
    ManualLine,
    amount,
    by_id,
    by_name,
    current_stage_ids,
    find_manual,
    normalize_key,
    payload_from_manual,
)


def u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


PAYMENT_CASH = u("\\u041d\\u0430\\u043b\\u0438\\u0447\\u043d\\u0430\\u044f")
OPERATION_INCOME = u("\\u041f\\u0440\\u0438\\u0445\\u043e\\u0434")
OPERATION_CONVERSION = u("\\u041a\\u043e\\u043d\\u0432\\u0435\\u0440\\u0442\\u0430\\u0446\\u0438\\u044f")
UNALLOCATED_CATEGORY_KEYS = {
    normalize_key(u("\\u041d\\u0435\\u0440\\u0430\\u0437\\u043d\\u0435\\u0441\\u0435\\u043d\\u043d\\u043e\\u0435 \\u0441\\u043f\\u0438\\u0441\\u0430\\u043d\\u0438\\u0435")),
    normalize_key(u("\\u041d\\u0435\\u0440\\u0430\\u0437\\u043d\\u0435\\u0441\\u0435\\u043d\\u043d\\u043e\\u0435 \\u043f\\u043e\\u0441\\u0442\\u0443\\u043f\\u043b\\u0435\\u043d\\u0438\\u0435")),
}


@dataclass
class SyncResult:
    transactions: int = 0
    final_rows: int = 0
    noncash_updates: int = 0
    noncash_updated: int = 0
    noncash_no_match: int = 0
    noncash_no_payload: int = 0
    cash_final_rows: int = 0
    cash_existing: int = 0
    cash_missing: int = 0
    cash_created: int = 0
    cash_skipped: int = 0
    cash_errors: int = 0
    noncash_errors: int = 0
    errors: int = 0
    check_items: list[dict[str, Any]] | None = None

    def as_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        if data.get("check_items") is None:
            data["check_items"] = []
        return data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync daily Google final rows into FinTablo")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD or DD.MM.YYYY")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD or DD.MM.YYYY")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", default="reports/fintablo_daily_sync.csv")
    return parser.parse_args()


def parse_day(value: str) -> date:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date: {value}")


def display_day(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def read_final_records(start: date, end: date) -> list[PaymentRecord]:
    env = load_env()
    settings = load_google_settings(env)
    sheets = build_sheets_service(get_credentials(settings))
    result: list[PaymentRecord] = []
    for sheet_name in (FINAL_SHEET_NAME, FINAL_IS_SHEET_NAME):
        rows = sheets.spreadsheets().values().get(
            spreadsheetId=settings.archive_spreadsheet_id,
            range=f"'{sheet_name}'!A2:N",
        ).execute().get("values", [])
        for row in rows:
            if len(row) < 2:
                continue
            record = PaymentRecord.from_row(row)
            record_date = parse_day(record.date)
            if start <= record_date <= end:
                result.append(record)
    return result


def manual_line_from_record(record: PaymentRecord) -> ManualLine:
    values = {
        "date": display_day(parse_day(record.date)),
        "operation_type": record.operation_type,
        "payment_type": record.payment_type,
        "bank": record.bank,
        "counterparty": record.counterparty,
        "invoice": record.invoice_number,
        "object": record.object_name,
        "project": record.project,
        "budget": record.budget_item,
        "responsible": record.responsible,
        "purpose": record.purpose,
        "amount": record.amount,
    }
    return ManualLine(source_row={"name": record.name}, values=values)


def operation_group(record: PaymentRecord) -> str:
    key = normalize_key(record.operation_type)
    if key == normalize_key(OPERATION_INCOME):
        return "income"
    if key == normalize_key(OPERATION_CONVERSION):
        return "transfer"
    return "outcome"


def cash_operation_group(record: PaymentRecord, cash_amount: Decimal) -> str:
    """Map a chat cash row to a FinTablo cash-flow group."""
    group = operation_group(record)
    if group == "transfer" and cash_amount != 0:
        return "income" if cash_amount > 0 else "outcome"
    return group

def canonical_cash_date(value: Any) -> str:
    """Return one stable date representation for cash deduplication."""
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return text[:10]


def cash_key_from_record(record: PaymentRecord) -> tuple[str, Decimal, str]:
    return (canonical_cash_date(record.date), amount(record.amount), normalize_key(record.purpose)[:80])


def cash_key_from_tx(tx: dict[str, Any]) -> tuple[str, Decimal, str]:
    return (canonical_cash_date(tx.get("date")), amount(tx.get("value")), normalize_key(tx.get("description") or "")[:80])


def cash_moneybag_id(moneybags: dict[int, dict[str, Any]]) -> int:
    for item in moneybags.values():
        if str(item.get("type") or "").strip() == "nal":
            return int(item.get("id") or 0)
    return 0


def should_update_tx(tx: dict[str, Any], category_by_id: dict[int, dict[str, Any]]) -> bool:
    if tx.get("group") == "transfer":
        return False
    current_category = category_by_id.get(int(tx.get("categoryId") or 0), {})
    current_category_key = normalize_key(current_category.get("name") or "")
    return (
        not tx.get("categoryId")
        or not tx.get("dealId") and not tx.get("directionId")
        or current_category_key in UNALLOCATED_CATEGORY_KEYS
    )


def sync_fintablo(start: date, end: date, *, apply: bool, output: Path) -> SyncResult:
    env = load_env()
    client = FinTabloClient(load_fintablo_settings(env))
    final_records = read_final_records(start, end)
    txs = client.list_transactions(date_from=display_day(start), date_to=display_day(end))
    directions = by_name(client.list_directions())
    deals_list = client.list_deals()
    deals = by_name(deals_list)
    categories_list = client.list_categories()
    categories = by_name(categories_list)
    category_by_id = by_id(categories_list)
    moneybags = by_id(client.list_moneybags())
    stage_ids = current_stage_ids(deals_list)

    result = SyncResult(transactions=len(txs), final_rows=len(final_records), check_items=[])
    report: list[dict[str, Any]] = []

    noncash_lines = [manual_line_from_record(record) for record in final_records if not record.payment_type.startswith(PAYMENT_CASH)]
    for tx in txs:
        moneybag = moneybags.get(int(tx.get("moneybagId") or 0), {})
        if moneybag.get("type") == "nal":
            continue
        if not should_update_tx(tx, category_by_id):
            continue
        line, reason = find_manual(tx, noncash_lines)
        if line is None:
            result.noncash_no_match += 1
            report.append({"kind": "noncash", "id": tx.get("id"), "action": "no_match", "reason": reason, "date": tx.get("date"), "value": tx.get("value"), "description": tx.get("description", "")})
            continue
        payload, notes = payload_from_manual(line, tx, directions, deals, categories, category_by_id, stage_ids)
        action = "update" if payload else "skip_no_payload"
        error = ""
        if payload:
            result.noncash_updates += 1
        elif notes == ["skip_conversion_links"]:
            action = "skip_conversion_links"
        else:
            result.noncash_no_payload += 1
        if apply and payload:
            try:
                client.request_json("PUT", f"/v1/transaction/{tx['id']}", payload=payload)
                result.noncash_updated += 1
                action = "updated"
            except Exception as exc:  # keep the daily report alive and explicit
                result.errors += 1
                result.noncash_errors += 1
                action = "error"
                error = str(exc)
                result.check_items.append({"type": "fintablo_update_error", "id": tx.get("id"), "date": tx.get("date"), "amount": tx.get("value"), "description": tx.get("description", ""), "reason": error})
        report.append({"kind": "noncash", "id": tx.get("id"), "action": action, "reason": reason, "payload": json.dumps(payload, ensure_ascii=False), "notes": ";".join(notes), "error": error, "date": tx.get("date"), "value": tx.get("value"), "description": tx.get("description", "")})

    existing_cash = {cash_key_from_tx(tx) for tx in txs if moneybags.get(int(tx.get("moneybagId") or 0), {}).get("type") == "nal"}
    cash_records = [record for record in final_records if record.payment_type.startswith(PAYMENT_CASH)]
    result.cash_final_rows = len(cash_records)
    cash_account_id = cash_moneybag_id(moneybags)
    for record in cash_records:
        key = cash_key_from_record(record)
        if key in existing_cash:
            result.cash_existing += 1
            report.append({"kind": "cash", "id": "", "action": "already_exists", "reason": "cash_key_exists", "date": record.date, "value": record.amount, "description": record.purpose})
            continue
        cash_amount = amount(record.amount)
        line = manual_line_from_record(record)
        original_group = operation_group(record)
        group = cash_operation_group(record, cash_amount)
        # A chat conversion has only one cash account. FinTablo cannot create a
        # transfer without the second moneybag, so represent it as a cash
        # receipt/expense instead of silently dropping the operation.
        conversion_fallback = original_group == "transfer" and cash_amount != 0
        if conversion_fallback:
            group = "income" if cash_amount > 0 else "outcome"
        fake_tx = {"group": group}
        payload_ids, notes = payload_from_manual(line, fake_tx, directions, deals, categories, category_by_id, stage_ids)
        payload = {
            "value": float(cash_amount),
            "moneybagId": cash_account_id,
            "group": group,
            "description": record.purpose,
            "date": display_day(parse_day(record.date)),
            **payload_ids,
        }
        action = "create"
        error = ""
        if cash_amount <= 0:
            action = "skip_zero_amount"
            result.cash_skipped += 1
            result.check_items.append({"type": "cash_not_created", "date": record.date, "amount": record.amount, "description": record.purpose, "reason": "zero_or_negative_amount"})
        elif not cash_account_id:
            result.cash_missing += 1
            action = "skip_no_cash_moneybag"
            result.cash_skipped += 1
            result.cash_errors += 1
            result.check_items.append({"type": "cash_not_created", "date": record.date, "amount": record.amount, "description": record.purpose, "reason": "cash_moneybag_not_found"})
        elif group == "transfer":
            # Kept for defensive compatibility if a future record supplies a
            # real two-account transfer; one-account chat conversions use the
            # fallback above and reach the normal create path.
            action = "skip_cash_transfer"
            result.cash_missing += 1
            result.cash_skipped += 1
            result.check_items.append({"type": "cash_not_created", "date": record.date, "amount": record.amount, "description": record.purpose, "reason": "cash_transfer_needs_manual_mapping"})
        else:
            result.cash_missing += 1
        if action == "create" and apply:
            try:
                client.request_json("POST", "/v1/transaction", payload=payload)
                result.cash_created += 1
                action = "created"
            except Exception as exc:
                result.errors += 1
                result.cash_errors += 1
                action = "error"
                error = str(exc)
                result.check_items.append({"type": "cash_create_error", "date": record.date, "amount": record.amount, "description": record.purpose, "reason": error})
        report.append({"kind": "cash", "id": "", "action": action, "reason": "missing_cash", "payload": json.dumps(payload, ensure_ascii=False), "notes": ";".join(notes + (["conversion_without_second_moneybag_as_cash_flow"] if conversion_fallback else [])), "error": error, "date": payload["date"], "value": payload["value"], "description": record.purpose})

    output.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in report for key in row}) or ["kind", "action"]
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report)
    return result


def main() -> int:
    args = parse_args()
    start = parse_day(args.start)
    end = parse_day(args.end)
    if end < start:
        raise SystemExit("--end must be >= --start")
    summary = sync_fintablo(start, end, apply=args.apply, output=Path(args.output))
    payload = {"start_date": start.isoformat(), "end_date": end.isoformat(), "apply": args.apply, **summary.as_dict(), "output": args.output}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if summary.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

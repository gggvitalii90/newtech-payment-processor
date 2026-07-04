from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from payment_processor.env import load_env
from payment_processor.fintablo_client import FinTabloClient, load_fintablo_settings
from payment_processor.google_api import load_google_settings, get_credentials, build_sheets_service


def _u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


MONTH_SHEETS = {
    "2026-04": "Апрель 2026",
    "2026-05": "Май 2026",
    "2026-06": "Июнь 2026",
    "2026-07": "Июль 2026",
}
MANUAL_COLUMNS = [
    "№", "Дата", "Тип операции", "Тип оплаты", "Банк", "Контрагент", "Номер счета",
    "Объект", "Проект", "Статья бюджета", "Ответственный", "Назначение платежа", "Ссылка на счет", "Сумма", "Сумма итог",
]
NONCASH = ("Безналичные",)
CASH = "Наличная"


@dataclass
class ManualRow:
    row_number: int
    sheet: str
    values: dict[str, str]

    @property
    def date(self) -> str:
        return normalize_date(self.values.get("Дата", ""))

    @property
    def amount(self) -> Decimal:
        return parse_amount(self.values.get("Сумма", ""))

    @property
    def operation_group(self) -> str:
        operation = norm(self.values.get("Тип операции", ""))
        if "приход" in operation:
            return "income"
        if "конвертац" in operation:
            return "transfer"
        return "outcome"

    @property
    def payment_type(self) -> str:
        return self.values.get("Тип оплаты", "").strip()

    @property
    def invoice(self) -> str:
        return normalize_invoice(self.values.get("Номер счета", ""))

    @property
    def counterparty_key(self) -> str:
        return norm(self.values.get("Контрагент", ""))

    @property
    def purpose_key(self) -> str:
        return norm(self.values.get("Назначение платежа", ""))


def norm(value: Any) -> str:
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"[«»\"'`]+", "", text)
    text = re.sub(r"[^0-9a-zа-я]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compact_key(value: Any) -> str:
    return re.sub(r"[^0-9a-zа-я]+", "", norm(value))



PROJECT_DIRECTION_ALIASES = {
    compact_key(_u("\u0411\u0430\u043d\u043a")): compact_key(_u("\u041e\u0444\u0438\u0441")),
    compact_key(_u("\u041d\u0430\u043b\u043e\u0433\u0438")): compact_key(_u("\u041e\u0444\u0438\u0441")),
    compact_key(_u("\u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0435 \u0440\u0430\u0441\u0445\u043e\u0434\u044b")): compact_key(_u("\u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u043e")),
}


def normalize_date(value: str) -> str:
    text = str(value or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], fmt).strftime("%d.%m.%Y")
        except ValueError:
            pass
    return text


def parse_amount(value: Any) -> Decimal:
    text = str(value or "").replace("\xa0", " ").replace(" ", "").replace(",", ".")
    text = re.sub(r"[^0-9.\-]", "", text)
    try:
        return Decimal(text or "0").quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def normalize_invoice(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"^(?:счет|сч|№|no|n)\s*", "", text)
    return re.sub(r"[^0-9a-zа-я/-]+", "", text)


def extract_invoice(description: str) -> str:
    text = str(description or "")
    patterns = [
        r"счет\s+на\s+оплату\s*(?:№|no|n)?\s*([A-Za-zА-Яа-яЁё0-9_./-]+)",
        r"сч[её]т\s*(?:№|no|n)?\s*([A-Za-zА-Яа-яЁё0-9_./-]+)",
        r"по\s+счету\s*(?:№|no|n)?\s*([A-Za-zА-Яа-яЁё0-9_./-]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return normalize_invoice(m.group(1))
    return ""


def read_manual_rows(start: str, end: str) -> list[ManualRow]:
    env = load_env()
    google_settings = load_google_settings(env)
    sheets = build_sheets_service(get_credentials(google_settings))
    spreadsheet_id = env["GOOGLE_DICTIONARY_SPREADSHEET_ID"]
    start_key = datetime.strptime(start, "%d.%m.%Y").strftime("%Y-%m-%d")
    end_key = datetime.strptime(end, "%d.%m.%Y").strftime("%Y-%m-%d")
    rows: list[ManualRow] = []
    for month_key, sheet in MONTH_SHEETS.items():
        if month_key < start_key[:7] or month_key > end_key[:7]:
            continue
        values = sheets.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"'{sheet}'!A1:O2000").execute().get("values", [])
        if not values:
            continue
        headers = values[0]
        for index, row in enumerate(values[1:], start=2):
            data = {headers[i]: str(row[i]).strip() if i < len(row) else "" for i in range(len(headers))}
            if not data.get("Дата") or not data.get("Сумма"):
                continue
            date_key = datetime.strptime(normalize_date(data["Дата"]), "%d.%m.%Y").strftime("%Y-%m-%d")
            if start_key <= date_key <= end_key:
                rows.append(ManualRow(index, sheet, data))
    return rows


def by_name(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for item in items:
        key = compact_key(item.get("name", ""))
        if key and key not in result:
            result[key] = item
    return result


def stage_key(value: str) -> str:
    key = compact_key(value)
    replacements = {
        compact_key("АР"): compact_key("АР_"),
        compact_key("КЖ"): compact_key("КЖ_"),
        compact_key("КМ (М)"): compact_key("КМ ( М )"),
        compact_key("КМ (ПР)"): compact_key("КМ ( ПР )"),
    }
    return replacements.get(key, key)


def resolve_ids(row: ManualRow, categories, directions, deals) -> tuple[dict[str, int], list[str]]:
    updates: dict[str, int] = {}
    notes: list[str] = []
    budget_key = _u("\u0421\u0442\u0430\u0442\u044c\u044f \u0431\u044e\u0434\u0436\u0435\u0442\u0430")
    project_key_name = _u("\u041f\u0440\u043e\u0435\u043a\u0442")
    object_key_name = _u("\u041e\u0431\u044a\u0435\u043a\u0442")

    category = categories.get(compact_key(row.values.get(budget_key, "")))
    if category:
        updates["categoryId"] = int(category["id"])
    elif row.values.get(budget_key, "").strip():
        notes.append("category_not_found")

    project_key = compact_key(row.values.get(project_key_name, ""))
    direction = directions.get(PROJECT_DIRECTION_ALIASES.get(project_key, project_key))
    if direction:
        updates["directionId"] = int(direction["id"])
    elif row.values.get(project_key_name, "").strip():
        notes.append("direction_not_found")

    deal = deals.get(compact_key(row.values.get(object_key_name, "")))
    if deal:
        if row.operation_group == "income":
            updates["dealId"] = int(deal["id"])
        else:
            project_stage = stage_key(row.values.get(project_key_name, ""))
            stage_id = None
            for stage in deal.get("stages") or []:
                if stage_key(stage.get("name", "")) == project_stage:
                    stage_id = int(stage["id"])
                    break
            if stage_id:
                updates["dealId"] = stage_id
            elif row.values.get(project_key_name, "").strip():
                notes.append("stage_not_found_skipped_deal")
            else:
                updates["dealId"] = int(deal["id"])
    elif row.values.get(object_key_name, "").strip():
        notes.append("deal_not_found")
    return updates, notes


def match_manual_for_tx(tx: dict[str, Any], manual_rows: list[ManualRow]) -> tuple[ManualRow | None, str]:
    tx_date = normalize_date(tx.get("date", ""))
    tx_amount = parse_amount(tx.get("value"))
    tx_invoice = extract_invoice(tx.get("description", ""))
    tx_desc = norm(tx.get("description", ""))
    noncash_rows = [r for r in manual_rows if not r.payment_type.startswith(CASH)]
    candidates = [r for r in noncash_rows if r.date == tx_date and r.amount == tx_amount]
    if tx_invoice:
        invoice_amount = [r for r in noncash_rows if r.amount == tx_amount and r.invoice and r.invoice == tx_invoice]
        if len(invoice_amount) == 1:
            if invoice_amount[0].date == tx_date:
                return invoice_amount[0], "date_amount_invoice"
            return invoice_amount[0], "amount_invoice"
        if len(invoice_amount) > 1:
            same_date = [r for r in invoice_amount if r.date == tx_date]
            if len(same_date) == 1:
                return same_date[0], "date_amount_invoice"
            candidates = same_date or invoice_amount
    if not candidates:
        amount_only = [r for r in noncash_rows if r.amount == tx_amount]
        best_amount = _best_text_match(amount_only, tx_desc)
        if best_amount is not None:
            return best_amount, "amount_text"
        return None, "no_date_amount"
    best = _best_text_match(candidates, tx_desc)
    if best is not None:
        return best, "date_amount_text"
    if len(candidates) == 1:
        return candidates[0], "date_amount_unique"
    return None, "ambiguous"


def _best_text_match(candidates: list[ManualRow], tx_desc: str) -> ManualRow | None:
    best = []
    for row in candidates:
        score = 0
        if row.invoice and row.invoice in tx_desc:
            score += 4
        if row.counterparty_key and row.counterparty_key in tx_desc:
            score += 3
        purpose_words = [w for w in row.purpose_key.split() if len(w) >= 4]
        score += sum(1 for w in purpose_words[:6] if w in tx_desc)
        best.append((score, row))
    best.sort(key=lambda item: item[0], reverse=True)
    if len(best) == 1 and best[0][0] > 0:
        return best[0][1]
    if len(best) > 1 and best[0][0] > best[1][0] and best[0][0] > 0:
        return best[0][1]
    return None


def cash_key(row_or_tx: Any) -> tuple[str, Decimal, str]:
    if isinstance(row_or_tx, ManualRow):
        return (row_or_tx.date, row_or_tx.amount, norm(row_or_tx.values.get("Назначение платежа", ""))[:80])
    return (normalize_date(row_or_tx.get("date", "")), parse_amount(row_or_tx.get("value")), norm(row_or_tx.get("description", ""))[:80])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="29.06.2026")
    parser.add_argument("--end", default="03.07.2026")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--only-category", action="store_true")
    parser.add_argument("--allow-cash-without-category", action="store_true")
    parser.add_argument("--output-dir", default="reports")
    args = parser.parse_args()

    env = load_env()
    client = FinTabloClient(load_fintablo_settings(env))
    manual_rows = read_manual_rows(args.start, args.end)
    transactions = client.list_transactions(date_from=args.start, date_to=args.end)
    categories = by_name(client.list_categories())
    directions = by_name(client.list_directions())
    deals = by_name(client.list_deals())
    moneybags = {int(x.get("id") or 0): x for x in client.list_moneybags()}
    cash_moneybag_id = next((int(x["id"]) for x in moneybags.values() if x.get("type") == "nal" and compact_key(x.get("name")) == compact_key("Наличные")), 0)

    update_rows = []
    for tx in transactions:
        moneybag = moneybags.get(int(tx.get("moneybagId") or 0), {})
        if moneybag.get("type") == "nal":
            continue
        current_missing = not tx.get("categoryId") or not tx.get("dealId") or not tx.get("directionId") or int(tx.get("categoryId") or 0) in (1404181, 1404182)
        if not current_missing:
            continue
        row, reason = match_manual_for_tx(tx, manual_rows)
        if row is None:
            update_rows.append({"transaction_id": tx.get("id"), "date": tx.get("date"), "value": tx.get("value"), "action": "no_match", "reason": reason, "description": tx.get("description", "")})
            continue
        ids, notes = resolve_ids(row, categories, directions, deals)
        if args.only_category:
            ids = {"categoryId": ids["categoryId"]} if "categoryId" in ids else {}
        payload = {k: v for k, v in ids.items() if v and (not tx.get(k) or int(tx.get(k) or 0) in (1404181, 1404182))}
        action = "update" if payload else "skip_no_ids"
        if args.only_category:
            payload = {"categoryId": payload["categoryId"]} if "categoryId" in payload else {}
            action = "update" if payload else "skip_no_category_payload"
        if args.apply and payload:
            client.request_json("PUT", f"/v1/transaction/{tx['id']}", payload=payload)
            action = "updated"
        update_rows.append({
            "transaction_id": tx.get("id"), "date": tx.get("date"), "value": tx.get("value"), "action": action,
            "reason": reason, "payload": json.dumps(payload, ensure_ascii=False), "notes": ";".join(notes),
            "manual_sheet": row.sheet, "manual_row": row.row_number,
            "manual_object": row.values.get("Объект", ""), "manual_project": row.values.get("Проект", ""), "manual_budget": row.values.get("Статья бюджета", ""),
            "description": tx.get("description", ""),
        })

    fintablo_cash_keys = {cash_key(tx) for tx in transactions if moneybags.get(int(tx.get("moneybagId") or 0), {}).get("type") == "nal"}
    cash_rows = [r for r in manual_rows if r.payment_type.startswith(CASH)]
    missing_cash = []
    for row in cash_rows:
        key = cash_key(row)
        if key in fintablo_cash_keys:
            continue
        ids, notes = resolve_ids(row, categories, directions, deals)
        if args.only_category:
            ids = {"categoryId": ids["categoryId"]} if "categoryId" in ids else {}
        payload = {
            "value": float(row.amount),
            "moneybagId": cash_moneybag_id,
            "group": row.operation_group,
            "description": row.values.get("Назначение платежа", ""),
            "date": row.date,
            **ids,
        }
        category = categories.get(compact_key(row.values.get("Статья бюджета", "")))
        category_group_ok = not category or category.get("group") == row.operation_group
        if not category_group_ok:
            payload.pop("categoryId", None)
        can_create_cash = bool(cash_moneybag_id and ("categoryId" in payload or args.allow_cash_without_category))
        if row.operation_group == "transfer" and ("moneybag2Id" not in payload or "value2" not in payload):
            action = "skip_transfer_needs_second_moneybag"
        elif not category_group_ok and not args.allow_cash_without_category:
            action = "skip_wrong_category_group"
        elif args.apply and can_create_cash:
            client.request_json("POST", "/v1/transaction", payload=payload)
            action = "created" if "categoryId" in payload else "created_without_category"
        else:
            if can_create_cash:
                action = "create" if "categoryId" in payload else "create_without_category"
            else:
                action = "skip_no_cash_account_or_category"
        missing_cash.append({
            "date": row.date, "value": str(row.amount), "action": action,
            "payload": json.dumps(payload, ensure_ascii=False), "notes": ";".join(notes),
            "manual_sheet": row.sheet, "manual_row": row.row_number,
            "object": row.values.get("Объект", ""), "project": row.values.get("Проект", ""), "budget": row.values.get("Статья бюджета", ""),
            "purpose": row.values.get("Назначение платежа", ""),
        })

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    updates_path = out / f"fintablo_manual_updates_{args.start.replace('.', '-')}_{args.end.replace('.', '-')}.csv"
    cash_path = out / f"fintablo_missing_cash_{args.start.replace('.', '-')}_{args.end.replace('.', '-')}.csv"
    write_csv(updates_path, update_rows)
    write_csv(cash_path, missing_cash)
    summary = {
        "start": args.start,
        "end": args.end,
        "apply": args.apply,
        "only_category": args.only_category,
        "allow_cash_without_category": args.allow_cash_without_category,
        "manual_rows": len(manual_rows),
        "transactions": len(transactions),
        "updates": len([r for r in update_rows if r.get("action") in ("update", "updated")]),
        "unmatched_updates": len([r for r in update_rows if r.get("action") == "no_match"]),
        "missing_cash": len(missing_cash),
        "updates_path": str(updates_path),
        "cash_path": str(cash_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({k for row in rows for k in row.keys()})
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())







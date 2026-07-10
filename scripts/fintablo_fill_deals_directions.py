
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payment_processor.dictionaries import normalize_key
from payment_processor.env import load_env
from payment_processor.fintablo_client import FinTabloClient, FinTabloError, load_fintablo_settings


def u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


INTERNAL_OBJECTS = {normalize_key(x) for x in [
    u("\\u041f\\u0421\\u041a \\u041d\\u044c\\u044e\\u0442\\u0435\\u043a"),
    u("\\u041f\\u0440\\u043e\\u0438\\u0437\\u0432\\u043e\\u0434\\u0441\\u0442\\u0432\\u043e"),
    u("\\u0410\\u0432\\u0442\\u043e\\u0445\\u043e\\u0437\\u044f\\u0439\\u0441\\u0442\\u0432\\u043e"),
    u("\\u041f\\u0421\\u041a \\u0418\\u0421"),
]}
CONVERSION = normalize_key(u("\\u041a\\u043e\\u043d\\u0432\\u0435\\u0440\\u0442\\u0430\\u0446\\u0438\\u044f"))
OFFICE = u("\\u041e\\u0444\\u0438\\u0441")
PRODUCTION = u("\\u041f\\u0440\\u043e\\u0438\\u0437\\u0432\\u043e\\u0434\\u0441\\u0442\\u0432\\u043e")
BANK_PROJECT = u("\\u0411\\u0430\\u043d\\u043a")
TAX_PROJECT = u("\\u041d\\u0430\\u043b\\u043e\\u0433\\u0438")
PRODUCTION_EXPENSES = u("\\u041f\\u0440\\u043e\\u0438\\u0437\\u0432\\u043e\\u0434\\u0441\\u0442\\u0432\\u0435\\u043d\\u043d\\u044b\\u0435 \\u0440\\u0430\\u0441\\u0445\\u043e\\u0434\\u044b")

FIELD_NAMES = [
    "date", "operation_type", "payment_type", "bank", "counterparty", "invoice", "object", "project",
    "budget", "responsible", "purpose", "amount",
]
STAGE_REPLACEMENTS = {
    normalize_key(u("\\u0410\\u0420")): normalize_key(u("\\u0410\\u0420_")),
    normalize_key(u("\\u041a\\u0416")): normalize_key(u("\\u041a\\u0416_")),
    normalize_key(u("\\u041a\\u041c (\\u041c)")): normalize_key(u("\\u041a\\u041c ( \\u041c )")),
    normalize_key(u("\\u041a\\u041c (\\u041f\\u0420)")): normalize_key(u("\\u041a\\u041c ( \\u041f\\u0420 )")),
}
PROJECT_TO_DIRECTION = {
    normalize_key(BANK_PROJECT): normalize_key(OFFICE),
    normalize_key(TAX_PROJECT): normalize_key(OFFICE),
    normalize_key(PRODUCTION_EXPENSES): normalize_key(PRODUCTION),
}
UNALLOCATED_CATEGORIES = {
    normalize_key(u("\\u041d\\u0435\\u0440\\u0430\\u0437\\u043d\\u0435\\u0441\\u0435\\u043d\\u043d\\u043e\\u0435 \\u0441\\u043f\\u0438\\u0441\\u0430\\u043d\\u0438\\u0435")),
    normalize_key(u("\\u041d\\u0435\\u0440\\u0430\\u0437\\u043d\\u0435\\u0441\\u0435\\u043d\\u043d\\u043e\\u0435 \\u043f\\u043e\\u0441\\u0442\\u0443\\u043f\\u043b\\u0435\\u043d\\u0438\\u0435")),
}


@dataclass
class ManualLine:
    source_row: dict[str, str]
    values: dict[str, str]

    @property
    def date(self) -> str:
        return self.values.get("date", "")

    @property
    def amount(self) -> Decimal:
        return amount(self.values.get("amount"))

    @property
    def invoice(self) -> str:
        return norm_invoice(self.values.get("invoice", ""))

    @property
    def purpose_key(self) -> str:
        return normalize_key(self.values.get("purpose", ""))


def amount(value: Any) -> Decimal:
    text = str(value or "").replace("\xa0", " ").replace(" ", "").replace(",", ".")
    text = re.sub(r"[^0-9.\-]", "", text)
    try:
        return Decimal(text or "0").quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")



def norm_invoice(value: Any) -> str:
    text = str(value or "").lower().replace(u("\\u0451"), u("\\u0435"))
    text = re.sub(u(r"^(?:\\u0441\\u0447\\u0435\\u0442|\\u0441\\u0447|\\u2116|no|n)\\s*"), "", text)
    return re.sub(u(r"[^0-9a-z\\u0430-\\u044f/-]+"), "", text)


def extract_invoice(description: str) -> str:
    patterns = [
        u(r"\\u0441\\u0447\\u0435\\u0442\\s+\\u043d\\u0430\\s+\\u043e\\u043f\\u043b\\u0430\\u0442\\u0443\\s*(?:\\u2116|no|n)?\\s*([A-Za-z\\u0410-\\u042f\\u0430-\\u044f\\u0401\\u04510-9_./-]+)"),
        u(r"\\u0441\\u0447[\\u0435\\u0451]\\u0442\\s*(?:\\u2116|no|n)?\\s*([A-Za-z\\u0410-\\u042f\\u0430-\\u044f\\u0401\\u04510-9_./-]+)"),
        u(r"\\u043f\\u043e\\s+\\u0441\\u0447\\u0435\\u0442\\u0443\\s*(?:\\u2116|no|n)?\\s*([A-Za-z\\u0410-\\u042f\\u0430-\\u044f\\u0401\\u04510-9_./-]+)"),
    ]
    for pattern in patterns:
        match = re.search(pattern, str(description or ""), flags=re.IGNORECASE)
        if match:
            return norm_invoice(match.group(1))
    return ""


def stage_key(value: str) -> str:
    key = normalize_key(value)
    return STAGE_REPLACEMENTS.get(key, key)


def target_direction_key(project: str) -> str:
    key = normalize_key(project)
    return PROJECT_TO_DIRECTION.get(key, key)


def by_name(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        key = normalize_key(str(item.get("name") or ""))
        if key and key not in result:
            result[key] = item
    return result


def by_id(items: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result = {}
    for item in items:
        try:
            result[int(item.get("id") or 0)] = item
        except (TypeError, ValueError):
            pass
    return result


def parse_manual_string(text: str) -> dict[str, str]:
    parts = [part.strip() for part in str(text or "").split("|")]
    values = dict(zip(FIELD_NAMES, parts))
    for name in FIELD_NAMES:
        values.setdefault(name, "")
    return values


def load_manual_lines(path: Path) -> list[ManualLine]:
    rows: list[ManualLine] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for raw in csv.DictReader(f):
            if raw.get("status") not in {"matched_exact", "matched_with_diffs"}:
                continue
            manual = parse_manual_string(raw.get("manual", ""))
            if not manual.get("date") or not manual.get("amount"):
                continue
            rows.append(ManualLine(raw, manual))
    return rows


def score_manual(tx: dict[str, Any], line: ManualLine) -> int:
    if str(tx.get("date") or "") != line.date:
        return -1
    if amount(tx.get("value")) != line.amount:
        return -1
    score = 10
    tx_invoice = extract_invoice(str(tx.get("description") or ""))
    if tx_invoice and line.invoice and tx_invoice == line.invoice:
        score += 20
    tx_desc = normalize_key(tx.get("description") or "")
    for word in line.purpose_key.split()[:8]:
        if len(word) >= 4 and word in tx_desc:
            score += 1
    return score


def find_manual(tx: dict[str, Any], lines: list[ManualLine]) -> tuple[ManualLine | None, str]:
    scored = [(score_manual(tx, line), line) for line in lines]
    scored = [(score, line) for score, line in scored if score >= 10]
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return None, "no_manual_match"
    if len(scored) == 1 or scored[0][0] > scored[1][0]:
        return scored[0][1], f"score_{scored[0][0]}"
    return None, "ambiguous_manual_match"


def current_stage_ids(deals: list[dict[str, Any]]) -> set[int]:
    ids: set[int] = set()
    for deal in deals:
        for stage in deal.get("stages") or []:
            try:
                ids.add(int(stage.get("id") or 0))
            except (TypeError, ValueError):
                pass
    return ids


def category_update(line: ManualLine, tx: dict[str, Any], categories: dict[str, dict[str, Any]], category_by_id: dict[int, dict[str, Any]]) -> tuple[dict[str, int], list[str]]:
    payload: dict[str, int] = {}
    notes: list[str] = []
    target = line.values.get("budget", "").strip()
    if not target:
        return payload, notes
    category = categories.get(normalize_key(target))
    if not category:
        notes.append("category_not_found")
        return payload, notes
    current = category_by_id.get(int(tx.get("categoryId") or 0), {})
    current_key = normalize_key(current.get("name") or "")
    if str(category.get("group") or "") and str(tx.get("group") or "") and str(category.get("group")) != str(tx.get("group")):
        notes.append("category_group_mismatch_skipped")
        return payload, notes
    target_id = int(category["id"])
    if int(tx.get("categoryId") or 0) != target_id and (not current_key or current_key in UNALLOCATED_CATEGORIES):
        payload["categoryId"] = target_id
    return payload, notes


def payload_from_manual(
    line: ManualLine,
    tx: dict[str, Any],
    directions: dict[str, dict[str, Any]],
    deals: dict[str, dict[str, Any]],
    categories: dict[str, dict[str, Any]],
    category_by_id: dict[int, dict[str, Any]],
    stage_ids: set[int],
) -> tuple[dict[str, int], list[str]]:
    values = line.values
    notes: list[str] = []
    payload, cat_notes = category_update(line, tx, categories, category_by_id)
    notes.extend(cat_notes)

    operation_key = normalize_key(values.get("operation_type"))
    object_key = normalize_key(values.get("object"))
    project = values.get("project", "")
    project_key = normalize_key(project)
    if operation_key == CONVERSION or object_key == CONVERSION or project_key == CONVERSION:
        return {}, ["skip_conversion"]

    direction = directions.get(target_direction_key(project))
    deal = deals.get(object_key)
    is_income = str(tx.get("group") or "") == "income" or normalize_key(values.get("operation_type")) == normalize_key(u("\\u041f\\u0440\\u0438\\u0445\\u043e\\u0434"))

    if object_key in INTERNAL_OBJECTS:
        if direction:
            target = int(direction["id"])
            if int(tx.get("directionId") or 0) != target:
                payload["directionId"] = target
        elif project.strip():
            notes.append("direction_not_found_for_internal_object")
        return payload, notes

    if deal:
        target_deal_id = int(deal["id"])
        if not is_income:
            target_stage = stage_key(project)
            stage_id = None
            stages = deal.get("stages") or []
            for stage in stages:
                if stage_key(str(stage.get("name") or "")) == target_stage:
                    stage_id = int(stage["id"])
                    break
            if stage_id:
                target_deal_id = stage_id
            elif stages:
                notes.append("stage_not_found_skipped_deal" if project.strip() else "stage_required_skipped_deal")
                return payload, notes
        else:
            # FinTablo accepts income on the base deal, not on a deal stage.
            pass
        current_deal_id = int(tx.get("dealId") or 0)
        if current_deal_id != target_deal_id:
            payload["dealId"] = target_deal_id
        return payload, notes

    if direction:
        target = int(direction["id"])
        if int(tx.get("directionId") or 0) != target:
            payload["directionId"] = target
    elif project.strip():
        notes.append("deal_and_direction_not_found")
    return payload, notes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manual", default="reports/manual_matching_current_2026-04-10_2026-06-30_after_cash_conversion_fix.csv")
    parser.add_argument("--start", default="01.04.2026")
    parser.add_argument("--end", default="03.07.2026")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", default="reports/fintablo_classification_audit.csv")
    args = parser.parse_args()

    manual_lines = load_manual_lines(Path(args.manual))
    client = FinTabloClient(load_fintablo_settings(load_env()))
    txs = client.list_transactions(date_from=args.start, date_to=args.end)
    directions = by_name(client.list_directions())
    deals_list = client.list_deals()
    deals = by_name(deals_list)
    categories_list = client.list_categories()
    categories = by_name(categories_list)
    category_by_id = by_id(categories_list)
    stage_ids = current_stage_ids(deals_list)

    report: list[dict[str, Any]] = []
    errors = 0
    for tx in txs:
        if tx.get("group") == "transfer":
            report.append({"id": tx.get("id"), "date": tx.get("date"), "value": tx.get("value"), "action": "skip", "reason": "skip_conversion_transfer", "description": tx.get("description", "")})
            continue
        line, reason = find_manual(tx, manual_lines)
        if line is None:
            report.append({"id": tx.get("id"), "date": tx.get("date"), "value": tx.get("value"), "action": "skip", "reason": reason, "description": tx.get("description", "")})
            continue
        payload, notes = payload_from_manual(line, tx, directions, deals, categories, category_by_id, stage_ids)
        action = "update" if payload else "skip_no_payload"
        error = ""
        if args.apply and payload:
            try:
                client.request_json("PUT", f"/v1/transaction/{tx['id']}", payload=payload)
                action = "updated"
            except FinTabloError as exc:
                action = "error"
                error = str(exc)
                errors += 1
        report.append({
            "id": tx.get("id"), "date": tx.get("date"), "value": tx.get("value"), "action": action, "reason": reason,
            "payload": json.dumps(payload, ensure_ascii=False), "notes": ";".join(notes), "error": error,
            "manual_object": line.values.get("object", ""), "manual_project": line.values.get("project", ""),
            "manual_budget": line.values.get("budget", ""), "manual_operation": line.values.get("operation_type", ""),
            "description": tx.get("description", ""),
        })

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({k for row in report for k in row})
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report)
    summary = {
        "apply": args.apply,
        "transactions": len(txs),
        "manual_lines": len(manual_lines),
        "updates": sum(1 for r in report if r["action"] in {"update", "updated"}),
        "updated": sum(1 for r in report if r["action"] == "updated"),
        "errors": errors,
        "skips": sum(1 for r in report if str(r["action"]).startswith("skip")),
        "output": str(out),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

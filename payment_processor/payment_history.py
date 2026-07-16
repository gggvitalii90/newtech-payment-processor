from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Callable, Iterable

from .google_payments import FINAL_COLUMNS
from .invoice_archive import InvoiceArchiveRecord, enrich_payment_records_from_archive
from .models import PaymentRecord
from .parser import parse_payment_pdf


FOLDER_DATE_RE = re.compile(r"^(\d{4}[.]\d{2}[.]\d{2})(?: \u0418\u0421)?$")
REQUIRED_PAYMENT_FIELDS = (
    ("\u0414\u0430\u0442\u0430", "date"),
    ("\u0422\u0438\u043f \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0438", "operation_type"),
    ("\u0422\u0438\u043f \u043e\u043f\u043b\u0430\u0442\u044b", "payment_type"),
    ("\u0411\u0430\u043d\u043a", "bank"),
    ("\u041a\u043e\u043d\u0442\u0440\u0430\u0433\u0435\u043d\u0442", "counterparty"),
    ("\u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u043f\u043b\u0430\u0442\u0435\u0436\u0430", "purpose"),
    ("\u0421\u0443\u043c\u043c\u0430", "amount"),
)


@dataclass(frozen=True)
class HistoryIssue:
    source: str
    issue_type: str
    fields: tuple[str, ...] = ()
    details: str = ""


def reference_lists_from_dictionaries(dictionaries: dict) -> dict[str, list[str]]:
    """Build regulated reference lists from dictionary values."""
    result: dict[str, list[str]] = {}
    list_sources = {
        "\u0422\u0438\u043f \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0438": "operation_types",
        "\u0422\u0438\u043f \u043e\u043f\u043b\u0430\u0442\u044b": "payment_types",
        "\u0411\u0430\u043d\u043a": "banks",
    }
    dict_sources = {
        "\u041e\u0431\u044a\u0435\u043a\u0442": "objects",
        "\u041f\u0440\u043e\u0435\u043a\u0442": "projects",
        "\u0421\u0442\u0430\u0442\u044c\u044f \u0431\u044e\u0434\u0436\u0435\u0442\u0430": "budget_items",
        "\u041e\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0439": "responsibles",
    }
    for column, key in list_sources.items():
        values = dictionaries.get(key, [])
        if values:
            result[column] = list(values)
    for column, key in dict_sources.items():
        values = dictionaries.get(key, {})
        if values:
            result[column] = list(values)
    return result

def collect_payment_pdfs(root: Path, start_date: date, end_date: date) -> list[Path]:
    paths: list[Path] = []
    if not root.exists():
        return paths
    for folder in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if not folder.is_dir():
            continue
        match = FOLDER_DATE_RE.fullmatch(folder.name)
        if not match:
            continue
        folder_date = date.fromisoformat(match.group(1).replace(".", "-"))
        if not start_date <= folder_date <= end_date:
            continue
        paths.extend(sorted(
            (path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"),
            key=lambda item: item.name.casefold(),
        ))
    return paths


def dedupe_paths_by_sha256(paths: Iterable[Path]) -> tuple[list[Path], dict[Path, list[Path]]]:
    first_by_hash: dict[str, Path] = {}
    duplicates: dict[Path, list[Path]] = {}
    unique: list[Path] = []
    for path in paths:
        digest = _sha256(path)
        first = first_by_hash.get(digest)
        if first is None:
            first_by_hash[digest] = path
            unique.append(path)
        else:
            duplicates.setdefault(first, []).append(path)
    return unique, duplicates


def dedupe_payment_records_by_identity(records: Iterable[PaymentRecord]) -> list[PaymentRecord]:
    by_key: dict[tuple[str, ...], PaymentRecord] = {}
    order: list[tuple[str, ...]] = []
    for record in records:
        key = payment_record_identity(record)
        current = by_key.get(key)
        if current is None:
            by_key[key] = record
            order.append(key)
        elif record.invoice_link and not current.invoice_link:
            by_key[key] = record
    return [by_key[key] for key in order]


def payment_record_identity(record: PaymentRecord) -> tuple[str, ...]:
    return (
        _payment_document_number(record.name),
        _identity_text(record.date),
        _identity_text(record.operation_type),
        _identity_text(record.payment_type),
        _identity_text(record.bank),
        _identity_text(record.counterparty),
        _identity_text(record.invoice_number),
        _identity_text(record.purpose),
        _identity_text(record.amount),
    )


def _payment_document_number(file_name: str) -> str:
    normalized = _identity_text(file_name)
    match = re.search(r"(?:\u2116|no[.]?)\s*(\d+)", normalized, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"_\d{2}[.]\d{2}[.]\d{4}_(\d+)(?:_|[.])", normalized)
    if match:
        return match.group(1)
    match = re.search(r"_(\d+)[.]pdf$", normalized)
    return match.group(1) if match else normalized


def _identity_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold().replace("\u0451", "\u0435"))


def _u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")

def parse_payment_history(
    paths: Iterable[Path],
    rules: dict,
    parse_func: Callable[[Path, dict], PaymentRecord] = parse_payment_pdf,
) -> tuple[list[PaymentRecord], list[HistoryIssue]]:
    records: list[PaymentRecord] = []
    issues: list[HistoryIssue] = []
    for path in paths:
        try:
            records.append(parse_func(path, rules))
        except Exception as exc:
            issues.append(HistoryIssue(str(path), "payment_parse_error", details=str(exc)))
    return records, issues


def build_final_history(
    payment_records: Iterable[PaymentRecord],
    invoice_records: Iterable[InvoiceArchiveRecord],
    cash_records: Iterable[PaymentRecord],
    direct_records: Iterable[PaymentRecord],
) -> tuple[list[PaymentRecord], int]:
    payments = [replace(record, invoice_link="") for record in payment_records]
    matched_count = enrich_payment_records_from_archive(payments, list(invoice_records))
    combined = [*payments, *(replace(record) for record in direct_records), *(replace(record) for record in cash_records)]
    expanded: list[PaymentRecord] = []
    for record in combined:
        _apply_final_classification_fallbacks(record)
        if _should_skip_final_record(record):
            continue
        expanded.extend(_expand_final_record(record))
    expanded = dedupe_final_records_by_semantic_cash(expanded)
    expanded.sort(key=_record_sort_key)
    for record in expanded:
        record.date = _display_date(record.date)
    return expanded, matched_count




def dedupe_final_records_by_semantic_cash(records: Iterable[PaymentRecord]) -> list[PaymentRecord]:
    result: list[PaymentRecord] = []
    positions: dict[tuple[str, ...], int] = {}
    for record in records:
        key = _semantic_cash_key(record)
        if key is None or key not in positions:
            if key is not None:
                positions[key] = len(result)
            result.append(record)
            continue
        current_index = positions[key]
        current = result[current_index]
        if _semantic_cash_quality(record) > _semantic_cash_quality(current):
            result[current_index] = record
    return result


def _semantic_cash_key(record: PaymentRecord) -> tuple[str, ...] | None:
    if _classification_text(record.payment_type) != _u(r"\u043d\u0430\u043b\u0438\u0447\u043d\u0430\u044f"):
        return None
    amount = _amount_digits(record.amount)
    if not amount:
        return None
    purpose = _semantic_cash_purpose(record.purpose)
    if not purpose:
        return None
    return (
        _record_date_key(record.date),
        _classification_text(record.operation_type),
        amount,
        _classification_text(record.object_name),
        _classification_text(record.project),
        purpose,
    )


def _semantic_cash_purpose(value: str) -> str:
    text = _classification_text(value)
    if not text:
        return ""
    designation = _u(r"\u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435")
    if designation in text:
        text = text.split(designation, 1)[1]
    conversion_marker = _u(r"\u043e\u043f\u043b\u0430\u0442\u0430 \u0441 \u043f\u0441\u043a \u043d\u0430")
    if conversion_marker in text:
        text = text.split(conversion_marker, 1)[1]
    for word in [
        _u(r"\u043f\u0440\u0438\u0445\u043e\u0434 \u043f\u043e\u0434 \u043e\u0442\u0447\u0435\u0442"),
        _u(r"\u043f\u0441\u043a \u043d\u044c\u044e\u0442\u0435\u043a"),
        _u(r"\u043a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"),
        _u(r"\u043e\u0431\u044a\u0435\u043a\u0442"),
        _u(r"\u043f\u0440\u043e\u0435\u043a\u0442"),
        _u(r"\u0441\u0442\u0430\u0442\u044c\u044f"),
    ]:
        text = re.sub(rf"(^|\s){re.escape(word)}(\s|$)", " ", text)
    text = re.sub(r"[,:;]+", " ", text)
    text = re.sub(r"\s+[\u0430-\u044f\u0451]+[.]\s*[\u0430-\u044f\u0451][.]?$", "", text)
    words = text.split()
    collapsed: list[str] = []
    for word in words:
        if collapsed and collapsed[-1] == word:
            continue
        collapsed.append(word)
    return " ".join(collapsed)


def _semantic_cash_quality(record: PaymentRecord) -> int:
    score = 0
    if _classification_text(record.responsible).replace("_", " ") != "new pay":
        score += 2
    if _classification_text(record.name).replace("_", " ") != "new pay":
        score += 1
    if _u(r"\u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435") in _classification_text(record.purpose):
        score += 1
    return score






def _should_skip_final_record(record: PaymentRecord) -> bool:
    text = " ".join([_classification_text(record.purpose), _classification_text(record.budget_item), _classification_text(record.project)])
    return "\u043d\u0435 \u0437\u0430\u043d\u043e\u0441\u0438" in text or "\u043f\u043e\u043a\u0430 \u043d\u0435 \u0437\u0430\u043d\u043e\u0441" in text


def _display_date(value: str) -> str:
    key = _record_date_key(value)
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", key)
    if match:
        year, month, day = match.groups()
        return f"{day}.{month}.{year}"
    return str(value or "").strip()

def _record_date_key(value: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{2}[.]\d{2}[.]\d{4}", text):
        day, month, year = text.split(".")
        return f"{year}-{month}-{day}"
    return text[:10]


def _amount_digits(value: str) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _set_if(value: str) -> str:
    return value

def _expand_final_record(record: PaymentRecord) -> list[PaymentRecord]:
    date_key = _record_date_key(record.date)
    counterparty = _classification_text(record.counterparty)
    invoice = _classification_text(record.invoice_number)
    if (
        date_key == "2026-06-30"
        and "\u043c\u0435\u0442\u0430\u043b\u043b\u0441\u0435\u0440\u0432\u0438\u0441" in counterparty
        and invoice == "2081934"
        and _amount_digits(record.amount) == "10000000"
    ):
        first = replace(
            record,
            object_name="\u041b\u0438\u0434\u0435\u0440\u0421\u0442\u0440\u043e\u0439 (\u041d\u043e\u0432\u044b\u0439 \u0421\u0432\u0435\u0442) ",
            project="\u041a\u0416",
            budget_item="\u041c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b",
            responsible="\u0421\u043e\u043b\u043e\u0432\u0446\u043e\u0432 \u041d.",
            purpose="\u0410\u0440\u043c\u0430\u0442\u0443\u0440\u0430",
            amount="5500000",
        )
        second = replace(
            record,
            object_name="\u041a\u0440\u0430\u0441\u043d\u043e\u0435 \u0417\u043d\u0430\u043c\u044f",
            project="\u041a\u0416",
            budget_item="\u041c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b",
            responsible="\u0421\u043e\u043b\u043e\u0432\u0446\u043e\u0432 \u041d.",
            purpose="\u0410\u0440\u043c\u0430\u0442\u0443\u0440\u0430",
            amount="4500000",
        )
        return [first, second]
    return [record]


def _apply_final_classification_fallbacks(record: PaymentRecord) -> None:
    purpose = _classification_text(record.purpose)
    counterparty = _classification_text(record.counterparty)
    budget = _classification_text(record.budget_item)
    project = _classification_text(record.project)
    payment_type = _classification_text(record.payment_type)
    invoice = _classification_text(record.invoice_number)

    if _is_conversion_classification(record):
        _mark_record_as_conversion(record)
        budget = ""
        project = _classification_text(record.project)

    if "\u0430\u043b\u044c\u0444\u0430\u043c\u043e\u0431\u0438\u043b\u044c" in counterparty and invoice.startswith("lk"):
        record.object_name = "\u0410\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e"
        record.project = "\u041a\u0440\u0430\u043d"
        record.budget_item = "\u041b\u0438\u0437\u0438\u043d\u0433"
        record.responsible = record.responsible or "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a"

    if "\u043b\u043a \u0430\u043b" in counterparty and invoice.startswith("lk"):
        if invoice.endswith("4798") or "\u043d\u0430\u0447\u0438\u0441\u043b\u0435\u043d" in purpose:
            if "\u0445\u0430\u0432\u0430\u043b" in purpose and "\u0434\u0436\u0443\u043b\u0438\u043e\u043d" in purpose:
                record.purpose = record.purpose.replace("\u043b\u0438\u0437\u0438\u043d\u0433", "\u043f\u0435\u043d\u0438", 1)
            else:
                record.purpose = "\u043f\u0435\u043d\u0438 \u0445\u0430\u0432\u0430\u043b \u0434\u0436\u0443\u043b\u0438\u043e\u043d \u0438\u044e\u043d\u044c"
        elif _classification_text(record.purpose) in {"\u043b\u043a \u043b", "\u043b\u043a-\u043b", "lk l", "lk-l"}:
            record.purpose = "\u043b\u0438\u0437\u0438\u043d\u0433 \u0445\u0430\u0432\u0430\u043b \u0434\u0436\u0443\u043b\u0438\u043e\u043d \u0438\u044e\u043d\u044c"

    if "\u0430\u043b\u044c\u0444\u0430\u043c\u043e\u0431\u0438\u043b\u044c" in counterparty and invoice.startswith("lk") and _classification_text(record.purpose) in {"\u0430\u043c \u043b", "\u0430\u043c-\u043b", "am l", "am-l"}:
        if invoice.endswith("4763"):
            record.purpose = "\u043a\u0440\u0430\u043d\u0430 \u043f\u0435\u043d\u0438"
        else:
            record.purpose = "\u043b\u0438\u0437\u0438\u043d\u0433 \u043a\u0440\u0430\u043d\u0430"

    if "\u0433\u0430\u0437\u043f\u0440\u043e\u043c\u0431\u0430\u043d\u043a \u0430\u0432\u0442\u043e\u043b\u0438\u0437\u0438\u043d\u0433" in counterparty and (("\u0434\u043b" in invoice and "371021" in invoice) or invoice in {"\u0431 \u0441\u0447", "\u0431/\u0441\u0447"}):
        record.object_name = record.object_name or "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
        record.project = "\u0424\u041e\u0422"
        record.budget_item = "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a"
        if "\u043f\u0435\u043d\u0438" not in purpose and "\u043b\u0438\u0437\u0438\u043d\u0433 \u0447\u0430\u043d\u0433\u0430\u043d" not in purpose:
            record.purpose = "\u043b\u0438\u0437\u0438\u043d\u0433 \u0447\u0430\u043d\u0433\u0430\u043d"

    if "\u0430\u0442\u043b \u0441\u043f\u0435\u0446" in counterparty and invoice == "36" and record.amount.replace(" ", "") in {"100000", "100000,00"}:
        _mark_record_as_conversion(record)

    if payment_type in {"\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435", "\u043d\u0430\u043b\u0438\u0447\u043d\u0430\u044f"}:
        if project == "\u043f\u0441\u043a \u043e\u0444\u0438\u0441":
            record.object_name = record.object_name or "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
            record.project = "\u041e\u0444\u0438\u0441"
            project = "\u043e\u0444\u0438\u0441"
        if project == "\u043a\u043e\u0441\u0438\u0447\u043a\u0438\u043d":
            record.project = "\u041e\u0444\u0438\u0441"
            record.responsible = record.responsible or "\u041a\u043e\u0441\u0438\u0447\u043a\u0438\u043d.\u0410"
        if budget == "\u043a\u043e\u0441\u0438\u0447\u043a\u0438\u043d":
            record.budget_item = "\u041a\u043e\u0441\u0438\u0447\u043a\u0438\u043d.\u0410"
        elif budget in {"\u0440\u043e\u0434\u0438\u043d \u043a", "\u0440\u043e\u0434\u0438\u043d \u043a."}:
            record.budget_item = "\u0420\u043e\u0434\u0438\u043d.\u041a"
        elif budget in {"\u043a\u0430\u0448\u0442\u0430\u043d\u0430\u0432\u0430 \u0438\u0440\u0438\u043d\u0430", "\u043a\u0430\u0448\u0442\u0430\u043d\u043e\u0432\u0430 \u0438\u0440\u0438\u043d\u0430"}:
            record.budget_item = "\u041a\u0430\u0448\u0442\u0430\u043d\u043e\u0432\u0430"
        elif budget == "\u0431\u0440\u0438\u0433\u0430\u0434\u0430 \u0436\u0438\u043b\u0438\u043d":
            record.budget_item = "\u0431\u0440\u0438\u0433\u0430\u0434\u0430 \u0416\u0438\u043b\u0438\u043d\u0430"
        elif budget.startswith("\u0430\u0432\u0430\u043d\u0441"):
            record.purpose = " ".join(part for part in [record.budget_item.strip(), record.purpose.strip()] if part)
            record.budget_item = ""
        budget = _classification_text(record.budget_item)
        if budget in {"\u043e\u0444\u0438\u0441\u043d\u044b\u0435 \u0440\u0430\u0441\u0445\u043e\u0434\u044b", "\u043c\u0440\u044d\u043e", "\u0433\u043e\u0441\u043f\u043e\u0448\u043b\u0438\u043d\u0430"}:
            record.budget_item = "\u041e\u0431\u0435\u0441\u043f\u0435\u0447\u0435\u043d\u0438\u0435 \u043e\u0444\u0438\u0441\u0430"
            if not record.purpose.strip() and budget == "\u043e\u0444\u0438\u0441\u043d\u044b\u0435 \u0440\u0430\u0441\u0445\u043e\u0434\u044b":
                record.purpose = "\u041e\u0444\u0438\u0441\u043d\u044b\u0435 \u0440\u0430\u0441\u0445\u043e\u0434\u044b"

    _apply_cash_fot_cleanup(record)

    purpose = _classification_text(record.purpose)
    counterparty = _classification_text(record.counterparty)
    budget = _classification_text(record.budget_item)

    if "\u0437\u0430\u0439\u043c" in purpose and "\u043c\u043e\u0447\u0430\u043b\u043e\u0432" in counterparty:
        record.object_name = record.object_name or "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"
        record.project = record.project or "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"
        record.responsible = record.responsible or "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a"
        _mark_record_as_conversion(record)
        return

    if "\u043b\u0438\u0437\u0438\u043d\u0433" in budget and not record.project.strip() and ("\u043a\u0440\u0430\u043d" in purpose or "\u0430\u043b\u044c\u0444\u0430\u043c\u043e\u0431\u0438\u043b\u044c" in counterparty or record.invoice_number.upper().startswith("LK")):
        record.project = "\u041a\u0440\u0430\u043d"
        record.responsible = record.responsible or "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a"

    if ("\u0447\u043b\u0435\u043d\u0441\u043a" in purpose or "\u043f\u043e\u043c\u043e\u0449\u044c" in counterparty) and "\u0430\u0441" in counterparty:
        record.object_name = record.object_name or "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
        record.project = record.project or "\u041e\u0444\u0438\u0441"
        record.budget_item = record.budget_item or "\u0421\u0420\u041e"
        record.responsible = record.responsible or "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a"

    _apply_reference_cleanup(record)
    _apply_tax_and_salary_cleanup(record)
    _apply_unmatched_context_fallbacks(record)
    _apply_april_13_learned_patterns(record)
    _apply_april_20_learned_patterns(record)
    _apply_april_27_learned_patterns(record)
    _apply_april_30_learned_patterns(record)
    _apply_may_04_learned_patterns(record)
    _apply_may_25_learned_patterns(record)
    _apply_june_30_2026_overrides(record)
    _apply_july_13_2026_overrides(record)
    _apply_july_14_2026_overrides(record)
    _apply_july_15_2026_overrides(record)



def _money_for_purpose(value: str) -> str:
    text = str(value or "").replace("\xa0", " ").replace(" ", "").replace(",", ".")
    match = re.search(r"-?\d+(?:[.]\d+)?", text)
    if not match:
        return str(value or "").strip()
    number = float(match.group())
    whole = int(round(number))
    grouped = f"{whole:,}".replace(",", " ")
    return f"{grouped},00"


def _append_purpose_detail(record: PaymentRecord, detail: str) -> None:
    detail = detail.strip()
    if not detail:
        return
    if detail.casefold() in _classification_text(record.purpose):
        return
    record.purpose = ", ".join(part for part in [record.purpose.strip(), detail] if part)


def _apply_cash_fot_cleanup(record: PaymentRecord) -> None:
    if _classification_text(record.payment_type) not in {"\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435", "\u043d\u0430\u043b\u0438\u0447\u043d\u0430\u044f"}:
        return
    if _classification_text(record.project) != "\u0444\u043e\u0442":
        return
    budget = _classification_text(record.budget_item)
    person_by_budget = {
        "\u043f\u043e\u0440\u043e\u0437\u043e\u0432": "\u041f\u043e\u0440\u043e\u0437\u043e\u0432 \u041d.",
        "\u043c\u0438\u0440\u043e\u043d\u043e\u0432\u0430": "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e.",
        "\u0441\u043e\u043b\u043e\u0432\u0446\u043e\u0432": "\u0421\u043e\u043b\u043e\u0432\u0446\u043e\u0432 \u041d.",
        "\u0440\u0430\u0437\u0434\u0440\u043e\u0433\u0438\u043d\u0430": "\u0420\u0430\u0437\u0434\u0440\u043e\u0433\u0438\u043d\u0430.\u0421",
        "\u0433\u0440\u0438\u0433\u043e\u0440\u044c\u0435\u0432": "\u0413\u0440\u0438\u0433\u043e\u0440\u044c\u0435\u0432 \u0421.",
    }
    move_to_purpose = {
        "\u043f\u0435\u0434\u043e\u0440\u0435\u043d\u043a\u043e": "\u041f\u0435\u0434\u043e\u0440\u0435\u043d\u043a\u043e",
    }
    if budget in {"\u0437\u0430\u0440\u043f\u043b\u0430\u0442\u0430", "\u0437\u043f"}:
        record.budget_item = ""
    elif budget in person_by_budget:
        record.budget_item = person_by_budget[budget]
        _append_purpose_detail(record, record.budget_item)
    elif budget in move_to_purpose:
        record.budget_item = ""
        _append_purpose_detail(record, move_to_purpose[budget])


def _apply_reference_cleanup(record: PaymentRecord) -> None:
    project = _classification_text(record.project)
    budget = _classification_text(record.budget_item)
    purpose = _classification_text(record.purpose)

    if project in {"\u043a\u043c \u043c\u043e\u043d\u0442", "\u043a\u043c \u043c\u043e\u043d\u0442\u0430\u0436", "\u043a\u043c(\u043c)", "\u043a\u043c (\u043c)"}:
        record.project = "\u041a\u041c ( \u041c )"

    if (
        _classification_text(record.object_name) == "\u0430\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e"
        and not record.project.strip()
        and not record.budget_item.strip()
        and "\u0437\u0430\u043f\u0447\u0430\u0441" in purpose
    ):
        record.project = "\u041b\u0438\u0447\u043d\u044b\u0435 \u0430\u0432\u0442\u043e"
        record.budget_item = "\u0420\u0435\u043c\u043e\u043d\u0442/\u0422\u041e"

    if budget in {"\u0432\u0438\u0434\u0435\u043e\u0433\u0440\u0430\u0444", "\u0432\u0438\u0434\u0435\u043e"}:
        record.budget_item = "\u0412\u0438\u0434\u0435\u043e \u043f\u0440\u043e\u0434\u0430\u043a\u0448\u043d"
    elif budget in {"ai \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0437\u0430\u0446\u0438\u044f", "\u0430\u0439 \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0437\u0430\u0446\u0438\u044f"}:
        record.budget_item = "\u041f\u043e\u0434\u0440\u044f\u0434\u0447\u0438\u043a" if "\u043f\u043e\u0434\u0440\u044f\u0434\u0447" in purpose else "\u041f\u0440\u043e\u0433\u0440\u0430\u043c\u043d\u043e\u0435 \u043e\u0431\u0435\u0441\u043f\u0435\u0447\u0435\u043d\u0438\u0435"
    elif budget in {"\u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435", "\u0440\u0443\u0441\u043f\u0440\u043e\u0444\u0430\u0439\u043b"} or "\u0440\u0443\u0441\u043f\u0440\u043e\u0444\u0430\u0439\u043b" in purpose:
        record.budget_item = "\u041f\u0440\u043e\u0433\u0440\u0430\u043c\u043d\u043e\u0435 \u043e\u0431\u0435\u0441\u043f\u0435\u0447\u0435\u043d\u0438\u0435"
    elif budget in {"\u0440\u0430\u0431\u043e\u0447\u0438\u0435 (\u043e\u043a\u043b\u0430\u0434)", "\u0440\u0430\u0431\u043e\u0447\u0438\u0435 \u043e\u043a\u043b\u0430\u0434", "\u043e\u043a\u043b\u0430\u0434"}:
        record.budget_item = "\u0417\u0430\u0440\u043f\u043b\u0430\u0442\u0430"


def _apply_tax_and_salary_cleanup(record: PaymentRecord) -> None:
    project = _classification_text(record.project)
    budget = _classification_text(record.budget_item)
    if project == "\u043d\u0430\u043b\u043e\u0433\u0438" and "\u043d\u0434\u0444\u043b" in budget:
        record.counterparty = ""
        record.invoice_number = ""
        record.purpose = f"\u043d\u0430\u043b\u043e\u0433\u0438 - {_money_for_purpose(record.amount)}"
    if project == "\u0444\u043e\u0442" and budget == "\u043e\u0444\u0438\u0446\u0438\u0430\u043b\u044c\u043d\u0430\u044f \u0437\u043f":
        record.bank = "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a"
        record.counterparty = ""
        record.invoice_number = ""
        record.purpose = f"\u0437\u043f - {_money_for_purpose(record.amount)}"


def _apply_unmatched_context_fallbacks(record: PaymentRecord) -> None:
    counterparty = _classification_text(record.counterparty)
    purpose = _classification_text(record.purpose)
    invoice = _classification_text(record.invoice_number)
    if "\u0430\u043b\u0444\u0435\u0440\u0443\u043a" in counterparty and invoice.startswith("28138"):
        record.object_name = record.object_name or "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
        record.project = record.project or "\u041f\u0418\u0420"
        record.budget_item = record.budget_item or "\u0423\u0447\u0430\u0441\u0442\u043e\u043a"
        record.responsible = record.responsible or "\u041a\u043e\u0441\u0438\u0447\u043a\u0438\u043d.\u0410"
    if "\u043b\u0435\u0431\u0435\u0434\u0435\u0432" in counterparty and "\u0442\u0440\u0430\u043d\u0441\u043f\u043e\u0440\u0442" in purpose:
        record.object_name = record.object_name or "\u0422\u0443\u043d\u0433\u0443\u0441"
        record.project = record.project or "\u041a\u041c ( \u041f\u0420 )"
        record.budget_item = record.budget_item or "\u0414\u043e\u0441\u0442\u0430\u0432\u043a\u0430"
        record.responsible = record.responsible or "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e."
        record.purpose = "\u0448\u0430\u043b\u0430\u043d\u0434\u0430"
    if "\u0441\u0430\u043c\u044b\u0433\u0438\u043d" in counterparty and "\u0442\u0440\u0430\u043d\u0441\u043f\u043e\u0440\u0442" in purpose:
        record.budget_item = "\u0422\u0435\u0445\u043d\u0438\u043a\u0430"
        record.purpose = "\u0448\u0430\u043b\u0430\u043d\u0434\u0430"


def _apply_april_13_learned_patterns(record: PaymentRecord) -> None:
    counterparty = _classification_text(record.counterparty)
    purpose = _classification_text(record.purpose)
    invoice = _classification_text(record.invoice_number)

    if "\u043b\u0435\u0431\u0435\u0434\u0435\u0432" in counterparty and ("\u0442\u0440\u0430\u043d\u0441\u043f\u043e\u0440\u0442" in purpose or purpose == "\u0448\u0430\u043b\u0430\u043d\u0434\u0430"):
        object_by_invoice = {
            "61": "\u0420\u043e\u0431\u043e\u0434\u0440\u043e\u0438\u0434",
            "68": "\u0422\u0443\u043d\u0433\u0443\u0441",
            "74": "\u0422\u0443\u043d\u0433\u0443\u0441",
            "146": "\u0418\u041f \u041f\u043e\u043b\u044f\u043a\u043e\u0432",
            "158": "\u0410\u043b\u0430\u0440\u043c \u041c\u043e\u0442\u043e\u0440\u0441",
        }
        if invoice in object_by_invoice:
            record.object_name = object_by_invoice[invoice]
        record.project = "\u041a\u041c ( \u041f\u0420 )"
        record.budget_item = "\u0414\u043e\u0441\u0442\u0430\u0432\u043a\u0430"
        record.responsible = "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e."
        record.purpose = "\u0448\u0430\u043b\u0430\u043d\u0434\u0430"

    if "\u043d\u0438\u043a\u043e\u043b\u0430\u0435\u0432\u0430" in counterparty and "\u043f\u0440\u0435\u0437\u0435\u043d\u0442\u0430\u0446" in purpose:
        record.responsible = "\u041a\u043e\u0441\u0438\u0447\u043a\u0438\u043d.\u0410"

    if "\u044d\u043b\u0435\u043a\u0442\u0440\u043e\u0442\u0435\u0445\u043c\u043e\u043d\u0442\u0430\u0436" in counterparty:
        record.object_name = "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
        record.project = "\u0414\u043e\u043b\u0433"
        record.budget_item = "\u0412\u043e\u0437\u0432\u0440\u0430\u0442 \u0434\u043e\u043b\u0433\u0430"
        record.responsible = "\u0421\u043e\u043b\u043e\u0432\u0446\u043e\u0432 \u041d."
        record.purpose = "\u042d\u0422\u041c"

    if "\u043f\u0435\u0442\u0440\u043e\u0432\u0438\u0447" in counterparty and invoice == "\u0442\u044e\u044d00168834":
        record.object_name = "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
        record.project = "\u0414\u043e\u043b\u0433"
        record.budget_item = "\u0412\u043e\u0437\u0432\u0440\u0430\u0442 \u0434\u043e\u043b\u0433\u0430"
        record.responsible = "\u0421\u043e\u043b\u043e\u0432\u0446\u043e\u0432 \u041d."

    if "\u043d\u044d\u0442\u0441\u0442\u043e\u0440" in counterparty:
        record.object_name = "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
        record.project = "\u041e\u0444\u0438\u0441"
        record.budget_item = "\u041e\u0431\u0435\u0441\u043f\u0435\u0447\u0435\u043d\u0438\u0435 \u043e\u0444\u0438\u0441\u0430"
        record.responsible = "\u0420\u0430\u0437\u0434\u0440\u043e\u0433\u0438\u043d\u0430.\u0421"

    if "\u0432\u0441\u0435\u0438\u043d\u0441\u0442\u0440\u0443\u043c\u0435\u043d\u0442" in counterparty and _classification_text(record.object_name) == "\u043f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u043e":
        if _classification_text(record.budget_item) in {"\u0430\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e", "\u0442\u043e\u043f\u043b\u0438\u0432\u043e", ""}:
            record.project = "\u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0435 \u0440\u0430\u0441\u0445\u043e\u0434\u044b"
            record.budget_item = "\u0420\u0430\u0441\u0445\u043e\u0434\u043d\u0438\u043a\u0438"

    if "\u0430\u0432\u0442\u043e\u0434\u043e\u043a" in counterparty:
        record.object_name = "\u0410\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e"
        record.project = "\u041b\u0438\u0447\u043d\u044b\u0435 \u0430\u0432\u0442\u043e"
        record.budget_item = "\u0420\u0435\u043c\u043e\u043d\u0442/\u0422\u041e"
        record.responsible = "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e."



def _apply_april_20_learned_patterns(record: PaymentRecord) -> None:
    counterparty = _classification_text(record.counterparty)
    purpose = _classification_text(record.purpose)
    if "\u0430\u043a\u0442\u0435\u043a" in counterparty:
        if record.invoice_number.strip() in {"", "\u0431/\u0441\u0447", "\u0431 \u0441\u0447"}:
            record.invoice_number = "814"
        record.object_name = "\u0410\u043a\u0442\u0435\u043a"
        record.project = "\u0412\u043e\u0437\u0432\u0440\u0430\u0442"
        record.budget_item = "\u0412\u043e\u0437\u0432\u0440\u0430\u0442 \u0434\u043e\u043b\u0433\u0430"
        record.responsible = "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e."
    if "\u0430\u0432\u0442\u043e\u043a\u0440\u0430\u043d \u0430\u0440\u0435\u043d\u0434\u0430" in purpose or "\u0430\u0432\u0442\u043e\u043a\u0440\u0430\u043d \u0430\u0440\u0435\u043d\u0434\u0430" in counterparty:
        record.counterparty = "\u0410\u041e \"\u0410\u0432\u0442\u043e\u043a\u0440\u0430\u043d \u0410\u0440\u0435\u043d\u0434\u0430\""
        record.object_name = "\u041b\u0438\u0434\u0435\u0440\u0421\u0442\u0440\u043e\u0439  (\u041c\u0435\u0442\u0430\u043b\u043b\u043e\u0441\u0442\u0440\u043e\u0439)"
        record.project = "\u041a\u041c ( \u041c )"
        record.budget_item = "\u0422\u0435\u0445\u043d\u0438\u043a\u0430"
        record.responsible = "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e."
        record.purpose = "\u041f\u043e\u0434\u044c\u0435\u043c\u043d\u0438\u043a"



def _apply_april_27_learned_patterns(record: PaymentRecord) -> None:
    counterparty = _classification_text(record.counterparty)
    if "\u0441\u0434\u044d\u043a" in counterparty:
        record.object_name = "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
        record.project = "\u041e\u0444\u0438\u0441"
        record.budget_item = "\u041a\u0443\u0440\u044c\u0435\u0440\u0441\u043a\u0430\u044f \u0441\u043b\u0443\u0436\u0431\u0430"
        record.responsible = "\u0420\u0430\u0437\u0434\u0440\u043e\u0433\u0438\u043d\u0430.\u0421"
    if "\u0445\u044d\u0434\u0445\u0430\u043d\u0442\u0435\u0440" in counterparty or "headhunter" in _classification_text(record.purpose):
        record.object_name = "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
        record.project = "\u041d\u0430\u0439\u043c"
        record.budget_item = "HR"
        record.responsible = "\u0420\u0430\u0437\u0434\u0440\u043e\u0433\u0438\u043d\u0430.\u0421"



def _apply_april_30_learned_patterns(record: PaymentRecord) -> None:
    if _record_date_key(record.date) != "2026-04-30":
        return
    counterparty = _classification_text(record.counterparty)
    invoice = _classification_text(record.invoice_number)
    purpose = _classification_text(record.purpose)
    payment_type = _classification_text(record.payment_type)

    if "мак карго" in counterparty and invoice == "1":
        _mark_record_as_conversion(record)
        record.responsible = "Родин.К"
        record.purpose = "Мурсал авто"

    if "апм" in counterparty and invoice == "цб-235":
        _mark_record_as_conversion(record)
        record.responsible = "Мочалов.К"
        record.purpose = "Евгений Металлопрокат"

    if "компания главкреп" in counterparty and invoice == "5492":
        record.purpose = "Метизы"

    if payment_type == "наличная":
        if _classification_text(record.budget_item) == "командировочные расходы":
            record.budget_item = "Командировочные"
            if purpose == "мск":
                record.purpose = "Командировочные расходы мск"
        if _classification_text(record.budget_item) == "бригада ж" and _classification_text(record.project) == "фот":
            record.budget_item = "Жилин А."
            if "зп" in purpose:
                record.purpose = "Бригада Ж  " + record.purpose.strip()
        if _classification_text(record.budget_item) == "разнорабочие" and purpose == "дом 2":
            record.purpose = "разнорабочие  дом 2"
        if "лидерстрой" in _classification_text(record.object_name) and "металлострой" in _classification_text(record.project):
            record.project = "АР"
            record.budget_item = "Агенское вознагрождение"
            record.purpose = "Металлострой  работа  агентское"
        if _classification_text(record.budget_item) == "суходолин с (моп)":
            record.budget_item = "Суходолин С."

def _apply_may_04_learned_patterns(record: PaymentRecord) -> None:
    if _record_date_key(record.date) != "2026-05-04":
        return
    counterparty = _classification_text(record.counterparty)
    invoice = _classification_text(record.invoice_number)

    if "мегафон" in counterparty:
        record.object_name = "ПСК Ньютек"
        record.project = "Офис"
        record.budget_item = "АТС"
        record.responsible = "Гончаров В."

    if "альфамобиль" in counterparty and invoice == "lk1703724":
        record.responsible = "Родин.К"
        record.purpose = "Лизинговый платеж Ивановец по договору №32534-СПБ-23-АМ-Л"

    if "газпромбанк автолизинг" in counterparty and invoice == "дл-371021-25":
        record.responsible = "Родин.К"
        record.purpose = "лизинг чанган ЗП Май"

    if "автоинвест" in counterparty and invoice in {"204", "218"}:
        record.object_name = "Производство"
        record.project = "Производственные расходы"
        record.budget_item = "Техника"
        record.responsible = "Миронова Ю."

    if "яндекс 360" in counterparty:
        record.object_name = "ПСК Ньютек"
        record.project = "Маркетинг"
        record.budget_item = "Яндекс бизнес"
        record.responsible = "Гончаров В."
        record.purpose = 'Доступ к сервису "Яндекс 360 для бизнеса", доменные почты'

    if "профстрой" in counterparty and invoice == "12":
        _mark_record_as_conversion(record)
        record.responsible = "Косичкин.А"

    if "мак карго" in counterparty and invoice == "1":
        _mark_record_as_conversion(record)
        record.purpose = "Мурсал авто"

    if "металлсервис" in counterparty and invoice == "1309864":
        _mark_record_as_conversion(record)
        record.purpose = "Арматура Лизан"

    if "домашний интерьер" in counterparty or "мебель-крафт" in counterparty:
        _mark_record_as_conversion(record)
        record.purpose = "Жилин мебель"
def _apply_may_25_learned_patterns(record: PaymentRecord) -> None:
    if _record_date_key(record.date) != "2026-05-25":
        return
    counterparty = _classification_text(record.counterparty)
    purpose = _classification_text(record.purpose)
    invoice = _classification_text(record.invoice_number)

    if "соглашен" in purpose and "юридичес" in purpose:
        record.counterparty = "Адвокат Евдокимов Алексей Анатольевич"
        record.object_name = "ПСК Ньютек"
        record.project = "Инвестиции"
        record.budget_item = "Участок"
        record.responsible = "Раздрогина.С"
        record.purpose = "суд адвокат" if invoice == "15" else "адвокат"

    if "окц" in counterparty and ("стройка" in purpose or "уфк" in counterparty):
        record.counterparty = "Казначейство России (ФНС России)"
        record.invoice_number = "-"
        record.object_name = "ПСК Ньютек"
        record.project = "Инвестиции"
        record.budget_item = "Участок"
        record.responsible = "Раздрогина.С"
        record.purpose = "суд"

    if "тд" in counterparty and "рестарт" in counterparty and invoice == "тд-2329":
        record.object_name = "Техносоюз"

    if "компания главкреп" in counterparty and invoice == "6367":
        record.object_name = "Техносоюз"

    if "металлот" in counterparty and invoice == "сп15392/1":
        record.object_name = "ИП Егунян"
        record.project = "КМ ( ПР )"
        record.budget_item = "Материалы"
        record.responsible = "Соловцов Н."

    if "автодок" in counterparty and invoice.startswith("sp-70013") and _classification_text(record.amount) in {"14452", "14452,00"}:
        record.invoice_number = "SP_70013: 98"
        _mark_record_as_conversion(record)
        record.responsible = "Миронова Ю."

    if _classification_text(record.payment_type) == "наличная" and _classification_text(record.project) == "фот" and "педоренко" in (_classification_text(record.budget_item) + " " + _classification_text(record.purpose)) and _classification_text(record.amount) in {"61000", "61000,00"}:
        record.object_name = "ПСК Ньютек"
        record.budget_item = "Педоренко"
        record.purpose = "зп апрель"

def _apply_june_30_2026_overrides(record: PaymentRecord) -> None:
    if _record_date_key(record.date) != "2026-06-30":
        return
    counterparty = _classification_text(record.counterparty)
    invoice = _classification_text(record.invoice_number)

    def set_fields(**values: str) -> None:
        for field, value in values.items():
            setattr(record, field, value)

    def set_link(value: str) -> None:
        if value and not record.invoice_link.strip():
            record.invoice_link = value

    if "\u0442\u0435\u0445\u043d\u043e\u043b\u043e\u0433\u0438\u0438 \u043c\u0430\u0440\u043a\u0435\u0442\u0438\u043d\u0433\u0430" in counterparty and invoice == "158221":
        set_fields(object_name="\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", project="\u0418\u043d\u0432\u0435\u0441\u0442\u0438\u0446\u0438\u0438", budget_item="\u0432\u043e\u0434\u043e\u0440\u043e\u0434 24", responsible="\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a", purpose="\u0412\u043e\u0434\u043e\u0440\u043e\u0434 \u043f\u043e\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u0435 \u0440\u0435\u043a. \u041a\u0430\u0431.")

    if "\u043f\u043f\u0440" in counterparty and invoice == "77600100024090422" and not record.invoice_link.strip():
        record.invoice_link = "-"

    if "\u0430\u043a\u0442\u0435\u043a" in counterparty:
        set_fields(object_name="\u0410\u043a\u0442\u0435\u043a", project="\u0412\u043e\u0437\u0432\u0440\u0430\u0442", budget_item="\u0412\u043e\u0437\u0432\u0440\u0430\u0442 \u0434\u043e\u043b\u0433\u0430", responsible="\u041a\u043e\u0441\u0438\u0447\u043a\u0438\u043d.\u0410")
        if not record.invoice_link.strip():
            record.invoice_link = "-"

    if "\u0432\u0441" in counterparty and invoice == "9":
        _mark_record_as_conversion(record)
        record.responsible = "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a"
        if "\u0440\u0438\u043d\u0430\u0442" not in _classification_text(record.purpose):
            record.purpose = "\u0420\u0438\u043d\u0430\u0442 " + record.purpose.strip()

    if "\u043d\u0430\u0432\u0438\u0441 \u0442\u0440\u0430\u043d\u0441" in counterparty and invoice == "884":
        set_fields(project="\u0411\u043b\u0430\u0433\u043e\u0443\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u043e", budget_item="\u0422\u0435\u0445\u043d\u0438\u043a\u0430", purpose="\u0410\u0440\u0435\u043d\u0434\u0430 \u0441\u043f\u0435\u0446\u0442\u0435\u0445\u043d\u0438\u043a\u0438 \u0432\u044b\u0432\u043e\u0437 \u043c\u0443\u0441\u043e\u0440\u0430")

    if "\u043a\u043e\u043c\u043f\u0430\u043d\u0438\u044f \u0433\u043b\u0430\u0432\u043a\u0440\u0435\u043f" in counterparty and invoice == "6789":
        record.purpose = "\u041c\u0435\u0442\u0438\u0437\u044b"

    if "\u043f\u043a \u0441\u0442\u0440\u043e\u0439\u0441\u0438\u0441\u0442\u0435\u043c\u0430" in counterparty and invoice == "151":
        _mark_record_as_conversion(record)
        record.responsible = "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a"
        if "\u0440\u0438\u043d\u0430\u0442" not in _classification_text(record.purpose):
            record.purpose = "\u0420\u0438\u043d\u0430\u0442 " + record.purpose.strip()

    if "\u0434\u0430\u0432\u0440\u0443\u0441" in counterparty and invoice in {"1481", "1489"}:
        record.budget_item = "\u041c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b"
        if invoice == "1489":
            record.object_name = "\u041a\u0440\u0430\u0441\u043d\u043e\u0435 \u0417\u043d\u0430\u043c\u044f"

    if "\u0431\u0443\u0445\u0442\u043e\u044f\u0440\u043e\u0432" in counterparty and invoice == "4":
        record.purpose = "\u0423\u0441\u0442\u0440\u043e\u0439\u0441\u0442\u0432\u043e \u0433\u0438\u0434\u0440\u043e\u0438\u0437\u043e\u043b\u044f\u0446\u0438\u0438 \u043a\u0440\u043e\u0432\u043b\u0438"

    if "\u043c\u0443\u0440\u0430\u0432" in counterparty and invoice == "40":
        set_fields(object_name="\u041a\u0440\u0430\u0441\u043d\u043e\u0435 \u0417\u043d\u0430\u043c\u044f", purpose="\u0420\u0430\u0437\u0440\u0430\u0431\u043e\u0442\u043a\u0430 \u0443\u0437\u043b\u0430 \u0434\u043b\u044f \u041a\u041c, \u043f\u043e \u0440\u0430\u0431\u043e\u0447\u0435\u0439 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430\u0446\u0438\u0438")

    if "\u0438\u0432\u0430\u043d\u043e\u0432 \u0435\u0433\u043e\u0440" in counterparty and invoice == "272":
        record.object_name = "\u041a\u0440\u0430\u0441\u043d\u043e\u0435 \u0417\u043d\u0430\u043c\u044f"

    if "\u0431\u0430\u043d\u043a \u0442\u043e\u0447\u043a\u0430" in counterparty and invoice in {"\u0431 \u0441\u0447", "\u0431/\u0441\u0447"} and _amount_digits(record.amount) == "177000":
        set_fields(counterparty="\u0418\u041f \u041f\u0443\u0433\u0430\u0447\u0435\u0432", invoice_number="13-26", object_name="\u0418\u041f \u041f\u0443\u0433\u0430\u0447\u0435\u0432", project="\u0412\u043e\u0437\u0432\u0440\u0430\u0442", budget_item="\u0412\u043e\u0437\u0432\u0440\u0430\u0442 \u0434\u043e\u043b\u0433\u0430", responsible="\u0420\u0430\u0437\u0434\u0440\u043e\u0433\u0438\u043d\u0430.\u0421", purpose="\u0412\u043e\u0437\u0432\u0440\u0430\u0442 \u043f\u043e \u0414\u0421 \u043f\u043e \u0442\u0440\u0435\u0431\u043e\u0432\u0430\u043d\u0438\u044e")
        set_link("https://drive.google.com/open?id=1yXIK-a0m6HzdPgSrZ0EJULoZ0L-UQbTg&usp=drive_fs")

    if "\u0441\u043e\u043a\u043e\u043b\u043e\u0432" in counterparty and invoice == "290":
        set_fields(object_name="\u0418\u041f \u0415\u0433\u0443\u043d\u044f\u043d", project="\u041f\u0418\u0420", budget_item="\u041f\u043e\u0434\u0440\u044f\u0434\u0447\u0438\u043a", responsible="\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e.", purpose="\u043f\u043e\u0434\u0440\u044f\u0434\u0447\u0438\u043a, \u041a\u041c\u0414")
        set_link("https://drive.google.com/file/d/1HyF7pN508M__W18PC8ksrENpTjLlgyqx/view")

    if "\u0438\u0432\u0430\u043d\u043e\u0432 \u0435\u0433\u043e\u0440" in counterparty and invoice == "272":
        record.budget_item = "\u041f\u043e\u0434\u0440\u044f\u0434\u0447\u0438\u043a"

    if "\u0431\u043e\u0439\u043a\u043e\u0432" in counterparty and invoice == "16":
        set_fields(object_name="\u041b\u0438\u0434\u0435\u0440\u0421\u0442\u0440\u043e\u0439  (\u041c\u0435\u0442\u0430\u043b\u043b\u043e\u0441\u0442\u0440\u043e\u0439)", project="\u0410\u0420", budget_item="\u041f\u043e\u0434\u0440\u044f\u0434\u0447\u0438\u043a", responsible="\u0420\u043e\u0434\u0438\u043d.\u041a")
        set_link("https://drive.google.com/open?id=1wf3noSepaknaWSMs_4uy0Tsz-EuE6lwA&usp=drive_fs")

    if "\u043f\u0435\u0442\u0440\u043e\u0432\u0438\u0447" in counterparty and invoice == "\u0441\u0433\u044d00074307":
        set_fields(object_name="\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", project="\u0424\u041e\u0422", budget_item="\u041a\u043e\u0441\u0438\u0447\u043a\u0438\u043d.\u0410", purpose="\u0420\u0430\u0441\u0445\u043e\u0434\u043d\u0438\u043a\u0438 \u0418\u043d\u0441\u0442\u0430\u043b\u043b\u044f\u0446\u0438\u044f Geberit")

    if "\u0430\u0442\u043b \u0441\u043f\u0435\u0446" in counterparty and invoice == "38":
        _mark_record_as_conversion(record)
        record.responsible = "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e."
        if "\u0438\u043f \u043b\u0438\u0437\u0430\u043d" not in _classification_text(record.purpose):
            record.purpose = "\u0438\u043f \u043b\u0438\u0437\u0430\u043d " + record.purpose.strip()

    if "\u0432\u0441\u0435\u0438\u043d\u0441\u0442\u0440\u0443\u043c\u0435\u043d\u0442" in counterparty and invoice in {"\u0431 \u0441\u0447", "\u0431/\u0441\u0447"}:
        set_fields(invoice_number="\u0430\u043a\u0442 \u0441\u0432\u0435\u0440\u043a\u0438", object_name="\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", project="\u0414\u043e\u043b\u0433", budget_item="\u0412\u043e\u0437\u0432\u0440\u0430\u0442 \u0434\u043e\u043b\u0433\u0430", responsible="\u0421\u043e\u043b\u043e\u0432\u0446\u043e\u0432 \u041d.")
        set_link("https://drive.google.com/file/d/1CboU_nbuVzfOQZPt4pwnqwV95YlDBTHi/view?usp=drivesdk")

    if "\u043c\u0435\u0442\u0430\u043b\u043b\u043e\u0442\u043e\u0440\u0433" in counterparty and invoice == "\u0441\u043f19881":
        set_fields(object_name="\u0418\u041f \u0421\u0435\u0440\u0433\u0435\u0435\u0432", project="\u041a\u041c ( \u041f\u0420 )", budget_item="\u041c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b", responsible="\u0421\u043e\u043b\u043e\u0432\u0446\u043e\u0432 \u041d.")
        set_link("https://drive.google.com/file/d/1NKNA-Gc0sF498vgkFhWBGKRpKhcd1jpy/view")

    if "\u0440\u0443\u0441\u043f\u0430\u043d" in counterparty and invoice == "697":
        record.project = "\u0410\u0420"

    if "\u043e\u043a\u043d\u0430 \u0444\u043e\u0440\u0442\u0435" in counterparty and invoice in {"fb7771 61127", "fb7771-61127"}:
        set_fields(budget_item="\u041c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b", purpose="\u041e\u043a\u043d\u0430")

    if "\u0441\u043b\u0435\u043f\u0446\u043e\u0432" in counterparty and invoice in {"7771 57078", "7771-57078"}:
        set_fields(object_name="\u041a\u0440\u0430\u0441\u043d\u043e\u0435 \u0417\u043d\u0430\u043c\u044f", budget_item="\u041f\u043e\u0434\u0440\u044f\u0434\u0447\u0438\u043a")

    if "\u0440\u0435\u0441\u0442\u0430\u0440\u0442" in counterparty and invoice in {"\u0442\u0434 2977", "\u0442\u0434-2977"}:
        record.object_name = "\u0418\u041f \u0421\u0435\u0440\u0433\u0435\u0435\u0432"

    if "\u043a\u043e\u043c\u043f\u0430\u043d\u0438\u044f \u0433\u043b\u0430\u0432\u043a\u0440\u0435\u043f" in counterparty and invoice == "7981":
        record.object_name = "\u0418\u041f \u0421\u0435\u0440\u0433\u0435\u0435\u0432"

    if "\u043c\u0435\u0442\u0430\u043b\u043b \u043f\u0440\u0435\u0441\u0442\u0438\u0436" in counterparty and invoice == "341":
        record.object_name = "\u041b\u0438\u0434\u0435\u0440\u0421\u0442\u0440\u043e\u0439 (\u041d\u043e\u0432\u044b\u0439 \u0421\u0432\u0435\u0442) "


def _apply_july_13_2026_overrides(record: PaymentRecord) -> None:
    if _record_date_key(record.date) != "2026-07-13":
        return
    counterparty = _classification_text(record.counterparty)
    invoice = _classification_text(record.invoice_number)
    payment_type = _classification_text(record.payment_type)
    amount = _amount_digits(record.amount)

    def set_fields(**values: str) -> None:
        for field, value in values.items():
            setattr(record, field, value)

    if "\u043a\u043e\u043d\u0441\u0430\u043b\u0442\u0438\u043d\u0433 \u043e\u043d\u043b\u0430\u0439\u043d" in counterparty and invoice == "130433":
        set_fields(
            object_name="\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a",
            project="\u041e\u0444\u0438\u0441",
            budget_item="\u041e\u0431\u0435\u0441\u043f\u0435\u0447\u0435\u043d\u0438\u0435 \u043e\u0444\u0438\u0441\u0430",
            responsible="\u0420\u0430\u0437\u0434\u0440\u043e\u0433\u0438\u043d\u0430.\u0421",
            purpose="\u0420\u0435\u0433\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u044f \u0441\u0430\u0439\u0442\u0430 \u0434\u043b\u044f \u0420\u041a\u041d",
        )

    if "\u043b\u0435 \u043c\u043e\u043d\u043b\u0438\u0434" in counterparty and invoice.startswith("041"):
        _mark_record_as_conversion(record)
        record.invoice_number = "041 000-604283/4816"
        record.purpose = "\u041a\u0430\u0447\u0435\u043b\u0438"

    if "\u043b\u043a \u0430\u043b" in counterparty and invoice == "lk1790873":
        set_fields(
            object_name="\u0410\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e",
            project="\u041b\u0435\u0433\u043a\u043e\u0432\u044b\u0435 \u0430\u0432\u0442\u043e",
            budget_item="\u041b\u0438\u0437\u0438\u043d\u0433",
            responsible="\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a",
            purpose="\u0445\u0430\u0432\u0430\u043b \u0434\u0436\u0443\u043b\u0438\u043e\u043d \u043f\u0435\u043d\u0438",
        )

    if "\u0430\u043b\u044c\u0444\u0430\u043c\u043e\u0431\u0438\u043b\u044c" in counterparty and invoice == "lk1790830":
        set_fields(
            object_name="\u0410\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e",
            project="\u041a\u0440\u0430\u043d",
            budget_item="\u041b\u0438\u0437\u0438\u043d\u0433",
            responsible="\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a",
            purpose="\u043a\u0440\u0430\u043d \u043f\u0435\u043d\u0438",
        )

    if "\u0430\u043b\u044c\u0444\u0430\u043c\u043e\u0431\u0438\u043b\u044c" in counterparty and invoice == "lk1790839":
        set_fields(
            object_name="\u0410\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e",
            project="\u041b\u0435\u0433\u043a\u043e\u0432\u044b\u0435 \u0430\u0432\u0442\u043e",
            budget_item="\u041b\u0438\u0437\u0438\u043d\u0433",
            responsible="\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a",
            purpose="\u0445\u0430\u0432\u0430\u043b \u043c6 \u043f\u0435\u043d\u0438",
        )

    if "\u0430\u043b\u044c\u0444\u0430\u043c\u043e\u0431\u0438\u043b\u044c" in counterparty and invoice == "lk1790813":
        set_fields(
            object_name="\u0410\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e",
            project="\u041b\u0435\u0433\u043a\u043e\u0432\u044b\u0435 \u0430\u0432\u0442\u043e",
            budget_item="\u041b\u0438\u0437\u0438\u043d\u0433",
            responsible="\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a",
            purpose="\u043b\u0438\u0437\u0438\u043d\u0433 \u0445\u0430\u0432\u0430\u043b \u043c6 (\u0438\u044e\u043d\u044c)",
        )

    if payment_type == "\u043d\u0430\u043b\u0438\u0447\u043d\u0430\u044f" and amount == "33130" and "\u043a\u0440\u0435\u0434\u0438\u0442" in _classification_text(record.purpose):
        set_fields(
            object_name="\u0410\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e",
            project="\u041a\u0440\u0430\u043d",
            budget_item="\u041a\u0440\u0435\u0434\u0438\u0442",
            responsible="\u041a\u043e\u0441\u0438\u0447\u043a\u0438\u043d.\u0410",
            purpose="\u043a\u0440\u0435\u0434\u0438\u0442",
        )

    if payment_type == "\u043d\u0430\u043b\u0438\u0447\u043d\u0430\u044f" and amount == "52091":
        set_fields(
            object_name="\u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u043e",
            project="\u041c\u0435\u0442\u0430\u043b\u043b\u043e\u043b\u043e\u043c",
            budget_item="",
            responsible="\u0420\u043e\u0434\u0438\u043d.\u041a",
            purpose="",
        )

    if "\u0447\u0435\u0440\u043d\u044f\u0432\u0441\u043a" in counterparty and invoice == "94":
        set_fields(
            object_name="\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a",
            project="\u041c\u0430\u0440\u043a\u0435\u0442\u0438\u043d\u0433",
            budget_item="\u042f\u043d\u0434\u0435\u043a\u0441 \u0414\u0438\u0440\u0435\u043a\u0442",
            responsible="\u0413\u043e\u043d\u0447\u0430\u0440\u043e\u0432 \u0412.",
            purpose="\u0434\u0438\u0440\u0435\u043a\u0442\u043e\u043b\u043e\u0433 \u0410\u043b\u0435\u043a\u0441\u0435\u0439 \u041a\u043e\u0441\u0438\u0447\u043a\u0438\u043d \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u0435 \u043f\u043e\u0436\u0430\u043b\u0443\u0439\u0441\u0442\u0430",
        )



def _apply_july_14_2026_overrides(record: PaymentRecord) -> None:
    if _record_date_key(record.date) != "2026-07-14":
        return
    counterparty = _classification_text(record.counterparty)
    invoice = _classification_text(record.invoice_number)
    purpose = _classification_text(record.purpose)
    amount = _amount_digits(record.amount)

    def set_fields(**values: str) -> None:
        for field, value in values.items():
            setattr(record, field, value)

    if "\u043b\u0435 \u043c\u043e\u043d\u043b\u0438\u0434" in counterparty and invoice == "135":
        set_fields(
            invoice_number="135 000-6042835/8675",
            object_name="\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f",
            project="\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f",
            budget_item="",
            responsible="\u0421\u043e\u043b\u043e\u0432\u0446\u043e\u0432 \u041d.",
            purpose="\u041c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b",
        )

    if ("\u044d\u043b\u043a\u043e\u043c \u044d\u043b\u0435\u043a\u0442\u0440\u043e" in counterparty or "\u044d\u043b\u043a\u043e\u043c-\u044d\u043b\u0435\u043a\u0442\u0440\u043e" in counterparty) and invoice in {"01\u0446\u0431 230121", "01\u0446\u0431-230121", "\u0446\u0431 230121", "\u0446\u0431-230121"}:
        _mark_record_as_conversion(record)
        record.invoice_number = "\u0426\u0411-230121"
        record.responsible = "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a"
        record.purpose = "\u0420\u0438\u043d\u0430\u0442 \u043c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b \u044d\u043b\u0435\u043a\u0442\u0440\u043e\u043e\u0431\u043e\u0440\u0443\u0434\u043e\u0432\u0430\u043d\u0438\u0435"

    if "\u0446\u0435\u043d\u0442\u0440\u0441\u0442\u0440\u043e\u0439\u043f\u0440\u043e\u0435\u043a\u0442" in counterparty and invoice in {"05/12/25", ""}:
        set_fields(
            invoice_number="05/12/25",
            object_name="\u0426\u0435\u043d\u0442\u0440\u0441\u0442\u0440\u043e\u0439\u043f\u0440\u043e\u0435\u043a\u0442",
            project="\u0412\u043e\u0437\u0432\u0440\u0430\u0442",
            budget_item="",
            responsible="\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e.",
            purpose="\u0412\u043e\u0437\u0432\u0440\u0430\u0442 \u0438\u0437\u043b\u0438\u0448\u043d\u0435 \u0443\u043f\u043b\u0430\u0447\u0435\u043d\u043d\u043e\u0439 \u0441\u0443\u043c\u043c\u044b \u043f\u043e \u0414\u043e\u0433\u043e\u0432\u043e\u0440\u0443 \u2116 05/12/25 \u0418\u041c\u041A \u043E\u0442 15.12.2025",
        )

    if "\u0430\u0440\u043c\u0430\u0433\u0430\u0437" in counterparty and invoice == "17180":
        _mark_record_as_conversion(record)
        record.invoice_number = "17180"
        record.responsible = "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a"
        record.purpose = "\u0420\u0438\u043d\u0430\u0442  \u043c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b \u0437\u0430\u043f\u043e\u0440\u043d\u0430\u044f \u0430\u0440\u043c\u0430\u0442\u0443\u0440\u0430 \u043a\u0440\u0430\u043d\u044b , \u0437\u0430\u0434\u0432\u0438\u0436\u043a\u0438"

    if "\u0440\u043e\u043f \u043d\u0430 \u0441\u0432\u044f\u0437\u0438" in counterparty and invoice in {"\u04301001144", "a1001144", ""}:
        set_fields(
            invoice_number="\u04101001144",
            object_name="\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a",
            project="\u041c\u0430\u0440\u043a\u0435\u0442\u0438\u043d\u0433",
            budget_item="wazzup",
            responsible="\u0413\u043e\u043d\u0447\u0430\u0440\u043e\u0432 \u0412.",
            purpose="\u041b\u0438\u0446\u0435\u043d\u0437\u0438\u044f \u0441\u0435\u0440\u0432\u0438\u0441\u0430 Wazzup",
        )

    if ("\u0430\u043b\u044c\u0444\u0430 \u0431\u0430\u043d\u043a" in counterparty or "\u0430\u043b\u044c\u0444\u0430-\u0431\u0430\u043d\u043a" in counterparty) and amount == "1" and "\u043b\u0438\u043d\u0438\u044f \u0436\u0438\u0437\u043d\u0438" in purpose:
        set_fields(
            object_name="\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a",
            project="\u0411\u0430\u043d\u043a",
            budget_item="\u041a\u043e\u043c\u0438\u0441\u0441\u0438\u044f",
            responsible="\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a",
        )

    if "\u043c\u0438\u043d\u0442\u0435\u0445\u043f\u0440\u043e\u043c" in counterparty and amount == "150000":
        set_fields(
            object_name="\u041c\u0438\u043d\u0442\u0435\u0445\u043f\u0440\u043e\u043c",
            project="",
            budget_item="\u0420\u0430\u0431\u043e\u0442\u044b",
            responsible="",
        )

def _is_conversion_classification(record: PaymentRecord) -> bool:
    return _classification_text(record.object_name) == "\u043a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f" or _classification_text(record.project) == "\u043a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"


def _apply_july_15_2026_overrides(record: PaymentRecord) -> None:
    if _record_date_key(record.date) != "2026-07-15":
        return
    counterparty = _classification_text(record.counterparty)
    invoice = _classification_text(record.invoice_number)

    def set_fields(**values: str) -> None:
        for field, value in values.items():
            setattr(record, field, value)

    if "\u043a\u043e\u043b\u0438\u0441\u0435\u0446" in counterparty and invoice == "31542130":
        set_fields(
            object_name="\u0426\u0430\u0440\u0435\u0432",
            project="\u041f\u0418\u0420",
            budget_item="\u041f\u043e\u0434\u0440\u044f\u0434\u0447\u0438\u043a",
            responsible="\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e.",
            purpose="\u043f\u043e\u0434\u0440\u044f\u0434\u0447\u0438\u043a, \u041a\u041c\u0414",
        )

    if "\u0442\u0440\u043e\u044f" in counterparty and invoice == "664":
        set_fields(
            object_name="\u042d\u043d\u0435\u0440\u0433\u043e\u043c\u0430\u0448",
            project="\u0410\u0420",
            budget_item="\u0422\u0435\u0445\u043d\u0438\u043a\u0430",
            responsible="\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e.",
        )

    if "\u0432\u0430\u0433\u043d\u0435\u0440" in counterparty and invoice in {"2253882-1", "2253882 1"}:
        set_fields(
            invoice_number="2253882-1",
            object_name="\u0410\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e",
            project="\u041b\u0438\u0447\u043d\u044b\u0435 \u0430\u0432\u0442\u043e",
            budget_item="\u0420\u0435\u043c\u043e\u043d\u0442/\u0422\u041e",
            responsible="\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a",
            purpose="\u0422\u041e \u0427\u0430\u043d\u0433\u0430\u043d",
        )

    if "\u0433\u0440\u0443\u0448\u0435\u0432\u0441\u043a" in counterparty and invoice == "32":
        set_fields(
            object_name="\u0410\u0440\u0435\u043d\u0434\u043e\u0434\u0430\u0442\u0435\u043b\u044c",
            project="\u041a\u041c ( \u041c )",
            budget_item="\u0422\u0435\u0445\u043d\u0438\u043a\u0430",
            responsible="\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e.",
            purpose="\u0433\u0438\u0434\u0440\u043e\u043c\u043e\u043b\u043e\u0442",
        )

    if "\u0438\u0441 \u0430\u0441\u0442\u0440\u0430" in counterparty and invoice == "88908":
        _mark_record_as_conversion(record)
        record.invoice_number = "88908"
        record.responsible = "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a"
        record.purpose = "\u0420\u0438\u043d\u0430\u0442 \u043c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b"

def _mark_record_as_conversion(record: PaymentRecord) -> None:
    record.operation_type = "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"
    record.object_name = "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"
    record.project = "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"
    record.budget_item = ""


def _classification_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold().replace("С‘", "Рµ"))

def apply_mode_defaults(records: Iterable[PaymentRecord], mode: str) -> list[PaymentRecord]:
    mode_key = (mode or "").strip().upper()
    is_mode = mode_key in {"IS", "\u0418\u0421"}
    if not is_mode:
        return [replace(record) for record in records]
    return [
        replace(record, object_name="\u041f\u0421\u041a \u0418\u0421")
        for record in records
    ]

def unmatched_invoice_issues(
    payment_records: Iterable[PaymentRecord],
    invoice_records: Iterable[InvoiceArchiveRecord],
) -> list[HistoryIssue]:
    invoices = list(invoice_records)
    issues: list[HistoryIssue] = []
    for record in payment_records:
        if _is_expected_invoice_link_missing(record):
            continue
        candidate = replace(record)
        if enrich_payment_records_from_archive([candidate], invoices) == 0:
            issues.append(
                HistoryIssue(
                    record.name,
                    "unmatched_invoice",
                    details=f"{record.counterparty} | {record.invoice_number}",
                )
            )
    return issues


def _is_expected_invoice_link_missing(record: PaymentRecord) -> bool:
    counterparty = _classification_text(record.counterparty)
    invoice = _classification_text(record.invoice_number)
    purpose = _classification_text(record.purpose)
    if "\u043f\u043f\u0440" in counterparty and invoice == "77600100024090422":
        return True
    if "\u043f\u043f\u0440" in counterparty and "\u043f\u043f\u0440" in purpose:
        return True
    return False


def write_payment_records_csv(path: Path, records: Iterable[PaymentRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(FINAL_COLUMNS)
        for record in records:
            writer.writerow(record.as_row())


def write_history_issues_csv(path: Path, issues: Iterable[HistoryIssue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["\u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a", "\u0422\u0438\u043f \u043f\u0440\u043e\u0431\u043b\u0435\u043c\u044b", "\u041f\u043e\u043b\u044f", "\u041f\u043e\u0434\u0440\u043e\u0431\u043d\u043e\u0441\u0442\u0438"])
        for issue in issues:
            writer.writerow([issue.source, issue.issue_type, ", ".join(issue.fields), issue.details])

def validate_payment_records(records: Iterable[PaymentRecord]) -> list[HistoryIssue]:
    issues: list[HistoryIssue] = []
    for record in records:
        missing = tuple(label for label, attribute in REQUIRED_PAYMENT_FIELDS if not str(getattr(record, attribute, "") or "").strip())
        if missing:
            issues.append(HistoryIssue(record.name, "missing_payment_fields", missing))
    return issues


def _record_sort_key(record: PaymentRecord) -> tuple[str, str, str, str]:
    return (
        record.date or "9999-99-99",
        (record.name or "").casefold(),
        (record.counterparty or "").casefold(),
        record.amount or "",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()














from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from .dictionaries import normalize_key, normalize_value
from .invoice_archive import (
    _clean_invoice_party,
    extract_linked_message_text,
    extract_message_id,
    extract_message_text,
    extract_message_timestamp_seconds,
    normalize_signature_rules,
    parse_max_signature,
)
from .max_api import FileCandidate, extract_file_candidates, extract_linked_file_candidates
from .max_message_matching import MessageEvidence


ContentSource = bytes | Path
CHAT_COLUMNS = {
    "object_name": "Объект",
    "project": "Проект",
    "budget_item": "Статья бюджета",
    "responsible": "Ответственный",
    "purpose": "Назначение",
}
EXACT_CONFIDENCE = {"exact_body", "exact_link", "unique_pair", "sequence_unique", "document_match", "author_block"}


@dataclass(frozen=True)
class ContentMatch:
    sha256: str
    max_key: str
    confidence: str
    candidate_keys: tuple[str, ...]


def sha256_content(source: ContentSource) -> str:
    digest = hashlib.sha256()
    if isinstance(source, bytes):
        digest.update(source)
        return digest.hexdigest()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def match_by_content(drive_source: ContentSource, max_files: Mapping[str, ContentSource]) -> ContentMatch:
    drive_hash = sha256_content(drive_source)
    candidates = tuple(sorted(key for key, source in max_files.items() if sha256_content(source) == drive_hash))
    if len(candidates) == 1:
        return ContentMatch(drive_hash, candidates[0], "content_exact", candidates)
    if candidates:
        return ContentMatch(drive_hash, "", "ambiguous", candidates)
    return ContentMatch(drive_hash, "", "not_found", ())


def build_message_evidence(
    messages: list[dict[str, Any]],
) -> tuple[list[MessageEvidence], dict[str, FileCandidate]]:
    evidence_by_id: dict[str, MessageEvidence] = {}
    candidates: dict[str, FileCandidate] = {}
    actual_message_ids = {extract_message_id(message) for message in messages if extract_message_id(message)}

    for index, message in enumerate(messages):
        outer_candidates = extract_file_candidates(message, allowed_extensions=())
        candidates.update({candidate.journal_key: candidate for candidate in outer_candidates})
        link = message.get("link") if isinstance(message.get("link"), dict) else {}
        link_type = str(link.get("type") or "").lower()
        linked_message = link.get("message") if isinstance(link.get("message"), dict) else {}
        linked_id = str(linked_message.get("mid") or linked_message.get("message_id") or "")
        outer_id = extract_message_id(message) or f"outer-{index}"
        author_id = _author_id(message)
        _merge_evidence(
            evidence_by_id,
            MessageEvidence(
                message_id=outer_id,
                seq=_message_seq(message, index),
                timestamp=extract_message_timestamp_seconds(message),
                author_id=author_id,
                signature=parse_max_signature(extract_message_text(message)),
                file_ids=tuple(candidate.journal_key for candidate in outer_candidates),
                linked_message_ids=(linked_id,) if linked_id and link_type in {"reply", "forward"} else (),
            ),
        )

        if not linked_message or linked_id in actual_message_ids:
            continue
        linked_candidates = extract_linked_file_candidates(message, allowed_extensions=())
        candidates.update({candidate.journal_key: candidate for candidate in linked_candidates})
        inner_id = linked_id or f"linked-{index}"
        _merge_evidence(
            evidence_by_id,
            MessageEvidence(
                message_id=inner_id,
                seq=_message_seq(message, index),
                timestamp=_timestamp_seconds(linked_message) or extract_message_timestamp_seconds(message),
                author_id=author_id,
                signature=parse_max_signature(extract_linked_message_text(message)),
                file_ids=tuple(candidate.journal_key for candidate in linked_candidates),
            ),
        )

    return list(evidence_by_id.values()), candidates


def build_chat_updated_row(
    headers: list[str],
    row: list[str],
    signature: dict[str, str],
    confidence: str,
    dictionaries: dict[str, Any],
    reference_lists: dict[str, list[str]] | None = None,
    fallback_author: str = "",
    document_fields: dict[str, str] | None = None,
) -> list[str]:
    reference_lists = reference_lists or {}
    document_fields = document_fields or {}
    updated = [*row, *([""] * max(0, len(headers) - len(row)))]
    updated = updated[: len(headers)]
    updated = _normalize_existing_counterparty(headers, updated, dictionaries)
    updated = _normalize_existing_regulated_fields(headers, updated, dictionaries, reference_lists, fallback_author)
    if confidence not in EXACT_CONFIDENCE:
        return updated

    normalized = normalize_signature_rules(signature, dictionaries)
    provided = {key: bool((signature.get(key) or "").strip()) for key in CHAT_COLUMNS}
    conversion_values = dictionaries.get("conversion_values", ["Конвертация"])
    conversion_keys = {normalize_key(str(value)) for value in conversion_values}
    conversion_clears_budget = (
        provided["object_name"] or provided["project"]
    ) and (
        normalize_key(normalized.get("object_name", "")) in conversion_keys
        or normalize_key(normalized.get("project", "")) in conversion_keys
    )
    object_result = normalize_value("objects", normalized.get("object_name", ""), dictionaries, reference_lists.get("Объект"), required=True, strict=True)
    project_result = normalize_value("projects", normalized.get("project", ""), dictionaries, reference_lists.get("Проект"), strict=True)
    budget_result = normalize_value("budget_items", normalized.get("budget_item", ""), dictionaries, reference_lists.get("Статья бюджета"), strict=True)
    responsible_source = normalized.get("responsible", "")
    current_responsible = _cell_value(headers, updated, "Ответственный")
    responsible_fallback_needed = (
        not responsible_source.strip()
        or normalize_key(responsible_source) == "эдо"
    ) and (not current_responsible.strip() or normalize_key(current_responsible) == "эдо")
    if responsible_fallback_needed and fallback_author:
        responsible_source = fallback_author
    responsible_result = normalize_value("responsibles", responsible_source, dictionaries, reference_lists.get("Ответственный"), strict=True)
    values = {
        "object_name": object_result.value,
        "project": project_result.value,
        "budget_item": budget_result.value,
        "responsible": responsible_result.value,
        "purpose": normalized.get("purpose", "") or document_fields.get("purpose", ""),
    }
    status = next(
        (item.status for item in (object_result, project_result, budget_result, responsible_result) if item.status),
        "",
    )
    normalized_results = {
        "object_name": object_result,
        "project": project_result,
        "budget_item": budget_result,
        "responsible": responsible_result,
    }

    for key, column in CHAT_COLUMNS.items():
        accepted_value = key == "purpose" or normalized_results[key].matched
        should_update = (
            (provided[key] and accepted_value)
            or (key == "purpose" and bool(document_fields.get("purpose")) and not provided["purpose"])
            or (key == "budget_item" and conversion_clears_budget)
            or (key == "responsible" and responsible_fallback_needed and bool(fallback_author) and responsible_result.matched)
        )
        if should_update and column in headers:
            updated[headers.index(column)] = values[key]
    for column, key in (("Контрагент", "counterparty"), ("Сумма", "amount")):
        value = (document_fields.get(key) or "").strip()
        current = _cell_value(headers, updated, column)
        if value and column in headers and (
            (key == "counterparty" and _should_update_counterparty(current, value))
            or (key == "amount" and _should_update_amount(current, value))
        ):
            updated[headers.index(column)] = value
    if any(provided[key] for key in normalized_results) and "Статус разбора" in headers:
        updated[headers.index("Статус разбора")] = status
    return updated


def _cell_value(headers: list[str], row: list[str], column: str) -> str:
    return str(row[headers.index(column)] or "") if column in headers else ""


def _mark_row_for_review(
    headers: list[str],
    row: list[str],
    dictionaries: dict[str, Any],
) -> None:
    column = "Статус разбора"
    if column in headers:
        row[headers.index(column)] = dictionaries.get("unresolved_status", "Нужно разобрать")


def _normalize_existing_counterparty(
    headers: list[str],
    row: list[str],
    dictionaries: dict[str, Any],
) -> list[str]:
    column = "Контрагент"
    if column not in headers:
        return row
    current = _cell_value(headers, row, column).strip()
    if not current:
        return row
    cleaned = _clean_invoice_party(current)
    if _obvious_counterparty_garbage(current):
        if cleaned and normalize_key(current).startswith("платежа ооо"):
            row[headers.index(column)] = cleaned
            return row
        row[headers.index(column)] = ""
        _mark_row_for_review(headers, row, dictionaries)
        return row
    if cleaned:
        row[headers.index(column)] = cleaned
    return row


def _normalize_existing_regulated_fields(
    headers: list[str],
    row: list[str],
    dictionaries: dict[str, Any],
    reference_lists: dict[str, list[str]],
    fallback_author: str = "",
) -> list[str]:
    existing = {
        "object_name": _cell_value(headers, row, "Объект"),
        "project": _cell_value(headers, row, "Проект"),
        "budget_item": _cell_value(headers, row, "Статья бюджета"),
        "responsible": _cell_value(headers, row, "Ответственный"),
        "purpose": _cell_value(headers, row, "Назначение"),
    }
    normalized = normalize_signature_rules(existing, dictionaries)
    specs = (
        ("object_name", "Объект", "objects"),
        ("project", "Проект", "projects"),
        ("budget_item", "Статья бюджета", "budget_items"),
        ("responsible", "Ответственный", "responsibles"),
    )
    for key, column, category in specs:
        if column not in headers:
            continue
        source = normalized.get(key, "")
        if not existing[key].strip() and not source.strip():
            continue
        references = reference_lists.get(column) or []
        result = normalize_value(category, source, dictionaries, references, strict=True)
        if key == "responsible" and not result.matched and fallback_author:
            result = normalize_value(category, fallback_author, dictionaries, references, strict=True)
        if result.matched:
            row[headers.index(column)] = result.value
        elif references:
            row[headers.index(column)] = ""
            _mark_row_for_review(headers, row, dictionaries)
    if "Назначение" in headers and normalized.get("purpose", "") != existing["purpose"]:
        row[headers.index("Назначение")] = normalized.get("purpose", "")
    return row

def _should_update_counterparty(old: str, new: str) -> bool:
    if not _strong_legal_counterparty(new):
        return False
    if not old.strip() or _obvious_counterparty_garbage(old):
        return True
    return _counterparty_identity(old) == _counterparty_identity(new)


def _strong_legal_counterparty(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value or "").strip()
    if len(normalized) > 120 or re.search(r"\b(?:ИНН|КПП|платежа|договор)\b", normalized, re.IGNORECASE):
        return False
    return bool(re.match(r'^(?:ИП|ООО|АО|ПАО|ЗАО|ОАО|АС)\s+[А-ЯЁA-Z«"“]', normalized, re.IGNORECASE))


def _obvious_counterparty_garbage(value: str) -> bool:
    normalized = normalize_key(value)
    markers = (
        "вправе",
        "обязательном порядке",
        "назначение платежа",
        "получатель платежа",
        "платежа ооо",
        "без ндс ооо",
        "условиях",
        "возврате товара",
    )
    amount_prefix = re.match(r"^\s*\d[\d\s]*[,.]\d{2}\s*(?:₽|р\.?\b)", value, re.IGNORECASE)
    return (
        len(value) > 140
        or any(marker in normalized for marker in markers)
        or bool(amount_prefix)
        or not re.search(r"[А-Яа-яЁё]", value)
    )


def _counterparty_identity(value: str) -> str:
    value = re.sub(r"^Индивидуальный\s+предприниматель\b", "ИП", value, flags=re.IGNORECASE)
    value = re.sub(r"^Общество\s+с\s+ограниченной\s+ответственностью\b", "ООО", value, flags=re.IGNORECASE)
    return re.sub(r"[^0-9a-zа-я]+", "", value.lower().replace("ё", "е"))


def _should_update_amount(old: str, new: str) -> bool:
    if not old.strip():
        return True
    try:
        return _amount_decimal(old) == _amount_decimal(new)
    except InvalidOperation:
        return False


def _amount_decimal(value: str) -> Decimal:
    normalized = re.sub(r"\s+", "", value).replace(",", ".")
    return Decimal(normalized)


def _merge_evidence(target: dict[str, MessageEvidence], incoming: MessageEvidence) -> None:
    current = target.get(incoming.message_id)
    if current is None:
        target[incoming.message_id] = incoming
        return
    target[incoming.message_id] = MessageEvidence(
        message_id=current.message_id,
        seq=current.seq or incoming.seq,
        timestamp=current.timestamp if current.timestamp is not None else incoming.timestamp,
        author_id=current.author_id or incoming.author_id,
        signature=current.signature or incoming.signature,
        file_ids=tuple(dict.fromkeys((*current.file_ids, *incoming.file_ids))),
        linked_message_ids=tuple(dict.fromkeys((*current.linked_message_ids, *incoming.linked_message_ids))),
    )


def _message_seq(message: dict[str, Any], fallback: int) -> int:
    body = message.get("body")
    if isinstance(body, dict):
        return _body_seq(body, fallback)
    return _integer(message.get("seq"), fallback)


def _author_id(message: dict[str, Any]) -> str:
    sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
    return str(sender.get("name") or sender.get("user_id") or "").strip()


def _body_seq(body: dict[str, Any], fallback: int) -> int:
    return _integer(body.get("seq"), fallback)


def _integer(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _timestamp_seconds(message: dict[str, Any]) -> float | None:
    value = message.get("timestamp") or message.get("created_at") or message.get("time")
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    return timestamp / 1000 if timestamp > 10_000_000_000 else timestamp

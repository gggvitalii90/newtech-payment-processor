from __future__ import annotations

import re
from dataclasses import dataclass, field

from .dictionaries import normalize_key


@dataclass(frozen=True)
class MessageEvidence:
    message_id: str
    seq: int
    timestamp: float | None
    author_id: str = ""
    signature: dict[str, str] = field(default_factory=dict)
    file_ids: tuple[str, ...] = ()
    linked_message_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SignatureMatch:
    file_id: str
    signature_message_id: str
    confidence: str
    signature: dict[str, str]


def match_signatures(
    evidence: list[MessageEvidence],
    window_seconds: float = 30 * 60,
) -> dict[str, SignatureMatch]:
    messages_by_id = {item.message_id: item for item in evidence if item.message_id}
    files = {
        file_id: item
        for item in evidence
        for file_id in item.file_ids
        if file_id
    }
    matches = {file_id: _ambiguous(file_id) for file_id in files}
    matched_files: set[str] = set()
    used_signatures: set[str] = set()

    for file_id, item in files.items():
        if item.signature:
            matches[file_id] = SignatureMatch(file_id, item.message_id, "exact_body", dict(item.signature))
            matched_files.add(file_id)
            used_signatures.add(item.message_id)

    link_candidates: dict[str, list[MessageEvidence]] = {file_id: [] for file_id in files}
    for signature_item in evidence:
        if not signature_item.signature or signature_item.message_id in used_signatures:
            continue
        for linked_message_id in signature_item.linked_message_ids:
            target = messages_by_id.get(linked_message_id)
            if target is not None:
                for file_id in target.file_ids:
                    if file_id not in matched_files:
                        link_candidates[file_id].append(signature_item)
    for file_id, file_item in files.items():
        if file_id in matched_files:
            continue
        for linked_message_id in file_item.linked_message_ids:
            target = messages_by_id.get(linked_message_id)
            if target is not None and target.signature and target.message_id not in used_signatures:
                link_candidates[file_id].append(target)

    for file_id, candidates in link_candidates.items():
        unique = _unique_messages(candidates)
        if file_id not in matched_files and len(unique) == 1:
            item = unique[0]
            matches[file_id] = SignatureMatch(file_id, item.message_id, "exact_link", dict(item.signature))
            matched_files.add(file_id)
            used_signatures.add(item.message_id)

    remaining_files = {file_id: item for file_id, item in files.items() if file_id not in matched_files}
    remaining_signatures = [
        item
        for item in evidence
        if item.signature and item.message_id not in used_signatures and not item.file_ids
    ]
    candidates_by_file = {
        file_id: [item for item in remaining_signatures if _within_window(file_item, item, window_seconds)]
        for file_id, file_item in remaining_files.items()
    }
    files_by_signature: dict[str, list[str]] = {item.message_id: [] for item in remaining_signatures}
    for file_id, candidates in candidates_by_file.items():
        for item in candidates:
            files_by_signature[item.message_id].append(file_id)

    for file_id, candidates in candidates_by_file.items():
        if len(candidates) != 1:
            continue
        item = candidates[0]
        if len(files_by_signature[item.message_id]) != 1:
            continue
        matches[file_id] = SignatureMatch(file_id, item.message_id, "unique_pair", dict(item.signature))

    return matches


def match_signatures_by_sequence(
    evidence: list[MessageEvidence],
    document_fields_by_file: dict[str, dict[str, str]] | None = None,
    window_seconds: float = 30 * 60,
) -> dict[str, SignatureMatch]:
    document_fields_by_file = document_fields_by_file or {}
    ordered = sorted(evidence, key=lambda item: (item.seq, item.timestamp or 0, item.message_id))
    positions = {item.message_id: index for index, item in enumerate(ordered)}
    messages_by_id = {item.message_id: item for item in ordered if item.message_id}
    files = {file_id: item for item in ordered for file_id in item.file_ids if file_id}
    matches = {file_id: _ambiguous(file_id) for file_id in files}
    matched_files: set[str] = set()
    used_signatures: set[str] = set()

    for file_id, item in files.items():
        if item.signature:
            matches[file_id] = SignatureMatch(file_id, item.message_id, "exact_body", dict(item.signature))
            matched_files.add(file_id)
            used_signatures.add(item.message_id)

    link_candidates: dict[str, list[MessageEvidence]] = {file_id: [] for file_id in files}
    for signature_item in ordered:
        if not signature_item.signature or signature_item.message_id in used_signatures:
            continue
        for linked_message_id in signature_item.linked_message_ids:
            target = messages_by_id.get(linked_message_id)
            if target is not None:
                for file_id in target.file_ids:
                    if file_id not in matched_files:
                        link_candidates[file_id].append(signature_item)
    for file_id, file_item in files.items():
        if file_id in matched_files:
            continue
        for linked_message_id in file_item.linked_message_ids:
            target = messages_by_id.get(linked_message_id)
            if target is not None and target.signature and target.message_id not in used_signatures:
                link_candidates[file_id].append(target)
    for file_id, candidates in link_candidates.items():
        unique = _unique_messages(candidates)
        if file_id not in matched_files and len(unique) == 1:
            item = unique[0]
            matches[file_id] = SignatureMatch(file_id, item.message_id, "exact_link", dict(item.signature))
            matched_files.add(file_id)
            used_signatures.add(item.message_id)

    pending_by_author: dict[str, list[str]] = {}
    for item in ordered:
        if item.author_id and item.file_ids:
            pending_by_author.setdefault(item.author_id, []).extend(
                file_id for file_id in item.file_ids if file_id not in matched_files
            )
        if not item.author_id or not item.signature or item.message_id in used_signatures:
            continue
        pending = [
            file_id for file_id in pending_by_author.get(item.author_id, [])
            if _within_window(files[file_id], item, window_seconds)
        ]
        if not pending:
            pending_by_author[item.author_id] = []
            continue
        for file_id in pending:
            matches[file_id] = SignatureMatch(file_id, item.message_id, "author_block", dict(item.signature))
            matched_files.add(file_id)
        pending_by_author[item.author_id] = []
        used_signatures.add(item.message_id)

    edges: dict[str, set[str]] = {}
    signatures_by_id = {
        item.message_id: item
        for item in ordered
        if item.signature and not item.file_ids and item.message_id not in used_signatures
    }
    for file_id, file_item in files.items():
        if file_id in matched_files:
            continue
        position = positions[file_item.message_id]
        candidates: list[MessageEvidence] = []
        for direction in (-1, 1):
            index = position + direction
            while 0 <= index < len(ordered):
                item = ordered[index]
                if not _within_window(file_item, item, window_seconds):
                    break
                if item.message_id in signatures_by_id:
                    candidates.append(item)
                    break
                index += direction
        candidates = _filter_document_candidates(candidates, document_fields_by_file.get(file_id, {}))
        edges[file_id] = {item.message_id for item in candidates}

    while True:
        singleton_claims: dict[str, list[str]] = {}
        for file_id, signature_ids in edges.items():
            if len(signature_ids) == 1:
                signature_id = next(iter(signature_ids))
                singleton_claims.setdefault(signature_id, []).append(file_id)
        forced = [
            (file_ids[0], signature_id)
            for signature_id, file_ids in singleton_claims.items()
            if len(file_ids) == 1
        ]
        if not forced:
            break
        for file_id, signature_id in forced:
            if file_id not in edges or signature_id not in edges[file_id]:
                continue
            item = signatures_by_id[signature_id]
            confidence = "document_match" if _document_score(item.signature, document_fields_by_file.get(file_id, {}))[0] else "sequence_unique"
            matches[file_id] = SignatureMatch(file_id, signature_id, confidence, dict(item.signature))
            del edges[file_id]
            for other_candidates in edges.values():
                other_candidates.discard(signature_id)

    return matches


def _ambiguous(file_id: str) -> SignatureMatch:
    return SignatureMatch(file_id, "", "ambiguous", {})


def _within_window(first: MessageEvidence, second: MessageEvidence, window_seconds: float) -> bool:
    if first.timestamp is None or second.timestamp is None:
        return False
    return abs(first.timestamp - second.timestamp) <= window_seconds


def _unique_messages(items: list[MessageEvidence]) -> list[MessageEvidence]:
    return list({item.message_id: item for item in items}.values())


def _filter_document_candidates(
    candidates: list[MessageEvidence],
    document_fields: dict[str, str],
) -> list[MessageEvidence]:
    if not candidates or not document_fields:
        return candidates
    scored = [(item, *_document_score(item.signature, document_fields)) for item in candidates]
    compatible = [entry for entry in scored if not (entry[2] and entry[1] == 0)]
    if compatible:
        scored = compatible
    best_score = max((entry[1] for entry in scored), default=0)
    best = [entry[0] for entry in scored if entry[1] == best_score]
    return best if best_score > 0 and len(best) == 1 else [entry[0] for entry in scored]


def _document_score(signature: dict[str, str], document_fields: dict[str, str]) -> tuple[int, int]:
    score = 0
    contradictions = 0
    for key, weight in (("counterparty", 3), ("invoice_number", 4), ("invoice_date", 2), ("amount", 3)):
        signature_value = _normalized_document_value(key, signature.get(key, ""))
        document_value = _normalized_document_value(key, document_fields.get(key, ""))
        if not signature_value or not document_value:
            continue
        matches = signature_value == document_value
        if key == "counterparty":
            matches = matches or signature_value in document_value or document_value in signature_value
        if matches:
            score += weight
        else:
            contradictions += 1
    return score, contradictions


def _normalized_document_value(key: str, value: str) -> str:
    normalized = normalize_key(value or "")
    if key in {"invoice_number", "invoice_date", "amount"}:
        return re.sub(r"[^0-9a-zа-я]", "", normalized)
    return re.sub(r"[^0-9a-zа-я]", "", normalized)

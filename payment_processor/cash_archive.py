from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from .dictionaries import normalize_key, normalize_value
from .invoice_archive import (
    extract_message_author,
    extract_message_id,
    extract_message_text,
    format_max_timestamp,
    normalize_signature_rules,
)
from .models import PaymentRecord


CASH_ARCHIVE_COLUMNS = [
    "Дата MAX",
    "Поток",
    "Чат",
    "Автор",
    "Тип операции",
    "Тип оплаты",
    "Контрагент",
    "Объект",
    "Проект",
    "Статья бюджета",
    "Ответственный",
    "Назначение",
    "Сумма",
    "MAX message_id",
    "Статус разбора",
    "Исходный текст",
]


@dataclass
class CashArchiveRecord:
    max_date: str
    flow: str
    chat: str
    author: str
    operation_type: str
    payment_type: str
    counterparty: str
    object_name: str
    project: str
    budget_item: str
    responsible: str
    purpose: str
    amount: str
    max_message_id: str
    analysis_status: str
    source_text: str

    def as_row(self) -> list[str]:
        return [
            self.max_date,
            self.flow,
            self.chat,
            self.author,
            self.operation_type,
            self.payment_type,
            self.counterparty,
            self.object_name,
            self.project,
            self.budget_item,
            self.responsible,
            self.purpose,
            self.amount,
            self.max_message_id,
            self.analysis_status,
            self.source_text,
        ]


def create_cash_archive_records(
    messages: list[dict[str, Any]],
    chat_id: str,
    dictionaries: dict[str, Any],
    reference_lists: dict[str, list[str]] | None = None,
) -> list[CashArchiveRecord]:
    reference_lists = reference_lists or {}
    records: list[CashArchiveRecord] = []
    for message in sorted(messages, key=lambda item: item.get("timestamp") or item.get("created_at") or item.get("time") or 0):
        text = extract_message_text(message)
        parsed_entries = parse_cash_message_entries(text)
        if not parsed_entries:
            continue
        author = extract_message_author(message)
        for parsed in parsed_entries:
            signature = normalize_cash_signature_rules(normalize_signature_rules(parsed, dictionaries, archive_rules=False), dictionaries)
            object_result = normalize_value("objects", signature.get("object_name", ""), dictionaries, reference_lists.get("Объект"), required=True)
            project_result = normalize_value("projects", signature.get("project", ""), dictionaries, reference_lists.get("Проект"))
            budget_result = normalize_value("budget_items", signature.get("budget_item", ""), dictionaries, reference_lists.get("Статья бюджета"))
            responsible_value = signature.get("responsible", "") or default_cash_responsible(author, dictionaries)
            responsible_result = normalize_value("responsibles", responsible_value, dictionaries, reference_lists.get("Ответственный"))
            if not responsible_result.value and responsible_value:
                responsible_result = normalize_value("responsibles", responsible_value, dictionaries)
            counterparty_result = normalize_value("counterparties", signature.get("counterparty", ""), dictionaries)
            records.append(
                CashArchiveRecord(
                    max_date=format_max_timestamp(message.get("timestamp") or message.get("created_at") or message.get("time"))[:10],
                    flow="ПСК нал",
                    chat=chat_id,
                    author=author,
                    operation_type=signature.get("operation_type", ""),
                    payment_type='Наличная',
                    counterparty=counterparty_result.value,
                    object_name=object_result.value,
                    project=project_result.value,
                    budget_item=budget_result.value,
                    responsible=responsible_result.value,
                    purpose=signature.get("purpose", ""),
                    amount=signature.get("amount", ""),
                    max_message_id=extract_message_id(message),
                    analysis_status=first_status([object_result.status, project_result.status, budget_result.status, responsible_result.status]),
                    source_text=text,
                )
            )
    return records


def default_cash_responsible(author: str, dictionaries: dict[str, Any]) -> str:
    if _norm(author) == 'кирилл' and 'Родин.К' in dictionaries.get("responsibles", {}):
        return 'Родин.К'
    return author


def cash_records_to_payment_records(records: list[CashArchiveRecord]) -> list[PaymentRecord]:
    return [
        PaymentRecord(
            name=record.max_message_id,
            date=(record.max_date or "")[:10],
            operation_type=record.operation_type,
            payment_type=record.payment_type,
            bank="",
            counterparty=record.counterparty,
            invoice_number="",
            object_name=record.object_name,
            project=record.project,
            budget_item=record.budget_item,
            responsible=record.responsible,
            purpose=record.purpose,
            invoice_link="",
            amount=record.amount,
        )
        for record in records
    ]


def parse_cash_message(text: str) -> dict[str, str] | None:
    entries = parse_cash_message_entries(text)
    return entries[0] if entries else None


def parse_cash_message_entries(text: str) -> list[dict[str, str]]:
    if not text or _is_chat_noise(text):
        return []
    normalized = _norm(text)
    expense = _u(r"\u0440\u0430\u0441\u0445\u043e\u0434")
    income = _u(r"\u043f\u0440\u0438\u0445\u043e\u0434")
    conversion = _u(r"\u043a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f")
    object_label = _u(r"\u043e\u0431\u044a\u0435\u043a\u0442")
    accountable = _u(r"\u043f\u043e\u0434 \u043e\u0442\u0447\u0435\u0442")
    balance = _u(r"\u043e\u0441\u0442\u0430\u0442\u043e\u043a")
    balance_typo = _u(r"\u043e\u0442\u0430\u0442\u043e\u043a")

    if normalized.startswith(conversion) or normalized.startswith(f"{income} {conversion}"):
        parsed = parse_standalone_cash_conversion(text)
        return [parsed] if parsed else []
    if (balance in normalized or balance_typo in normalized) and expense not in normalized and income not in normalized:
        return []
    if accountable in normalized and object_label not in normalized:
        parsed = parse_cash_accountable_message(text)
        return [parsed] if parsed else []
    if expense in normalized or object_label in normalized:
        if has_cash_field_labels(text):
            structured_entries = parse_cash_structured_message_entries(text)
            if structured_entries:
                return structured_entries
        freeform_entries = parse_cash_freeform_entries(text)
        if freeform_entries:
            return freeform_entries
        parsed = parse_cash_structured_message(text)
        return [parsed] if parsed else []
    if income in normalized:
        freeform_entries = parse_cash_freeform_entries(text)
        if freeform_entries:
            return freeform_entries
        parsed = parse_cash_accountable_message(text)
        return [parsed] if parsed else []
    return []


def _u(value: str) -> str:
    return value.encode("ascii").decode("raw_unicode_escape")


def parse_standalone_cash_conversion(text: str) -> dict[str, str] | None:
    amount = extract_cash_amount(text) or extract_unsigned_cash_amount(text)
    if not amount:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    prefix = _u(r"^(?:\u043f\u0440\u0438\u0445\u043e\u0434\s+)?\u043a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f\s*")
    conversion = _u(r"\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f")
    first_line = re.sub(prefix, "", lines[0], flags=re.IGNORECASE).strip(" .,-")
    detail_lines = [first_line]
    for line in lines[1:]:
        if extract_cash_amount(line) or extract_unsigned_cash_amount(line):
            continue
        detail_lines.append(line.strip(" .,-"))
    purpose = ", ".join(part for part in detail_lines if part)
    return {
        "operation_type": conversion,
        "object_name": conversion,
        "project": conversion,
        "budget_item": "",
        "purpose": purpose,
        "amount": amount.lstrip("+"),
    }


def has_cash_field_labels(text: str) -> bool:
    return any(re.search(rf"(^|\n)\s*{label}\s*:", text, re.IGNORECASE) for label in ["объект", "проект", "статья", "назначение", "контрагент"])


def parse_cash_freeform_entries(text: str) -> list[dict[str, str]]:
    normalized = _norm(text)
    if "расход" not in normalized and "рвсход" not in normalized and "приход" not in normalized:
        return []
    entries: list[dict[str, str]] = []
    for sign, value, description in re.findall(r"([+-])\s*([\d\s]+(?:[,.]\d{1,2})?)\s*([^\n+-]*?)(?=\n\s*(?:[+-]\s*\d|[Оо]статок\b|[Оо]таток\b)|\Z)", text, re.IGNORECASE | re.DOTALL):
        parsed = parse_cash_freeform_entry(sign, value, description, normalized)
        if parsed:
            entries.append(parsed)
    if not entries and "приход" in normalized:
        parsed = parse_cash_multiline_income(text)
        if parsed:
            entries.append(parsed)
    return entries


def parse_cash_freeform_entry(sign: str, value: str, description: str, normalized_message: str) -> dict[str, str] | None:
    amount = re.sub(r"\s+", "", value).replace(".", ",")
    amount = f"+{amount}" if sign == "+" else amount
    description = re.split(r"\b[Оо]т?аток\b", description)[0]
    description = re.sub(r"\s+", " ", description).strip(" ,.;")
    if not description:
        return None
    parts = [part.strip(" .;") for part in re.split(r"[,/]", description) if part.strip(" .;")]
    if not parts:
        return None
    parts = expand_freeform_shorthand_parts(parts)
    parts = merge_freeform_project_tokens(parts)
    result = {"operation_type": "Приход" if sign == "+" or "приход" in normalized_message else "Расход", "amount": amount, "object_name": parts[0]}
    if len(parts) > 1:
        result["project"] = parts[1]
    if len(parts) > 2:
        budget_item, purpose = split_freeform_budget_and_purpose(parts[2])
        result["budget_item"] = budget_item
        if purpose:
            result["purpose"] = purpose
    if len(parts) > 3:
        result["purpose"] = ", ".join(parts[3:])
    return result


def parse_cash_multiline_income(text: str) -> dict[str, str] | None:
    amount = extract_cash_amount(text)
    if not amount or not amount.startswith("+"):
        return None
    lines = [line.strip(" ,.;") for line in text.splitlines() if line.strip(" ,.;")]
    content_lines = []
    for line in lines:
        normalized_line = _norm(line)
        if normalized_line == "приход" or normalized_line.startswith("а где ") or normalized_line.startswith("почему "):
            continue
        if "+" in line or "-" in line:
            continue
        content_lines.append(line)
    if not content_lines:
        return None
    result = {"operation_type": "Приход", "amount": amount, "object_name": content_lines[0]}
    if len(content_lines) > 1:
        result["purpose"] = ", ".join(content_lines[1:])
    return result


def merge_freeform_project_tokens(parts: list[str]) -> list[str]:
    if len(parts) >= 3 and _norm(parts[1]) == "км" and _norm(parts[2]) in {"пр", "п р"}:
        return [parts[0], "КМ (ПР)", *parts[3:]]
    return parts


def expand_freeform_shorthand_parts(parts: list[str]) -> list[str]:
    if len(parts) == 2 and re.match(r"^пр\s*[.]?\s*фот$", _norm(parts[0])):
        return ["пр", "фот", parts[1]]
    if len(parts) == 1:
        normalized = _norm(parts[0])
        if normalized.startswith("командировочные расходы"):
            purpose = parts[0][len("Командировочные расходы") :].strip(" .,-")
            return ["пск", "офис", "командировочные расходы", purpose] if purpose else ["пск", "офис", "командировочные расходы"]
        if normalized == "комиссия":
            return ["пск", "банк", "комиссия"]
    return parts


def split_freeform_budget_and_purpose(value: str) -> tuple[str, str]:
    if re.match(r"^(?:аванс\s+)?зп\b", value, re.IGNORECASE):
        return "Зарплата", value
    match = re.search(r"\b((?:аванс\s+)?зп\b.*)$", value, re.IGNORECASE)
    if not match:
        return value, ""
    budget_item = value[: match.start()].strip(" .,-")
    purpose = match.group(1).strip(" .,-")
    return (value, "") if not budget_item else (budget_item, purpose)


def parse_cash_structured_message_entries(text: str) -> list[dict[str, str]]:
    blocks = split_cash_structured_blocks(text)
    if len(blocks) <= 1:
        parsed = parse_cash_structured_message(text)
        return [parsed] if parsed else []
    entries: list[dict[str, str]] = []
    for block in blocks:
        parsed = parse_cash_structured_message(block)
        if parsed:
            entries.append(parsed)
    return entries


def split_cash_structured_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current_lines: list[str] = []
    current_operation = ""

    def flush() -> None:
        nonlocal current_lines
        if not current_lines:
            return
        block_lines = ([current_operation] if current_operation else []) + current_lines
        block = "\n".join(line for line in block_lines if line.strip()).strip()
        if block:
            blocks.append(block)
        current_lines = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        operation = _cash_operation_header(line)
        if operation:
            flush()
            current_operation = operation
            continue
        field_key = ""
        match = re.match(r"^([^:]+):\s*(.*)$", line)
        if match:
            field_key = cash_field_key(match.group(1))
        if field_key == "object_name" and _structured_block_has_entry_data(current_lines):
            flush()
        current_lines.append(raw_line)

    flush()
    return blocks


def _cash_operation_header(line: str) -> str:
    match = re.match(r"^\s*(Расход|Приход)\b", line, re.IGNORECASE)
    if not match:
        return ""
    rest = line[match.end():].strip()
    if rest:
        return ""
    return match.group(1)


def _structured_block_has_entry_data(lines: list[str]) -> bool:
    if not lines:
        return False
    joined = "\n".join(lines)
    if extract_cash_amount(joined):
        return True
    return any(
        cash_field_key(match.group(1)) == "object_name"
        for line in lines
        if (match := re.match(r"^([^:]+):", line.strip()))
    )


def parse_cash_structured_message(text: str) -> dict[str, str] | None:
    result: dict[str, str] = {}
    if re.search(r"(^|\n)\s*расход\b", text, re.IGNORECASE):
        result["operation_type"] = "Расход"
    if re.search(r"(^|\n)\s*приход\b", text, re.IGNORECASE):
        result["operation_type"] = "Приход"
    for raw_line in text.splitlines():
        match = re.match(r"^([^:]+):\s*(.*)$", raw_line.strip())
        if not match:
            continue
        key = cash_field_key(match.group(1))
        if key:
            result[key] = match.group(2).strip().rstrip(".")
    amount = extract_cash_amount(text)
    if amount:
        result["amount"] = amount
    if not result.get("operation_type") and amount:
        result["operation_type"] = "Приход" if amount.startswith("+") else "Расход"
    if not result.get("operation_type"):
        result["operation_type"] = "Расход"
    if not any(result.get(key) for key in ["object_name", "project", "budget_item", "purpose", "counterparty", "amount"]):
        return None
    return result


def parse_cash_accountable_message(text: str) -> dict[str, str] | None:
    if "приход" not in _norm(text):
        return None
    amount = extract_cash_amount(text)
    if not amount:
        return None
    source_match = re.search(r"под\s+отчет\s+с\s+([А-ЯA-ZЁа-яa-zё][^\n\r+-]*)", text, re.IGNORECASE)
    if source_match:
        return {"operation_type": "Приход", "payment_type": "Наличные", "responsible": "", "amount": amount, "purpose": "Под отчет", "object_name": source_match.group(1).strip(" .,;")}
    responsible_match = re.search(r"под\s+отчет\s+(?:\+\s*)?(?:родину|родин|([А-ЯA-ZЁа-яa-zё]+))", text, re.IGNORECASE)
    responsible = "Родин" if re.search(r"родин", text, re.IGNORECASE) else (responsible_match.group(1) if responsible_match else "")
    return {"operation_type": "Приход", "payment_type": "Наличные", "responsible": responsible, "amount": amount, "purpose": "Под отчет"}


def normalize_cash_signature_rules(signature: dict[str, str], dictionaries: dict[str, Any]) -> dict[str, str]:
    result = dict(signature)
    object_key = _norm(result.get("object_name", ""))
    project_key = _norm(result.get("project", ""))
    budget_key = _norm(result.get("budget_item", ""))
    purpose_key = _norm(result.get("purpose", ""))
    conversion_values = {_norm(value) for value in dictionaries.get("conversion_values", [])}
    if object_key in conversion_values or project_key in conversion_values:
        result.update({"operation_type": "Конвертация", "object_name": "Конвертация", "project": "Конвертация", "budget_item": ""})
        return result
    if object_key.startswith("конвертация"):
        details = [result.get("object_name", ""), result.get("project", ""), result.get("budget_item", ""), result.get("purpose", "")]
        result.update({"operation_type": "Конвертация", "object_name": "Конвертация", "project": "Конвертация", "budget_item": "", "purpose": ", ".join(part.strip() for part in details if part.strip())})
        return result
    if 'разгруз' in budget_key and ('кран' in purpose_key or 'метал' in budget_key or 'метал' in purpose_key):
        result["budget_item"] = 'Техника'
        budget_key = 'техника'
    if 'развоз' in budget_key:
        result["budget_item"] = 'Развозка сотрудников'
        budget_key = 'развозка сотрудников'
    if object_key in {"\u043f\u0441\u043a", "\u043f\u0441\u043a \u043d\u044c\u044e\u0442\u0435\u043a"} and project_key == "\u043a\u0440\u0435\u0434\u0438\u0442" and budget_key == "\u0430\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e" and "\u043f\u043e\u0434\u044a\u0435\u043c\u043d\u0438\u043a" in purpose_key:
        result.update({"object_name": "\u0410\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e", "project": "\u041f\u043e\u0434\u044a\u0435\u043c\u043d\u0438\u043a", "budget_item": "\u041a\u0440\u0435\u0434\u0438\u0442"})
        return result
    if object_key in {"пр", "производство"} and project_key == "фот" and not budget_key:
        result["budget_item"] = "Зарплата"
    if result.get("operation_type") == "Приход" and object_key in {"пр", "производство"} and "метал" in purpose_key:
        result.update({"object_name": "Производство", "project": "Реализация", "budget_item": "Металлолом"})
        return result
    if result.get("operation_type") == "Приход" and object_key == "кран":
        result.update({"object_name": "Автохозяйство", "project": "Кран", "budget_item": "Работы"})
        return result
    if result.get("operation_type") == "Приход" and not budget_key:
        result["budget_item"] = "Работы"
    if project_key in {"автохояйство", "автохозяйство"} and (purpose_key == "ремонт крана" or "кран" in purpose_key):
        result.update({"object_name": "Автохозяйство", "project": "Кран", "budget_item": "Топливо" if budget_key == "топливо" else "Ремонт ТО"})
    if object_key == "усть луга" and budget_key == "ремонт трамбовки":
        result["budget_item"] = "Ремонт ТО"
    if object_key == "ренессанс" and (project_key == "подрядчик" or budget_key == "юрист"):
        result.update({"project": "Офис", "budget_item": "Юридические услуги"})
    if object_key == "риверботс" and project_key == "метизы":
        result["project"] = "КМ (М)"
        if not budget_key or budget_key == "метизы":
            result["budget_item"] = "Расходники"
    return result


def extract_cash_amount(text: str) -> str:
    matches = re.findall(r"([+-])\s*([\d\s]+(?:[,.]\d{1,2})?)\s*(?:р\.?|руб\.?)?", text, re.IGNORECASE)
    if not matches:
        return ""
    sign, value = matches[-1]
    cleaned = re.sub(r"\s+", "", value).replace(".", ",")
    return f"{sign}{cleaned}" if sign == "+" else cleaned


def extract_unsigned_cash_amount(text: str) -> str:
    matches = re.findall(r"(?<![\d+-])([1-9]\d{0,2}(?:[ \u00a0]\d{3})+(?:[,.]\d{1,2})?|[1-9]\d{3,}(?:[,.]\d{1,2})?)", text, re.IGNORECASE)
    if not matches:
        return ""
    return re.sub(r"[ \u00a0]+", "", matches[-1]).replace(".", ",")


def cash_field_key(label: str) -> str:
    return {"объект": "object_name", "проект": "project", "статья": "budget_item", "статья бюджета": "budget_item", "назначение": "purpose", "ответственный": "responsible", "контрагент": "counterparty"}.get(_norm(label), "")


def write_cash_archive_xlsx(path: Path, records: list[CashArchiveRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Наличка"
    sheet.append(CASH_ARCHIVE_COLUMNS)
    for record in records:
        sheet.append(record.as_row())
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    workbook.save(path)


def first_status(statuses: list[str]) -> str:
    for status in statuses:
        if status:
            return status
    return ""


def _is_chat_noise(text: str) -> bool:
    normalized = " ".join(_norm(text).split())
    if not normalized:
        return True
    if normalized.startswith("/start") or "нажмите кнопку" in normalized:
        return True
    if normalized.startswith("у меня "):
        return True
    if "хочу расходы занести" in normalized or "бот в помощь" in normalized:
        return True
    if "в таблице стоит" in normalized or "приход пишется со знаком" in normalized:
        return True
    return normalized in {"остаток ноль"}


def _norm(value: str) -> str:
    return normalize_key(value).replace("ё", "е")

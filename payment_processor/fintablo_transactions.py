from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .dictionaries import normalize_key
from .models import PaymentRecord


def _u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


OPERATION_EXPENSE = _u("\\u0420\\u0430\\u0441\\u0445\\u043e\\u0434")
OPERATION_INCOME = _u("\\u041f\\u0440\\u0438\\u0445\\u043e\\u0434")
OPERATION_CONVERSION = _u("\\u041a\\u043e\\u043d\\u0432\\u0435\\u0440\\u0442\\u0430\\u0446\\u0438\\u044f")
PAYMENT_CASH = _u("\\u041d\\u0430\\u043b\\u0438\\u0447\\u043d\\u0430\\u044f")
PAYMENT_NONCASH_WITH_VAT = _u("\\u0411\\u0435\\u0437\\u043d\\u0430\\u043b\\u0438\\u0447\\u043d\\u044b\\u0435 \\u0441 \\u041d\\u0414\\u0421")
PAYMENT_NONCASH_WITHOUT_VAT = _u("\\u0411\\u0435\\u0437\\u043d\\u0430\\u043b\\u0438\\u0447\\u043d\\u044b\\u0435 \\u0431\\u0435\\u0437 \\u041d\\u0414\\u0421")
BANK_SBER = _u("\\u0431/\\u043d \\u0421\\u0431\\u0435\\u0440\\u0431\\u0430\\u043d\\u043a")
BANK_IP_MOCHALOV = _u("\\u0431/\\u043d \\u0418\\u041f \\u041c\\u043e\\u0447\\u0430\\u043b\\u043e\\u0432")
BANK_INVESTSTROY = _u("\\u0431/\\u043d \\u0418\\u041d\\u0412\\u0415\\u0421\\u0422\\u0421\\u0422\\u0420\\u041e\\u0419")
BANK_TOCHKA = _u("\\u0431/\\u043d \\u0422\\u043e\\u0447\\u043a\\u0430")
BANK_ALFA = _u("\\u0431/\\u043d \\u0410\\u043b\\u044c\\u0444\\u0430")


CONVERSION_KEY = normalize_key(OPERATION_CONVERSION)
VAT_KEY = normalize_key(_u("\\u041d\\u0414\\u0421"))
WITHOUT_VAT_KEY = normalize_key(_u("\\u0431\\u0435\\u0437 \\u041d\\u0414\\u0421"))
VAT_NOT_KEY = normalize_key(_u("\\u041d\\u0414\\u0421 \\u043d\\u0435"))
VAT_5_RE = re.compile(_u(r"\\b\\u041d\\u0414\\u0421\\s*5\\s*%"), re.IGNORECASE)
INVOICE_PATTERNS = [
    re.compile(_u(r"\\u0441\\u0447[\\u0435\\u0451]\\u0442\\s+\\u043d\\u0430\\s+\\u043e\\u043f\\u043b\\u0430\\u0442\\u0443\\s*(?:\\u2116|no|n)?\\s*([A-Za-z\\u0410-\\u042f\\u0430-\\u044f\\u0401\\u04510-9_./-]+)"), re.IGNORECASE),
    re.compile(_u(r"\\u0441\\u0447[\\u0435\\u0451]\\u0442\\s+\\u043a\\s+\\u0437\\u0430\\u043a\\u0430\\u0437[-\\u0430-\\u044f\\s]*\\s*(?:\\u2116|no|n)?\\s*([A-Za-z\\u0410-\\u042f\\u0430-\\u044f\\u0401\\u04510-9_./-]+)"), re.IGNORECASE),
    re.compile(_u(r"\\u0441\\u0447[\\u0435\\u0451]\\u0442\\s*(?:\\u2116|no|n)?\\s*([A-Za-z\\u0410-\\u042f\\u0430-\\u044f\\u0401\\u04510-9_./-]+)"), re.IGNORECASE),
    re.compile(_u(r"\\u043f\\u043e\\s+\\u0441\\u0447\\u0435\\u0442\\u0443\\s*(?:\\u2116|no|n)?\\s*([A-Za-z\\u0410-\\u042f\\u0430-\\u044f\\u0401\\u04510-9_./-]+)"), re.IGNORECASE),
]


def fintablo_transactions_to_payment_records(
    transactions: Iterable[dict[str, Any]],
    *,
    moneybags: list[dict[str, Any]],
    categories: list[dict[str, Any]],
    partners: list[dict[str, Any]],
    deals: list[dict[str, Any]],
    directions: list[dict[str, Any]],
) -> list[PaymentRecord]:
    moneybag_by_id = _by_id(moneybags)
    category_by_id = _by_id(categories)
    partner_by_id = _by_id(partners)
    deal_by_id = _by_id(deals)
    direction_by_id = _by_id(directions)
    return [
        _transaction_to_payment_record(
            tx,
            moneybag_by_id=moneybag_by_id,
            category_by_id=category_by_id,
            partner_by_id=partner_by_id,
            deal_by_id=deal_by_id,
            direction_by_id=direction_by_id,
        )
        for tx in transactions
    ]




def fetch_fintablo_payment_records(client: Any, start: Any, end: Any, *, include_cash: bool = False) -> list[PaymentRecord]:
    moneybags = client.list_moneybags()
    categories = client.list_categories()
    partners = client.list_partners()
    deals = client.list_deals()
    directions = client.list_directions()
    transactions = client.list_transactions(date_from=_fintablo_date(start), date_to=_fintablo_date(end))
    if not include_cash:
        moneybag_by_id = _by_id(moneybags)
        transactions = [
            tx for tx in transactions
            if str(moneybag_by_id.get(_int_id(tx.get("moneybagId")), {}).get("type") or "").strip() != "nal"
        ]
    return fintablo_transactions_to_payment_records(
        transactions,
        moneybags=moneybags,
        categories=categories,
        partners=partners,
        deals=deals,
        directions=directions,
    )


def _fintablo_date(value: Any) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%d.%m.%Y")
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        year, month, day = match.groups()
        return f"{day}.{month}.{year}"
    return text


def _transaction_to_payment_record(
    tx: dict[str, Any],
    *,
    moneybag_by_id: dict[int, dict[str, Any]],
    category_by_id: dict[int, dict[str, Any]],
    partner_by_id: dict[int, dict[str, Any]],
    deal_by_id: dict[int, dict[str, Any]],
    direction_by_id: dict[int, dict[str, Any]],
) -> PaymentRecord:
    group = str(tx.get("group") or "").strip()
    description = str(tx.get("description") or "").strip()
    moneybag = moneybag_by_id.get(_int_id(tx.get("moneybagId")), {})
    category = category_by_id.get(_int_id(tx.get("categoryId")), {})
    partner = partner_by_id.get(_int_id(tx.get("partnerId")), {})
    deal = deal_by_id.get(_int_id(tx.get("dealId")), {})
    direction = direction_by_id.get(_int_id(tx.get("directionId")), {})

    operation_type = _operation_type(group, category, description)
    object_name = str(deal.get("name") or "").strip()
    project = str(direction.get("name") or "").strip()
    if operation_type == OPERATION_CONVERSION:
        object_name = object_name or OPERATION_CONVERSION
        project = project or OPERATION_CONVERSION

    return PaymentRecord(
        name=f"fintablo:{tx.get('id', '')}",
        date=str(tx.get("date") or "").strip(),
        operation_type=operation_type,
        payment_type=_payment_type(moneybag, description),
        bank=_bank_name(moneybag),
        counterparty=str(partner.get("name") or "").strip(),
        invoice_number=_extract_invoice_number(description),
        object_name=object_name,
        project=project,
        budget_item=str(category.get("name") or "").strip(),
        responsible="",
        purpose=description,
        invoice_link="",
        amount=_amount(tx.get("value")),
    )


def _by_id(items: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in items:
        item_id = _int_id(item.get("id"))
        if item_id:
            result[item_id] = item
    return result


def _int_id(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _operation_type(group: str, category: dict[str, Any], description: str) -> str:
    category_text = normalize_key(str(category.get("name") or ""))
    description_text = normalize_key(description)
    if group == "transfer" or CONVERSION_KEY in category_text or CONVERSION_KEY in description_text:
        return OPERATION_CONVERSION
    if group == "income":
        return OPERATION_INCOME
    return OPERATION_EXPENSE


def _payment_type(moneybag: dict[str, Any], description: str) -> str:
    if str(moneybag.get("type") or "").strip() == "nal":
        return PAYMENT_CASH
    text = normalize_key(description)
    if WITHOUT_VAT_KEY in text or VAT_NOT_KEY in text or VAT_5_RE.search(description):
        return PAYMENT_NONCASH_WITHOUT_VAT
    if VAT_KEY in text:
        return PAYMENT_NONCASH_WITH_VAT
    return PAYMENT_NONCASH_WITHOUT_VAT


def _bank_name(moneybag: dict[str, Any]) -> str:
    if str(moneybag.get("type") or "").strip() == "nal":
        return ""
    name = normalize_key(str(moneybag.get("name") or ""))
    if normalize_key(_u("\\u0418\\u041f \\u041c\\u043e\\u0447")) in name and normalize_key(_u("\\u0410\\u043b\\u044c\\u0444\\u0430")) in name:
        return BANK_IP_MOCHALOV
    if normalize_key(_u("\\u0418\\u041d\\u0412\\u0415\\u0421\\u0422\\u0421\\u0422\\u0420\\u041e\\u0419")) in name:
        return BANK_INVESTSTROY
    if normalize_key(_u("\\u0421\\u0431\\u0435\\u0440")) in name:
        return BANK_SBER
    if normalize_key(_u("\\u0422\\u043e\\u0447\\u043a\\u0430")) in name:
        return BANK_TOCHKA
    if normalize_key(_u("\\u0410\\u043b\\u044c\\u0444\\u0430")) in name:
        return BANK_ALFA
    return str(moneybag.get("name") or "").strip()


def _extract_invoice_number(description: str) -> str:
    for pattern in INVOICE_PATTERNS:
        match = pattern.search(description)
        if match:
            value = match.group(1).strip(" .,")
            if _is_bad_invoice_token(value):
                continue
            return value
    return ""


def _is_bad_invoice_token(value: str) -> bool:
    return normalize_key(value) in {
        _u(r"\u043d\u0430"),
        _u(r"\u043a"),
        _u(r"\u043e\u043f\u043b\u0430\u0442\u0443"),
        _u(r"\u0437\u0430\u043a\u0430\u0437"),
    }

def _amount(value: Any) -> str:
    text = str(value or "").replace(" ", "").replace(",", ".")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return str(value or "").strip()
    if number == number.to_integral():
        return str(number.quantize(Decimal("1")))
    return format(number.normalize(), "f")




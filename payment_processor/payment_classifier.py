from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader


PAYMENT_TITLE_RE = re.compile(r"ПЛАТ[ЕЁ]ЖНОЕ\s+ПОРУЧЕНИЕ", re.IGNORECASE)
PAYMENT_MARKERS = (
    "ПЛАТЕЛЬЩИК",
    "ПОЛУЧАТЕЛЬ",
    "БАНК ПЛАТЕЛЬЩИКА",
    "БАНК ПОЛУЧАТЕЛЯ",
    "БИК",
    "СЧ. №",
)
NON_PAYMENT_MARKERS = (
    "СЧЕТ НА ОПЛАТУ",
    "СЧЁТ НА ОПЛАТУ",
    "УНИВЕРСАЛЬНЫЙ ПЕРЕДАТОЧНЫЙ ДОКУМЕНТ",
    "СЧЕТ-ФАКТУРА",
    "СЧЁТ-ФАКТУРА",
    "АКТ ВЫПОЛНЕННЫХ РАБОТ",
)


def is_payment_order_pdf(path: Path) -> bool:
    if path.suffix.lower() != ".pdf":
        return False
    try:
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages[:2])
    except Exception:
        return False
    return is_payment_order_text(text)


def is_payment_order_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text).upper()
    if not PAYMENT_TITLE_RE.search(normalized):
        return False
    marker_count = sum(1 for marker in PAYMENT_MARKERS if marker in normalized)
    return marker_count >= 4

import re
from pathlib import Path

from pypdf import PdfReader

from .models import PaymentRecord


DEFAULT_ACCOUNT_BANKS = {
    "40702810003500033560": "б/н Точка",
    "40702810532250003784": "б/н Альфа",
    "40802810932180010224": "б/н ИП Мочалов",
    "40702810332180011648": "б/н ИНВЕСТСТРОЙ",
    "40702810855000125967": "б/н Сбербанк",
}


def parse_payment_text(text: str, file_name: str, rules: dict | None = None) -> PaymentRecord:
    rules = rules or {}
    lines = _lines(text)
    joined = _joined(lines)
    payer_account = _extract_payer_account(lines, joined)
    payer_name = _extract_payer_name(lines)
    payer_bank_bik = _extract_payer_bank_bik(lines)
    counterparty = _extract_counterparty(lines)
    full_purpose = _extract_purpose(lines)
    invoice_number = _extract_invoice_number(full_purpose)
    purpose = _display_purpose(full_purpose)
    classification = _apply_classification(counterparty, purpose, invoice_number, rules)

    return PaymentRecord(
        name=file_name,
        date=_extract_date(joined),
        operation_type="Расход",
        payment_type=classification.get("payment_type") or _payment_type(full_purpose),
        bank=_bank_name(payer_account, payer_name, payer_bank_bik, joined, rules),
        counterparty=classification.get("counterparty") or counterparty,
        invoice_number=invoice_number,
        object_name=classification.get("object", ""),
        project=classification.get("project", ""),
        budget_item=classification.get("budget_item", ""),
        responsible=classification.get("responsible", ""),
        purpose=classification.get("purpose") or purpose,
        invoice_link="",
        amount=_extract_amount(lines, joined),
    )


def parse_payment_pdf(path: Path, rules: dict | None = None) -> PaymentRecord:
    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return parse_payment_text(text, path.name, rules)


def _lines(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def _joined(lines: list[str]) -> str:
    return "\n".join(lines)


def _extract_date(text: str) -> str:
    match = re.search(r"ПЛАТ[ЕЁ]ЖНОЕ ПОРУЧЕНИЕ\s*№?\s*\d+\s+(\d{2}\.\d{2}\.\d{4})", text, re.I)
    if not match:
        match = re.search(r"\b(\d{2}\.\d{2}\.\d{4})\b", text)
    if not match:
        return ""
    day, month, year = match.group(1).split(".")
    return f"{year}-{month}-{day}"


def _extract_payer_account(lines: list[str], text: str) -> str:
    known = _known_accounts_from_text(text)
    for account in known:
        return account

    for idx, line in enumerate(lines):
        if line.lower() == "плательщик":
            window = lines[max(0, idx - 4) : idx + 5]
            for item in window:
                match = re.search(r"\b(\d{20})\b", item)
                if match:
                    return match.group(1)

    match = re.search(r"Сч\.\s*№\s*(\d{20})", text)
    return match.group(1) if match else ""


def _known_accounts_from_text(text: str) -> list[str]:
    accounts = []
    configured = sorted(DEFAULT_ACCOUNT_BANKS, key=len, reverse=True)
    for account in configured:
        if account in text:
            accounts.append(account)
    return accounts


def _extract_payer_name(lines: list[str]) -> str:
    for idx, line in enumerate(lines):
        if line.lower() != "плательщик":
            continue
        collected = []
        for item in reversed(lines[max(0, idx - 8) : idx]):
            if _is_payer_boundary(item):
                if collected:
                    break
                continue
            collected.append(item)
            if _looks_like_payer_name(item):
                break
        return " ".join(reversed(collected)).strip()
    return ""


def _is_payer_boundary(line: str) -> bool:
    low = line.lower()
    return (
        low in {"сумма", "сч. №", "инн", "кпп"}
        or "сумма" in low
        or "сч. №" in low
        or re.fullmatch(r"\d[\d\s\u00a0]*-\d{2}", line) is not None
        or re.fullmatch(r"\d{20}", line) is not None
        or ("инн" in low and "кпп" in low)
    )


def _looks_like_payer_name(line: str) -> bool:
    upper = line.upper()
    return any(token in upper for token in ["ООО", "ИП", "ИНДИВИДУАЛЬНЫЙ", "ОБЩЕСТВО"])


def _extract_payer_bank_bik(lines: list[str]) -> str:
    for idx, line in enumerate(lines):
        if line.lower() != "банк плательщика":
            continue
        for item in lines[idx + 1 : idx + 7]:
            match = re.search(r"\b(\d{9})\b", item)
            if match:
                return match.group(1)
    return ""


def _bank_name(payer_account: str, payer_name: str, payer_bank_bik: str, text: str, rules: dict) -> str:
    if "МОЧАЛОВ" in payer_name.upper() and payer_bank_bik == "044030653":
        return "б/н ИП Мочалов Сбер"
    if "МОЧАЛОВ" in payer_name.upper() and payer_bank_bik == "044030786":
        return "б/н ИП Мочалов"
    account_banks = DEFAULT_ACCOUNT_BANKS | rules.get("account_banks", {})
    if payer_account in account_banks:
        return account_banks[payer_account]
    if "ООО \"Банк Точка\"" in text:
        return "б/н Точка"
    if "ПАО Сбербанк" in text or "ПАО СБЕРБАНК" in text:
        return "б/н Сбербанк"
    if "АЛЬФА-БАНК" in text:
        return "б/н Альфа"
    return ""


def _extract_amount(lines: list[str], text: str) -> str:
    dash_amounts = re.findall(r"(?<!\d)(\d[\d \u00a0]*-\d{2})(?!\d)", text)
    if not dash_amounts:
        return ""
    raw = dash_amounts[0]
    rubles, kopecks = raw.replace(" ", "").replace("\u00a0", "").split("-")
    if kopecks == "00":
        return rubles
    return f"{rubles},{kopecks}"


def _extract_counterparty(lines: list[str]) -> str:
    for idx, line in enumerate(lines):
        if line.lower() == "получатель":
            before = _counterparty_before_marker(lines, idx)
            if before:
                return before
            return _counterparty_after_marker(lines, idx)
        if line.endswith("Получатель") and len(line) > len("Получатель"):
            after = _counterparty_after_marker(lines, idx)
            if after:
                return after
    return ""


def _counterparty_before_marker(lines: list[str], marker_idx: int) -> str:
    collected = []
    for line in reversed(lines[max(0, marker_idx - 10) : marker_idx]):
        low = line.lower()
        if not line:
            continue
        if _is_counterparty_boundary(line):
            if collected:
                break
            continue
        if not collected and not _looks_like_name(line) and not _looks_like_person_name(line) and not _looks_like_name_continuation(line):
            continue
        collected.append(line)
        if _looks_like_name(line):
            break
    return " ".join(reversed(collected)).strip()


def _counterparty_after_marker(lines: list[str], marker_idx: int) -> str:
    collected = []
    for line in lines[marker_idx + 1 : marker_idx + 5]:
        low = line.lower()
        if _looks_like_purpose_start(line) or low.startswith("назначение платежа"):
            break
        if re.search(r"\b\d{20}\b", line) or re.search(r"\b\d{8,}\b", line):
            break
        if line in {"М.П.", "Подписи"}:
            break
        if collected and not (_looks_like_name(line) or _looks_like_name_continuation(line) or line.startswith("(")):
            break
        collected.append(line)
    return " ".join(collected).strip()


def _looks_like_name(line: str) -> bool:
    upper = line.upper()
    return re.search(r"(?<![А-ЯЁ])(?:ООО|ИП|УФК|АО|ПАО|АНО|РОДИН)(?![А-ЯЁ])|(?<![А-ЯЁ])КОМПАН", upper) is not None


def _looks_like_person_name(line: str) -> bool:
    return re.fullmatch(
        r"[\u0410-\u042f\u0401][\u0430-\u044f\u0451-]+\s+[\u0410-\u042f\u0401][\u0430-\u044f\u0451-]+\s+[\u0410-\u042f\u0401][\u0430-\u044f\u0451-]+",
        line.strip(),
    ) is not None


def _looks_like_name_continuation(line: str) -> bool:
    letters = [char for char in line if char.isalpha()]
    if len(letters) < 6:
        return False
    uppercase_share = sum(char.isupper() for char in letters) / len(letters)
    return uppercase_share > 0.8 or line.endswith('"')


def _is_counterparty_boundary(line: str) -> bool:
    low = line.lower()
    if low in {"код", "5", "01", "вид оп.", "наз. пл.", "очер. плат.", "рез. поле", "срок плат.", "сч. №", "инн"}:
        return True
    return (
        re.fullmatch(r"\d{20}", line) is not None
        or "сч. №" in low
        or low.startswith("бик")
        or "банк получателя" in low
        or "наз. пл." in low
        or "очер." in low
        or "рез. поле" in low
        or "кпп" in low
    )


def _extract_purpose(lines: list[str]) -> str:
    marker_idx = next((idx for idx, line in enumerate(lines) if line.lower() == "назначение платежа"), -1)
    if marker_idx == -1:
        return ""
    start = marker_idx - 1
    while start > 0 and _is_purpose_continuation(lines[start - 1]):
        start -= 1
    return _clean_purpose(" ".join(lines[start:marker_idx]))


def _is_purpose_continuation(line: str) -> bool:
    low = line.lower()
    if re.search(r"\b(счет|сч[её]т|сч[её]ту|договор|оплата|предоплата|ндс|работ|услуг|штраф|вода|аренда|труба|смесь|обучение|проведение|осмотр|медицинск)\b", low):
        return True
    if _looks_like_name(line):
        return False
    if low in {"м.п.", "подписи", "получатель"}:
        return False
    if any(token in low for token in ["рез. поле", "вид оп.", "наз. пл.", "очер.", "срок плат.", "банк получателя"]):
        return False
    if re.fullmatch(r"\d{1,2}", line) or re.fullmatch(r"\d{20}", line):
        return False
    if re.match(r"^\d{2}\.\d{2}\.\d{4}\b", low):
        return True
    if re.search(r"\bот\s+\d{2}\.\d{2}\.\d{4}\b", low):
        return True
    if re.search(r"\b\d[\d\s\u00a0]*[,.]\d{2}\s*руб", low):
        return True
    return False


def _looks_like_purpose_start(line: str) -> bool:
    return _is_purpose_continuation(line)


def _clean_purpose(purpose: str) -> str:
    return re.sub(r"\s+", " ", purpose).strip()


def _display_purpose(purpose: str) -> str:
    purpose = _remove_invoice_tail_from_purpose(purpose)
    purpose = re.sub(r",?\s*в\s*т\.\s*ч\.\s*НДС\b.*$", "", purpose, flags=re.I)
    purpose = re.sub(r"\s*В том числе НДС\b.*$", "", purpose, flags=re.I)
    purpose = re.sub(r"\s*НДС не облагается\b.*$", "", purpose, flags=re.I)
    cut = _first_sentence_dot(purpose)
    if cut is not None:
        purpose = purpose[:cut]
    return purpose.strip(" .")


def _remove_invoice_tail_from_purpose(purpose: str) -> str:
    starts_with_invoice = re.match(r"^\s*Сч[её]т\s*№", purpose, flags=re.I)
    if starts_with_invoice:
        match = re.search(r"\bна\s+(.+)$", purpose, flags=re.I)
        if match:
            return match.group(1)
        return purpose
    return re.sub(r"\.?\s*Сч[её]т\s*№.*$", "", purpose, flags=re.I)


def _first_sentence_dot(text: str) -> int | None:
    for idx, char in enumerate(text):
        if char != ".":
            continue
        prev_char = text[idx - 1] if idx > 0 else ""
        next_char = text[idx + 1] if idx + 1 < len(text) else ""
        if prev_char.isdigit() and next_char.isdigit():
            continue
        if idx > 0 and text[max(0, idx - 2) : idx + 1].lower() in {" г.", "т."}:
            continue
        return idx
    return None


def _extract_invoice_number(purpose: str) -> str:
    if "\u0440\u0430\u0441\u0442\u043e\u0440\u0436" in purpose.lower():
        return "\u0431/\u0441\u0447"
    # Invoice identifiers may contain Cyrillic prefixes, slashes, hyphens,
    # dots and a bank sub-number after a colon (for example ???00168834 or
    # SP-70013: 93). Keep the complete identifier for PP matching.
    token = r"[A-Za-z\u0410-\u042f\u0430-\u044f\u0401\u04510-9][A-Za-z\u0410-\u042f\u0430-\u044f\u0401\u04510-9/_.-]*(?:\s*:\s*[A-Za-z\u0410-\u042f\u0430-\u044f\u0401\u04510-9][A-Za-z\u0410-\u042f\u0430-\u044f\u0401\u04510-9/_.-]*)?"
    invoice_label = r"(?:\u0441\u0447\u0435\u0442\u0443?|\u0441\u0447\u0451\u0442\u0443?)\b"
    patterns = [
        rf"\b\u043f\u043e\s+\u0437\u0430\u043a\u0430\u0437\u0443\s*(?:\u2116\s*)?({token})",
        rf"{invoice_label}(?:\s+\u043d\u0430\s+\u043e\u043f\u043b\u0430\u0442\u0443)?\s*(?:\u2116|#|N|No)?\s*({token})",
        rf"\b\u0434\u043e\u0433\u043e\u0432\u043e\u0440\u0430?\s*(?:\u2116|#|N|No)\s*({token})",
        rf"\b\u043f\u043e\s+\u0441\u0447\u0435\u0442\u0443\s*(?:\u2116\s*)?({token})",
        rf"\b\u043f\u043e\s+\u0441\u0447\.\s*(?:\u2116\s*)?({token})",
    ]
    for pattern in patterns:
        match = re.search(pattern, purpose, re.IGNORECASE)
        if match:
            return match.group(1).strip(" .,;:")
    return "\u0431/\u0441\u0447"


def _payment_type(purpose: str) -> str:
    low = purpose.lower()
    if "ндс не облагается" in low or re.search(r"\bндс\s*5(?:[.,]0+)?\s*%", low):
        return "Безналичные без НДС"
    if "ндс" in low:
        return "Безналичные с НДС"
    return "Безналичные без НДС"


def _apply_classification(counterparty: str, purpose: str, invoice_number: str, rules: dict) -> dict[str, str]:
    haystack = f"{counterparty}\n{purpose}\n{invoice_number}".lower()
    for rule in rules.get("classification_rules", []):
        if not _rule_matches(rule, haystack, counterparty, purpose, invoice_number):
            continue
        return {
            "object": rule.get("object", ""),
            "project": rule.get("project", ""),
            "budget_item": rule.get("budget_item", ""),
            "responsible": rule.get("responsible", ""),
            "payment_type": rule.get("payment_type", ""),
            "purpose": rule.get("purpose", ""),
            "counterparty": rule.get("counterparty", ""),
        }
    return {"object": "", "project": "", "budget_item": "", "responsible": "", "payment_type": "", "purpose": "", "counterparty": ""}


def _rule_matches(rule: dict, haystack: str, counterparty: str, purpose: str, invoice_number: str) -> bool:
    checks = {
        "counterparty_contains": counterparty,
        "purpose_contains": purpose,
        "invoice_number_contains": invoice_number,
    }
    used = False
    for key, source in checks.items():
        expected = rule.get(key)
        if not expected:
            continue
        used = True
        values = expected if isinstance(expected, list) else [expected]
        if not any(_contains_normalized(source, str(value)) for value in values):
            return False
    if not used and rule.get("contains"):
        values = rule["contains"] if isinstance(rule["contains"], list) else [rule["contains"]]
        return any(_contains_normalized(haystack, str(value)) for value in values)
    return used


def _contains_normalized(source: str, needle: str) -> bool:
    return _normalize_match_text(needle) in _normalize_match_text(source)


def _normalize_match_text(value: str) -> str:
    value = value.lower()
    value = value.replace('"', "").replace("«", "").replace("»", "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()

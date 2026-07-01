from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DICTIONARIES_PATH = APP_DIR / "dictionaries.json"
DEFAULT_UNRESOLVED_STATUS = "Нужно разобрать"


@dataclass(frozen=True)
class NormalizationResult:
    value: str
    status: str
    original: str
    matched: bool


GOOGLE_DICTIONARY_SPREADSHEET_ID = "1zPEtx_qNOWypYcvCJCvwckqAc8FP8qVFB7sgFW9F57I"
GOOGLE_DICTIONARY_SHEET_NAME = "\u0421\u043f\u0440\u0430\u0432\u043e\u0447\u043d\u0438\u043a"
GOOGLE_DICTIONARY_RANGE = "A1:AE1000"
GOOGLE_REFERENCE_COLUMN_MAP = {
    "\u0422\u0438\u043f \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0438": "operation_types",
    "\u0422\u0438\u043f \u043e\u043f\u043b\u0430\u0442\u044b": "payment_types",
    "\u0411\u0430\u043d\u043a": "banks",
    "\u041e\u0431\u044a\u0435\u043a\u0442": "objects",
    "\u041f\u0440\u043e\u0435\u043a\u0442": "projects",
    "\u0421\u0442\u0430\u0442\u044c\u044f \u0431\u044e\u0434\u0436\u0435\u0442\u0430": "budget_items",
    "\u041e\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0439": "responsibles",
}
GOOGLE_DICT_CATEGORIES = {"objects", "projects", "budget_items", "responsibles"}
GOOGLE_LIST_CATEGORIES = {"operation_types", "payment_types", "banks"}


def load_dictionaries(path: Path = DEFAULT_DICTIONARIES_PATH, prefer_google: bool = False) -> dict[str, Any]:
    data = _load_local_dictionaries(path)
    if not prefer_google:
        return data
    try:
        return load_google_dictionaries(data)
    except Exception:
        return data


def _load_local_dictionaries(path: Path = DEFAULT_DICTIONARIES_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"unresolved_status": DEFAULT_UNRESOLVED_STATUS}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    data.setdefault("unresolved_status", DEFAULT_UNRESOLVED_STATUS)
    return data


def load_google_dictionaries(base: dict[str, Any] | None = None) -> dict[str, Any]:
    from .env import load_env
    from .google_api import build_sheets_service, extract_google_id, get_credentials, load_google_settings

    env = load_env()
    spreadsheet_id = extract_google_id(env.get("GOOGLE_DICTIONARY_SPREADSHEET_ID", "") or GOOGLE_DICTIONARY_SPREADSHEET_ID)
    sheet_name = env.get("GOOGLE_DICTIONARY_SHEET_NAME", "") or GOOGLE_DICTIONARY_SHEET_NAME
    if not spreadsheet_id:
        return dict(base or {})
    settings = load_google_settings(env)
    sheets = build_sheets_service(get_credentials(settings))
    rows = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!{GOOGLE_DICTIONARY_RANGE}",
        valueRenderOption="FORMATTED_VALUE",
    ).execute().get("values", [])
    return dictionaries_from_google_rows(rows, base or {})


def dictionaries_from_google_rows(rows: list[list[Any]], base: dict[str, Any] | None = None) -> dict[str, Any]:
    result = dict(base or {})
    result.setdefault("unresolved_status", DEFAULT_UNRESOLVED_STATUS)
    if len(rows) < 3:
        return result
    headers = [str(value).strip() for value in rows[1]]
    values_by_category: dict[str, list[str]] = {}
    for column_idx, header in enumerate(headers):
        category = GOOGLE_REFERENCE_COLUMN_MAP.get(header)
        if not category:
            continue
        values = values_by_category.setdefault(category, [])
        seen = {normalize_key(value) for value in values}
        for row in rows[2:]:
            if column_idx >= len(row):
                continue
            text = str(row[column_idx]).strip()
            if not text:
                continue
            key = normalize_key(text)
            if not key or key in seen:
                continue
            seen.add(key)
            values.append(text)

    for category in GOOGLE_DICT_CATEGORIES:
        if category in values_by_category:
            result[category] = _merge_canonical_mapping(result.get(category, {}), values_by_category[category])
    for category in GOOGLE_LIST_CATEGORIES:
        if category in values_by_category:
            result[category] = values_by_category[category]
    return result


def _merge_canonical_mapping(existing: Any, canonical_values: list[str]) -> dict[str, list[str]]:
    existing = existing if isinstance(existing, dict) else {}
    existing_by_key = {normalize_key(str(canonical)): aliases for canonical, aliases in existing.items()}
    result: dict[str, list[str]] = {}
    for canonical in canonical_values:
        aliases = existing.get(canonical, existing_by_key.get(normalize_key(canonical), []))
        if not isinstance(aliases, list):
            aliases = []
        result[canonical] = [str(alias).strip() for alias in aliases if str(alias).strip()]
    return result


def normalize_value(
    category: str,
    value: str,
    dictionaries: dict[str, Any],
    reference_values: list[str] | None = None,
    required: bool = False,
    strict: bool = False,
) -> NormalizationResult:
    original = (value or "").strip()
    unresolved_status = dictionaries.get("unresolved_status", DEFAULT_UNRESOLVED_STATUS)
    if not original:
        return NormalizationResult("", unresolved_status if required else "", original, False)

    normalized_original = normalize_key(original)
    mapping = dictionaries.get(category, {})
    if isinstance(mapping, dict):
        for canonical in mapping:
            if normalized_original == normalize_key(canonical):
                return NormalizationResult(str(canonical), "", original, True)
        for canonical, aliases in mapping.items():
            if normalized_original in {normalize_key(choice) for choice in aliases or []}:
                return NormalizationResult(str(canonical), "", original, True)

    for reference in reference_values or []:
        if normalized_original == normalize_key(reference):
            return NormalizationResult(reference, "", original, True)

    return NormalizationResult(
        "" if strict else original,
        unresolved_status if required or strict else "",
        original,
        False,
    )


def normalize_key(value: str) -> str:
    value = value.lower().replace("ё", "е")
    value = re.sub(r"[\"'«».,;:()№#]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .dictionaries import normalize_key
from .fintablo_client import FinTabloClient


def _u(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


DEFAULT_DEAL_STAGES = [
    _u(0x0410, 0x0420, 0x005f),
    _u(0x041a, 0x0416, 0x005f),
    _u(0x041a, 0x041c, 0x0020, 0x0028, 0x0020, 0x041c, 0x0020, 0x0029),
    _u(0x041a, 0x041c, 0x0020, 0x0028, 0x0020, 0x041f, 0x0420, 0x0020, 0x0029),
    _u(0x0411, 0x043b, 0x0430, 0x0433, 0x043e, 0x0443, 0x0441, 0x0442, 0x0440, 0x043e, 0x0439, 0x0441, 0x0442, 0x0432, 0x043e),
    _u(0x041f, 0x0418, 0x0420),
    _u(0x041c, 0x043e, 0x0431, 0x0438, 0x043b, 0x0438, 0x0437, 0x0430, 0x0446, 0x0438, 0x044f),
]

TECHNICAL_OBJECTS = {
    _u(0x041a, 0x043e, 0x043d, 0x0432, 0x0435, 0x0440, 0x0442, 0x0430, 0x0446, 0x0438, 0x044f),
}


@dataclass(frozen=True)
class ReferenceSyncPlan:
    google_categories: list[str]
    google_deals: list[str]
    fintablo_categories: list[str]
    fintablo_deals: list[str]
    missing_categories: list[str]
    extra_categories: list[str]
    missing_deals: list[str]
    extra_deals: list[str]
    missing_stages: dict[str, list[str]] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {
            "google_categories": len(self.google_categories),
            "google_deals": len(self.google_deals),
            "fintablo_categories": len(self.fintablo_categories),
            "fintablo_deals": len(self.fintablo_deals),
            "missing_categories": len(self.missing_categories),
            "extra_categories": len(self.extra_categories),
            "missing_deals": len(self.missing_deals),
            "extra_deals": len(self.extra_deals),
            "deals_with_missing_stages": len(self.missing_stages),
            "missing_stage_total": sum(len(values) for values in self.missing_stages.values()),
        }


def reference_values_from_dictionaries(dictionaries: dict[str, Any]) -> tuple[list[str], list[str]]:
    categories = _canonical_values(dictionaries.get("budget_items", {}))
    deals = [value for value in _canonical_values(dictionaries.get("objects", {})) if value not in TECHNICAL_OBJECTS]
    return categories, deals


def build_reference_sync_plan(
    dictionaries: dict[str, Any],
    fintablo_categories: list[dict[str, Any]],
    fintablo_deals: list[dict[str, Any]],
    *,
    default_stages: list[str] | None = None,
) -> ReferenceSyncPlan:
    default_stages = default_stages or DEFAULT_DEAL_STAGES
    google_categories, google_deals = reference_values_from_dictionaries(dictionaries)
    fintablo_category_names = _names(fintablo_categories)
    fintablo_deal_names = _names(fintablo_deals)

    missing_categories = _missing(google_categories, fintablo_category_names)
    extra_categories = _missing(fintablo_category_names, google_categories)
    missing_deals = _missing(google_deals, fintablo_deal_names)
    extra_deals = _missing(fintablo_deal_names, google_deals)
    missing_stages = _missing_deal_stages(google_deals, fintablo_deals, default_stages)

    return ReferenceSyncPlan(
        google_categories=google_categories,
        google_deals=google_deals,
        fintablo_categories=fintablo_category_names,
        fintablo_deals=fintablo_deal_names,
        missing_categories=missing_categories,
        extra_categories=extra_categories,
        missing_deals=missing_deals,
        extra_deals=extra_deals,
        missing_stages=missing_stages,
    )


def fetch_reference_sync_plan(client: FinTabloClient, dictionaries: dict[str, Any]) -> ReferenceSyncPlan:
    return build_reference_sync_plan(
        dictionaries,
        client.list_categories(),
        client.list_deals(),
    )


def _canonical_values(mapping: Any) -> list[str]:
    if isinstance(mapping, dict):
        values = [str(key).strip() for key in mapping if str(key).strip()]
    elif isinstance(mapping, list):
        values = [str(value).strip() for value in mapping if str(value).strip()]
    else:
        values = []
    return _dedupe_preserve_order(values)


def _names(items: list[dict[str, Any]]) -> list[str]:
    return _dedupe_preserve_order(str(item.get("name") or "").strip() for item in items if str(item.get("name") or "").strip())


def _dedupe_preserve_order(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = normalize_key(str(value))
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(str(value).strip())
    return result


def _missing(left: list[str], right: list[str]) -> list[str]:
    right_keys = {normalize_key(value) for value in right}
    return [value for value in left if normalize_key(value) not in right_keys]


def _missing_deal_stages(google_deals: list[str], fintablo_deals: list[dict[str, Any]], default_stages: list[str]) -> dict[str, list[str]]:
    deal_by_key = {normalize_key(str(deal.get("name") or "")): deal for deal in fintablo_deals}
    result: dict[str, list[str]] = {}
    for deal_name in google_deals:
        deal = deal_by_key.get(normalize_key(deal_name))
        if not deal:
            result[deal_name] = list(default_stages)
            continue
        stage_names = _names([stage for stage in deal.get("stages") or [] if isinstance(stage, dict)])
        missing = _missing(default_stages, stage_names)
        if missing:
            result[deal_name] = missing
    return result

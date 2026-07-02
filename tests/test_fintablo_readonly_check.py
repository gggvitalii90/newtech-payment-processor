from __future__ import annotations

from datetime import date

from scripts.fintablo_readonly_check import build_summary, fintablo_date


class FakeClient:
    def list_moneybags(self):
        return [{"id": 1, "name": "cash"}]

    def list_categories(self):
        return [{"id": 2, "name": "fuel", "group": "outcome"}]

    def list_partners(self):
        return [{"id": 3, "name": "partner"}]

    def list_directions(self):
        return [{"id": 4, "name": "direction"}]

    def list_deals(self):
        return [{"id": 5, "name": "deal", "directionId": 4}]

    def list_employees(self):
        return [{"id": 6, "name": "employee"}]

    def list_transactions(self, *, date_from, date_to):
        assert date_from == "01.07.2026"
        assert date_to == "02.07.2026"
        return [
            {"id": 10, "group": "income", "value": 100},
            {"id": 11, "group": "outcome", "value": 50},
            {"id": 12, "group": "outcome", "value": 25},
        ]


def test_fintablo_date_formats_for_api() -> None:
    assert fintablo_date(date(2026, 7, 1)) == "01.07.2026"


def test_build_summary_counts_reference_lists_and_groups() -> None:
    summary = build_summary(FakeClient(), date(2026, 7, 1), date(2026, 7, 2))

    assert summary["counts"] == {
        "moneybags": 1,
        "categories": 1,
        "partners": 1,
        "directions": 1,
        "deals": 1,
        "employees": 1,
        "transactions": 3,
    }
    assert summary["transaction_groups"] == {"income": 1, "outcome": 2}
    assert summary["sample_transactions"][0]["id"] == 10

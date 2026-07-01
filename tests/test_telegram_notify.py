from payment_processor.telegram_notify import format_update_notification, google_spreadsheet_link


def _ru(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


def test_google_spreadsheet_link_is_clickable_html() -> None:
    link = google_spreadsheet_link("sheet123")

    assert link == '<a href="https://docs.google.com/spreadsheets/d/sheet123/edit">' + _ru(r"\u041e\u0442\u043a\u0440\u044b\u0442\u044c Google \u0442\u0430\u0431\u043b\u0438\u0446\u0443") + '</a>'


def test_format_update_notification_contains_period_counts_and_clickable_link() -> None:
    report = {
        "status": "ok",
        "start_date": "2026-06-29",
        "end_date": "2026-06-30",
        "dry_run": False,
        "steps": [
            {"command": ["scripts/backfill_max_archive.py", "--mode", "PSK"], "returncode": 0, "stdout": "downloaded= 5\npayment_orders= 2\ninvoice_rows= 3\ngoogle_rows= 3\ncash_rows= 4\npayment_rows= 6\n"},
            {"command": ["scripts/backfill_max_archive.py", "--mode", "IS"], "returncode": 0, "stdout": "downloaded= 1\npayment_orders= 1\ninvoice_rows= 1\ngoogle_rows= 1\ncash_rows= 0\npayment_rows= 1\n"},
            {"command": ["scripts/backfill_payment_history.py", "--mode", "PSK"], "returncode": 0, "stdout": '{"payment_records": 3, "matched_invoices": 2, "missing_payment_links": 1, "cash_operations": 4, "final_records": 7, "issues": {"missing_payment_fields": 1}}'},
            {"command": ["scripts/backfill_payment_history.py", "--mode", "IS"], "returncode": 0, "stdout": '{"payment_records": 1, "matched_invoices": 1, "missing_payment_links": 0, "cash_operations": 0, "final_records": 1, "issues": {}}'},
        ],
        "drive_lifecycle": {"paid_invoices": 2, "moved": 1, "already_archived": 1},
    }

    message = format_update_notification(report, "sheet123")

    assert _ru(r"\u2705 \u041e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u0435 NewTech \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u043e") in message
    assert _ru(r"\U0001f4c5 \u041f\u0435\u0440\u0438\u043e\u0434: 29.06.2026 \u2014 30.06.2026") in message
    assert _ru(r"\U0001f4c4 \u0421\u0447\u0435\u0442\u0430: \u0444\u0430\u0439\u043b\u043e\u0432 6 / \u0441\u0442\u0440\u043e\u043a 4 / Google 4") in message
    assert _ru(r"\U0001f3e6 \u041f\u041f: 4 / \u0441\u043e\u043f\u043e\u0441\u0442\u0430\u0432\u043b\u0435\u043d\u043e 3 / \u0431\u0435\u0437 \u0441\u0447\u0435\u0442\u0430 1") in message
    assert _ru(r"\U0001f4b5 \u041d\u0430\u043b\u0438\u0447\u043a\u0430: 4 \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0439") in message
    assert _ru(r"\U0001f4ca \u0418\u0442\u043e\u0433\u043e\u0432\u0430\u044f: 8 \u0441\u0442\u0440\u043e\u043a") in message
    assert _ru(r"\u26a0\ufe0f \u041d\u0443\u0436\u043d\u043e \u043f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c") in message
    assert "missing_payment_fields: 1" in message
    assert '<a href="https://docs.google.com/spreadsheets/d/sheet123/edit">' + _ru(r"\u041e\u0442\u043a\u0440\u044b\u0442\u044c Google \u0442\u0430\u0431\u043b\u0438\u0446\u0443") + '</a>' in message

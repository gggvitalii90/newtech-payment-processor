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
            {"command": ["scripts/fintablo_sync_daily.py"], "returncode": 0, "stdout": '{"transactions": 10, "final_rows": 8, "noncash_updates": 3, "noncash_updated": 2, "noncash_no_payload": 5, "noncash_no_match": 1, "cash_final_rows": 4, "cash_existing": 2, "cash_missing": 2, "cash_created": 2, "errors": 0, "check_items": [{"id": 123, "date": "29.06.2026", "amount": -3000, "description": "cash row", "reason": "cash_moneybag_not_found"}]}'},
            {"command": ["scripts/fintablo_sync_from_manual_final.py"], "returncode": 0, "stdout": '{"manual_rows": 20, "transactions": 10, "updates": 7, "unmatched_updates": 2, "missing_cash": 0, "check_items": [{"id": 456, "date": "30.06.2026", "amount": -5000, "description": "manual row", "reason": "no_match", "manual": "July 2026:12"}]}'},
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
    assert _ru(r"\U0001f9fe FinTablo: \u0431\u0435\u0437\u043d\u0430\u043b \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d 2/3, \u043d\u0430\u043b\u0438\u0447\u043a\u0430 \u0441\u043e\u0437\u0434\u0430\u043d\u0430 2, \u0443\u0436\u0435 \u0431\u044b\u043b\u043e 2, \u043e\u0448\u0438\u0431\u043e\u043a 0") in message
    assert "FinTablo manual: " + _ru(r"\u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u043e 7, \u043a \u0441\u0432\u0435\u0440\u043a\u0435 2") in message
    assert _ru(r"\u26a0\ufe0f \u041d\u0443\u0436\u043d\u043e \u043f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c") in message
    assert "missing_payment_fields: 1" in message
    assert "FinTablo без квалификации: 5" in message
    assert "FinTablo " + _ru(r"\u0431\u0435\u0437 \u0441\u0442\u0440\u043e\u043a\u0438 \u0432 \u0418\u0442\u043e\u0433\u043e\u0432\u043e\u0439: 1") in message
    assert "FinTablo manual " + _ru(r"\u0431\u0435\u0437 \u0441\u043e\u0432\u043f\u0430\u0434\u0435\u043d\u0438\u044f") not in message
    assert "id=123" in message
    assert "cash_moneybag_not_found" in message
    assert "July 2026:12" in message
    assert '<a href="https://docs.google.com/spreadsheets/d/sheet123/edit">' + _ru(r"\u041e\u0442\u043a\u0440\u044b\u0442\u044c Google \u0442\u0430\u0431\u043b\u0438\u0446\u0443") + '</a>' in message


def test_format_update_notification_uses_single_day_label() -> None:
    report = {
        "status": "ok",
        "start_date": "2026-06-29",
        "end_date": "2026-06-29",
        "dry_run": False,
        "steps": [],
    }

    message = format_update_notification(report, "sheet123")

    assert _ru(r"\U0001f4c5 \u0414\u0430\u0442\u0430: 29.06.2026") in message
    assert _ru(r"\u041f\u0435\u0440\u0438\u043e\u0434: 29.06.2026") not in message


def test_format_update_notification_includes_failed_step_error_excerpt() -> None:
    report = {
        "status": "error",
        "start_date": "2026-07-12",
        "end_date": "2026-07-12",
        "steps": [
            {
                "command": ["scripts/backfill_payment_history.py", "--mode", "IS"],
                "returncode": 1,
                "stdout": "",
                "stderr": "Traceback line\nRuntimeError: " + _ru(r"\u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d MAX_IS_CHAT_ID"),
            }
        ],
    }

    message = format_update_notification(report, "sheet123")

    assert _ru(r"\u274c \u041e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u0435 NewTech \u0437\u0430\u0432\u0435\u0440\u0448\u0438\u043b\u043e\u0441\u044c \u0441 \u043e\u0448\u0438\u0431\u043a\u043e\u0439") in message
    assert _ru(r"\u0448\u0430\u0433 \u0443\u043f\u0430\u043b") + ": scripts/backfill_payment_history.py --mode IS" in message
    assert "RuntimeError: " + _ru(r"\u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d MAX_IS_CHAT_ID") in message


def test_format_update_notification_does_not_hide_missing_payment_report_as_zero() -> None:
    report = {
        "status": "ok",
        "start_date": "2026-07-23",
        "end_date": "2026-07-23",
        "dry_run": False,
        "steps": [
            {
                "command": ["scripts/backfill_max_archive.py", "--mode", "PSK"],
                "returncode": 0,
                "stdout": "downloaded= 2\ninvoice_rows= 2\ngoogle_rows= 2\n",
            },
            {
                "command": ["scripts/backfill_payment_history.py", "--mode", "PSK"],
                "returncode": 1,
                "stdout": "",
                "stderr": "FinTablo unavailable",
            },
        ],
    }

    message = format_update_notification(report, "sheet123")

    assert "ПП: отчёт не получен" in message
    assert "Наличка: отчёт не получен" in message
    assert "Итоговая: отчёт не получен" in message
    assert "ПП: 0 / сопоставлено 0 / без счета 0" not in message

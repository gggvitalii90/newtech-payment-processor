from __future__ import annotations

import html
import json
import re
import urllib.request
from datetime import datetime
from typing import Any


def _u(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


OK = _u(r"\u2705")
ERROR = _u(r"\u274c")
TEST = _u(r"\U0001f9ea")
CAL = _u(r"\U0001f4c5")
BUILDING = _u(r"\U0001f3e2")
DOC = _u(r"\U0001f4c4")
BANK = _u(r"\U0001f3e6")
CASH = _u(r"\U0001f4b5")
CHART = _u(r"\U0001f4ca")
FOLDER = _u(r"\U0001f4c1")
WARN = _u(r"\u26a0\ufe0f")
GREEN = _u(r"\U0001f7e2")
LINK = _u(r"\U0001f517")
CLOCK = _u(r"\U0001f558")
RECEIPT = _u(r"\U0001f9fe")


def google_spreadsheet_link(spreadsheet_id: str) -> str:
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
    label = _u(r"\u041e\u0442\u043a\u0440\u044b\u0442\u044c Google \u0442\u0430\u0431\u043b\u0438\u0446\u0443")
    return f'<a href="{html.escape(url, quote=True)}">{label}</a>'


def format_update_notification(report: dict[str, Any], spreadsheet_id: str) -> str:
    status = str(report.get("status", ""))
    dry_run = bool(report.get("dry_run"))
    failed_steps = [step for step in report.get("steps", []) if int(step.get("returncode", 0) or 0) != 0]
    if failed_steps or status == "error":
        title = f"{ERROR} " + _u(r"\u041e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u0435 NewTech \u0437\u0430\u0432\u0435\u0440\u0448\u0438\u043b\u043e\u0441\u044c \u0441 \u043e\u0448\u0438\u0431\u043a\u043e\u0439")
    elif dry_run or status == "dry_run_ok":
        title = f"{TEST} " + _u(r"\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 NewTech \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u0430")
    else:
        title = f"{OK} " + _u(r"\u041e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u0435 NewTech \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d\u043e")

    max_summary = _sum_key_value_stdout(_steps_for_script(report, "backfill_max_archive.py"))
    payment_summary = _sum_json_stdout(_steps_for_script(report, "backfill_payment_history.py"))
    fintablo_income_summary = _sum_json_stdout(_steps_for_script(report, "fintablo_append_income_to_google.py"))
    fintablo_expense_summary = _sum_json_stdout(_steps_for_script(report, "fintablo_append_expenses_to_google.py"))
    fintablo_summary = _sum_json_stdout(_steps_for_script(report, "fintablo_sync_daily.py"))
    manual_fintablo_summary = _sum_json_stdout(_steps_for_script(report, "fintablo_sync_from_manual_final.py"))
    issues = _sum_issues(payment_summary)
    drive = report.get("drive_lifecycle") or {}

    lines = [
        title,
        "",
        f"{CAL} {_format_period_label(report)}",
        f"{BUILDING} " + _u(r"\u041f\u043e\u0442\u043e\u043a: \u041f\u0421\u041a + \u0418\u0421"),
        "",
        f"{DOC} " + _u(r"\u0421\u0447\u0435\u0442\u0430: \u0444\u0430\u0439\u043b\u043e\u0432") + f" {_invoice_file_count(max_summary)} / " + _u(r"\u0441\u0442\u0440\u043e\u043a") + f" {max_summary.get('invoice_rows', 0)} / Google {max_summary.get('google_rows', 0)}",
        f"{BANK} " + _u(r"\u041f\u041f") + f": {payment_summary.get('payment_records', 0)} / " + _u(r"\u0441\u043e\u043f\u043e\u0441\u0442\u0430\u0432\u043b\u0435\u043d\u043e") + f" {payment_summary.get('matched_invoices', 0)} / " + _u(r"\u0431\u0435\u0437 \u0441\u0447\u0435\u0442\u0430") + f" {payment_summary.get('missing_payment_links', 0)}",
        f"{CASH} " + _u(r"\u041d\u0430\u043b\u0438\u0447\u043a\u0430") + f": {payment_summary.get('cash_operations', max_summary.get('cash_rows', 0))} " + _u(r"\u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0439"),
        f"{CHART} " + _u(r"\u0418\u0442\u043e\u0433\u043e\u0432\u0430\u044f") + f": {payment_summary.get('final_records', 0)} " + _u(r"\u0441\u0442\u0440\u043e\u043a"),
    ]
    if drive:
        lines.append(
            f"{FOLDER} Drive: " + _u(r"\u043e\u043f\u043b\u0430\u0447\u0435\u043d\u043e") + f" {drive.get('paid_invoices', 0)} / "
            + _u(r"\u043f\u0435\u0440\u0435\u043d\u0435\u0441\u0435\u043d\u043e") + f" {drive.get('moved', 0)} / "
            + _u(r"\u0443\u0436\u0435 \u0432 \u0430\u0440\u0445\u0438\u0432\u0435") + f" {drive.get('already_archived', 0)}"
        )
    if fintablo_summary:
        lines.append(
            f"{RECEIPT} FinTablo: "
            + _u(r"\u0431\u0435\u0437\u043d\u0430\u043b \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d") + f" {fintablo_summary.get('noncash_updated', 0)}/{fintablo_summary.get('noncash_updates', 0)}, "
            + _u(r"\u043d\u0430\u043b\u0438\u0447\u043a\u0430 \u0441\u043e\u0437\u0434\u0430\u043d\u0430") + f" {fintablo_summary.get('cash_created', 0)}, "
            + _u(r"\u0443\u0436\u0435 \u0431\u044b\u043b\u043e") + f" {fintablo_summary.get('cash_existing', 0)}, "
            + _u(r"\u043e\u0448\u0438\u0431\u043e\u043a") + f" {fintablo_summary.get('cash_errors', 0) + fintablo_summary.get('noncash_errors', 0)}"
        )
    if fintablo_income_summary or fintablo_expense_summary:
        lines.append(
            f"{RECEIPT} FinTablo -> Google: "
            + _u(r"\u043f\u0440\u0438\u0445\u043e\u0434\u044b") + f" {fintablo_income_summary.get('google_income_appended', 0)}/{fintablo_income_summary.get('google_income_missing', 0)}, "
            + _u(r"\u0440\u0430\u0441\u0445\u043e\u0434\u044b") + f" {fintablo_expense_summary.get('google_expense_appended', 0)}/{fintablo_expense_summary.get('google_expense_missing', 0)}"
        )
    if manual_fintablo_summary:
        lines.append(
            f"{RECEIPT} FinTablo manual: "
            + _u(r"\u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u043e") + f" {manual_fintablo_summary.get('updated', manual_fintablo_summary.get('updates', 0))}, "
            + _u(r"\u043a \u0441\u0432\u0435\u0440\u043a\u0435") + f" {manual_fintablo_summary.get('unmatched_updates', 0)}"
        )

    lines.append("")
    problems: list[str] = []
    for step in failed_steps:
        command = " ".join(str(part) for part in step.get("command", []))
        problem = _u(r"\u0448\u0430\u0433 \u0443\u043f\u0430\u043b: ") + (command or "unknown")
        excerpt = _step_error_excerpt(step)
        if excerpt:
            problem += " | " + excerpt
        problems.append(problem)
    for key, value in sorted(issues.items()):
        if value:
            problems.append(f"{key}: {value}")
    if max_summary.get("days_error", 0):
        problems.append(_u(r"\u0434\u043d\u0435\u0439 \u0441 \u043e\u0448\u0438\u0431\u043a\u043e\u0439 MAX: ") + str(max_summary["days_error"]))
    if fintablo_summary.get("errors", 0):
        problems.append(f"FinTablo errors: {fintablo_summary['errors']}")
    if fintablo_summary.get("noncash_no_payload", 0):
        problems.append(f"FinTablo без квалификации: {fintablo_summary['noncash_no_payload']}")
    if fintablo_summary.get("noncash_no_match", 0):
        problems.append(f"FinTablo без строки в Итоговой: {fintablo_summary['noncash_no_match']}")
    if manual_fintablo_summary.get("update_errors", 0):
        problems.append(f"FinTablo manual errors: {manual_fintablo_summary['update_errors']}")
    detail_items = []
    detail_items.extend(_format_check_item(item) for item in fintablo_summary.get("check_items", [])[:5])
    detail_items.extend(_format_check_item(item) for item in manual_fintablo_summary.get("check_items", [])[:5])
    detail_items = [item for item in detail_items if item]
    if problems or detail_items:
        lines.append(f"{WARN} " + _u(r"\u041d\u0443\u0436\u043d\u043e \u043f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c"))
        lines.extend(_u(r"\u2022 ") + item for item in problems[:8])
        lines.extend(_u(r"\u2022 ") + item for item in detail_items[:8])
    else:
        lines.append(f"{GREEN} " + _u(r"\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430: \u043f\u0440\u043e\u0431\u043b\u0435\u043c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e"))

    if spreadsheet_id:
        lines.extend(["", f"{LINK} " + google_spreadsheet_link(spreadsheet_id)])
    lines.extend(["", f"{CLOCK} " + _u(r"\u0412\u0440\u0435\u043c\u044f \u0437\u0430\u043f\u0443\u0441\u043a\u0430") + f": {datetime.now().strftime('%d.%m.%Y %H:%M')}"])
    return "\n".join(lines)


def _format_check_item(item: dict[str, Any]) -> str:
    parts = []
    item_id = item.get("id") or item.get("transaction_id")
    if item_id:
        parts.append(f"id={item_id}")
    if item.get("manual"):
        parts.append(f"manual={item.get('manual')}")
    if item.get("date"):
        parts.append(str(item.get("date")))
    amount = item.get("amount") or item.get("value")
    if amount not in (None, ""):
        parts.append(str(amount))
    reason = item.get("reason") or item.get("error") or item.get("action")
    if reason:
        parts.append(str(reason))
    description = str(item.get("description") or item.get("purpose") or "").strip()
    if description:
        parts.append(description[:80])
    return " | ".join(parts)


def send_telegram_message(env: dict[str, str], text: str) -> bool:
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        return False
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    return bool(result.get("ok"))


def _invoice_file_count(max_summary: dict[str, int]) -> int:
    return max_summary.get("archive_files") or max_summary.get("downloaded", 0)


def _format_period(report: dict[str, Any]) -> str:
    start = str(report.get("start_date") or report.get("date") or "")
    end = str(report.get("end_date") or start)
    if start == end:
        return _format_date(start)
    return f"{_format_date(start)} \u2014 {_format_date(end)}"


def _format_period_label(report: dict[str, Any]) -> str:
    start = str(report.get("start_date") or report.get("date") or "")
    end = str(report.get("end_date") or start)
    if start != end:
        return _u(r"\u041f\u0435\u0440\u0438\u043e\u0434") + f": {_format_period(report)}"
    label = _u(r"\u0414\u0430\u0442\u0430")
    try:
        report_date = datetime.fromisoformat(start).date()
        today = datetime.now().date()
        delta_days = (today - report_date).days
        if delta_days == 0:
            label = _u(r"\u0421\u0435\u0433\u043e\u0434\u043d\u044f")
        elif delta_days == 1:
            label = _u(r"\u0412\u0447\u0435\u0440\u0430")
    except ValueError:
        pass
    return f"{label}: {_format_date(start)}"


def _format_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%d.%m.%Y")
    except ValueError:
        return value


def _steps_for_script(report: dict[str, Any], script_name: str) -> list[dict[str, Any]]:
    return [step for step in report.get("steps", []) if any(str(part).endswith(script_name) for part in step.get("command", []))]


def _step_error_excerpt(step: dict[str, Any], limit: int = 180) -> str:
    text = str(step.get("stderr") or step.get("stdout") or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    excerpt = lines[-1]
    if len(excerpt) > limit:
        excerpt = excerpt[: limit - 3].rstrip() + "..."
    return excerpt


def _sum_key_value_stdout(steps: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for step in steps:
        for line in str(step.get("stdout", "")).splitlines():
            match = re.match(r"^([a-zA-Z_]+)=\s*(\d+)\s*$", line.strip())
            if match:
                totals[match.group(1)] = totals.get(match.group(1), 0) + int(match.group(2))
    return totals


def _sum_json_stdout(steps: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Any] = {}
    for step in steps:
        payload = _last_json_object(str(step.get("stdout", "")))
        if not payload:
            continue
        for key, value in payload.items():
            if isinstance(value, int):
                totals[key] = int(totals.get(key, 0)) + value
            elif key == "issues" and isinstance(value, dict):
                current = totals.setdefault("issues", {})
                for issue_key, issue_value in value.items():
                    current[issue_key] = int(current.get(issue_key, 0)) + int(issue_value)
            elif key.endswith("_items") and isinstance(value, list):
                current = totals.setdefault(key, [])
                current.extend(item for item in value if isinstance(item, dict))
    return totals


def _last_json_object(value: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    fallback: dict[str, Any] = {}
    for match in re.finditer(r"\{", value):
        try:
            parsed, end = decoder.raw_decode(value[match.start():])
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        fallback = parsed
        if not value[match.start() + end :].strip():
            return parsed
    return fallback


def _sum_issues(summary: dict[str, Any]) -> dict[str, int]:
    issues = summary.get("issues", {})
    if not isinstance(issues, dict):
        return {}
    return {str(key): int(value) for key, value in issues.items()}

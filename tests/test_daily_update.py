from datetime import date
from pathlib import Path

from scripts.backfill_max_archive import normalize_mode
from scripts.run_daily_update import build_daily_commands, build_period_commands, find_confirmed_final_payment


def test_backfill_normalizes_ascii_modes():
    assert normalize_mode("PSK") == "\u041f\u0421\u041a"
    assert normalize_mode("IS") == "\u0418\u0421"


def test_daily_commands_update_both_invoice_archives_before_payment_sheets():
    commands = build_daily_commands(date(2026, 6, 24), Path("stage"), dry_run=False)
    scripts = [command[1] for command in commands]
    assert scripts == [
        "scripts/backfill_max_archive.py",
        "scripts/backfill_max_archive.py",
        "scripts/backfill_payment_history.py",
        "scripts/backfill_payment_history.py",
        "scripts/fintablo_append_income_to_google.py",
        "scripts/fintablo_sync_daily.py",
        "scripts/fintablo_sync_from_manual_final.py",
    ]
    assert commands[0][-1] == "\u041f\u0421\u041a"
    assert commands[1][-1] == "\u0418\u0421"
    assert "--upsert" in commands[2]
    assert "--upsert" in commands[3]


def test_period_commands_use_requested_start_and_end():
    commands = build_period_commands(date(2026, 6, 29), date(2026, 6, 30), Path("stage"), dry_run=False)

    for command in commands[:-1]:
        assert command[command.index("--start") + 1] == "2026-06-29"
        assert command[command.index("--end") + 1] == "2026-06-30"
    assert commands[-1][commands[-1].index("--start") + 1] == "29.06.2026"
    assert commands[-1][commands[-1].index("--end") + 1] == "30.06.2026"


def test_daily_dry_run_uses_local_invoice_build_and_does_not_write_sheets():
    commands = build_daily_commands(date(2026, 6, 24), Path("stage"), dry_run=True)
    assert "--local-only" in commands[0]
    assert "--local-only" in commands[1]
    assert "--dry-run" in commands[2]
    assert "--dry-run" in commands[3]
    assert "--apply" not in commands[4]
    assert "--apply" not in commands[5]
    assert "--apply" not in commands[6]


def test_daily_live_run_applies_fintablo_sync_after_google_update():
    commands = build_daily_commands(date(2026, 7, 10), Path("stage"), dry_run=False)

    income_command = commands[-3]
    command = commands[-2]
    manual_command = commands[-1]

    assert income_command[1] == "scripts/fintablo_append_income_to_google.py"
    assert income_command[income_command.index("--start") + 1] == "2026-07-10"
    assert income_command[income_command.index("--end") + 1] == "2026-07-10"
    assert "--apply" in income_command
    assert command[1] == "scripts/fintablo_sync_daily.py"
    assert command[command.index("--start") + 1] == "2026-07-10"
    assert command[command.index("--end") + 1] == "2026-07-10"
    assert "--apply" in command
    assert manual_command[1] == "scripts/fintablo_sync_from_manual_final.py"
    assert manual_command[manual_command.index("--start") + 1] == "10.07.2026"
    assert manual_command[manual_command.index("--end") + 1] == "10.07.2026"
    assert "--apply" in manual_command


def test_finalization_requires_same_invoice_link_and_payment_date():
    from payment_processor.invoice_archive import InvoiceArchiveRecord
    from payment_processor.models import PaymentRecord
    invoice = InvoiceArchiveRecord(
        "2026-06-24", "???", "chat", "?????", "invoice.pdf", "pdf", "??????",
        "??????????? ??? ???", "", '??? "???????"', "15", "2026-06-01",
        "??? ??????", "????", "??????????", "?????.?", "??????", "1000", "???????",
        "https://drive.google.com/file/d/invoice-file/view", "mid", "fid", "??",
    )
    wrong = PaymentRecord("wrong.pdf", "2026-06-24", "??????", "", "", '??? "???????"', "15", "", "", "", "", "", "https://drive.google.com/file/d/other/view", "1000")
    right = PaymentRecord("right.pdf", "2026-06-24", "??????", "", "", '??? "???????"', "15", "", "", "", "", "", "https://drive.google.com/file/d/invoice-file/view", "1000")
    assert find_confirmed_final_payment(invoice, [wrong, right], date(2026, 6, 24)) is right


def test_daily_commands_default_to_fintablo_payment_source():
    commands = build_daily_commands(date(2026, 7, 14), Path("stage"), dry_run=False)
    payment_commands = commands[2:4]
    for command in payment_commands:
        assert command[command.index("--payment-source") + 1] == "fintablo"


def test_daily_commands_can_use_fintablo_payment_source():
    commands = build_daily_commands(date(2026, 7, 2), Path("stage"), dry_run=False, payment_source="fintablo")
    payment_commands = commands[2:4]
    for command in payment_commands:
        assert command[command.index("--payment-source") + 1] == "fintablo"


def test_main_runs_period_as_separate_daily_reports(monkeypatch):
    import scripts.run_daily_update as daily

    class Args:
        date = ""
        start = "2026-07-06"
        end = "2026-07-07"
        staging_root = "stage"
        dry_run = True
        no_telegram = True
        payment_source = "fintablo"

    calls = []
    monkeypatch.setattr(daily, "parse_args", lambda: Args())
    monkeypatch.setattr(daily, "_run_one_day", lambda day, args: calls.append(day.isoformat()) or 0)

    assert daily.main() == 0
    assert calls == ["2026-07-06", "2026-07-07"]


def test_daily_update_keeps_google_result_when_fintablo_is_unavailable(monkeypatch):
    import scripts.run_daily_update as daily

    class Args:
        staging_root = "stage"
        dry_run = False
        no_telegram = True
        payment_source = "fintablo"

    class Completed:
        def __init__(self, command, returncode=0, stdout="{}", stderr=""):
            self.args = command
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    commands = [
        ["python", "scripts/backfill_max_archive.py"],
        ["python", "scripts/backfill_payment_history.py"],
        ["python", "scripts/fintablo_append_income_to_google.py"],
        ["python", "scripts/fintablo_sync_daily.py"],
    ]
    monkeypatch.setattr(daily, "build_daily_commands", lambda *_args: commands)

    def run(command, **_kwargs):
        if command[1] == "scripts/fintablo_sync_daily.py":
            return Completed(command, 1, "", "FinTablo subscription expired")
        return Completed(command)

    reports = []
    monkeypatch.setattr(daily.subprocess, "run", run)
    monkeypatch.setattr(daily, "finalize_drive", lambda day: {"paid_invoices": 0})
    monkeypatch.setattr(daily, "_write_report", lambda _start, _end, report: reports.append(report))

    assert daily._run_one_day(date(2026, 7, 13), Args()) == 0
    assert reports[0]["status"] == "ok"
    assert reports[0]["steps"][-1]["returncode"] == 1

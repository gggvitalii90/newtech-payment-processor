from payment_processor.history_runner import write_google_history


def test_write_google_history_dry_run_makes_no_google_calls() -> None:
    class Forbidden:
        def __getattr__(self, name):
            raise AssertionError(name)
    assert write_google_history(Forbidden(), "sheet", ["payment"], ["final"], dry_run=True) == (0, 0)

def test_write_google_history_uses_requested_final_sheet() -> None:
    calls = []

    class FakeSheets:
        pass

    import payment_processor.history_runner as runner
    original_setup = runner.setup_payment_sheets
    original_archive = runner.replace_payment_archive_rows
    original_final = runner.replace_final_rows
    try:
        runner.setup_payment_sheets = lambda service, spreadsheet_id: calls.append(("setup", spreadsheet_id))
        runner.replace_payment_archive_rows = lambda service, spreadsheet_id, rows: 0
        runner.replace_final_rows = lambda service, spreadsheet_id, rows, sheet_name="????????": calls.append(("final", sheet_name, list(rows))) or 1
        assert runner.write_google_history(FakeSheets(), "sheet", [], ["row"], final_sheet_name="???????? ??") == (0, 1)
    finally:
        runner.setup_payment_sheets = original_setup
        runner.replace_payment_archive_rows = original_archive
        runner.replace_final_rows = original_final

    assert calls[-1] == ("final", "???????? ??", ["row"])



def test_write_google_history_upsert_mode_preserves_existing_rows():
    import payment_processor.history_runner as runner
    calls = []
    originals = (runner.setup_payment_sheets, runner.upsert_payment_archive, runner.upsert_final_rows)
    try:
        runner.setup_payment_sheets = lambda *_args: calls.append("setup")
        runner.upsert_payment_archive = lambda *_args: (2, 1)
        runner.upsert_final_rows = lambda *_args, **_kwargs: (3, 1)
        result = runner.write_google_history(
            object(), "sheet", ["payment"], ["final"], upsert=True,
        )
    finally:
        runner.setup_payment_sheets, runner.upsert_payment_archive, runner.upsert_final_rows = originals
    assert result == (3, 4)
    assert calls == ["setup"]


def test_backfill_payment_history_passes_cli_upsert_to_google_writer() -> None:
    from pathlib import Path

    source = Path("scripts/backfill_payment_history.py").read_text(encoding="utf-8")

    assert "upsert=args.upsert" in source

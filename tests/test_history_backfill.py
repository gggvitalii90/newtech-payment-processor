from datetime import date
from pathlib import Path

from payment_processor.history_backfill import BackfillState, run_resumable_days


def test_backfill_state_saves_and_loads_completed_days(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = BackfillState.load(path)
    state.mark_day(date(2026, 4, 1), {"downloaded": 3})
    loaded = BackfillState.load(path)
    assert loaded.is_completed(date(2026, 4, 1))
    assert loaded.days["2026-04-01"]["downloaded"] == 3


def test_run_resumable_days_skips_completed_and_checkpoints_each_success(tmp_path: Path) -> None:
    state = BackfillState.load(tmp_path / "state.json")
    state.mark_day(date(2026, 4, 1), {"downloaded": 1})
    called = []
    run_resumable_days(date(2026, 4, 1), date(2026, 4, 3), state, lambda day: called.append(day) or {"downloaded": 2})
    assert called == [date(2026, 4, 2), date(2026, 4, 3)]
    assert BackfillState.load(state.path).is_completed(date(2026, 4, 3))
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable


@dataclass
class BackfillState:
    path: Path
    days: dict[str, dict[str, Any]]

    @classmethod
    def load(cls, path: Path) -> "BackfillState":
        if not path.exists():
            return cls(path, {})
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(path, dict(payload.get("days", {})))

    def is_completed(self, day: date) -> bool:
        return bool(self.days.get(day.isoformat(), {}).get("completed"))

    def mark_day(self, day: date, details: dict[str, Any]) -> None:
        self.days[day.isoformat()] = {**details, "completed": True}
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"version": 1, "days": self.days}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)


def run_resumable_days(
    start_date: date,
    end_date: date,
    state: BackfillState,
    handler: Callable[[date], dict[str, Any]],
) -> None:
    current = start_date
    while current <= end_date:
        if not state.is_completed(current):
            state.mark_day(current, handler(current))
        current += timedelta(days=1)
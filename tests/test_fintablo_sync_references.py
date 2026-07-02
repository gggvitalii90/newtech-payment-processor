from __future__ import annotations

import sys

from scripts import fintablo_sync_references


def test_sync_references_apply_is_blocked(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["fintablo_sync_references.py", "--apply"])

    assert fintablo_sync_references.main() == 2

    captured = capsys.readouterr()
    assert "not implemented" in captured.err

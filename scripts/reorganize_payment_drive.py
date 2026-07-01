from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payment_processor.env import load_env
from payment_processor.google_api import build_drive_service, get_credentials, load_google_settings
from payment_processor.payment_drive import add_file_counts, reorganize_day_folders, write_move_report

DEFAULT_ROOT_ID = "1jB4mkAxrfykCC_N5BO4P-jx0QSEsiQhX"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Перемещение существующих папок ПП в структуру год/месяц/дата")
    parser.add_argument("--root-id", default=DEFAULT_ROOT_ID)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_google_settings(load_env())
    drive = build_drive_service(get_credentials(settings))
    planned = reorganize_day_folders(drive, args.root_id, apply=False)
    before = add_file_counts(drive, planned)
    before_path = ROOT / "reports" / "payment_drive_reorganization_before.csv"
    write_move_report(before_path, before)
    print(f"day_folders={len(before)}")
    print(f"files={sum(int(row['file_count']) for row in before)}")
    print(f"before_report={before_path}")
    if not args.apply:
        print("DRY RUN: папки не изменялись")
        return 0

    moved = reorganize_day_folders(drive, args.root_id, apply=True)
    after = add_file_counts(drive, moved)
    after_path = ROOT / "reports" / "payment_drive_reorganization_after.csv"
    write_move_report(after_path, after)
    remaining = reorganize_day_folders(drive, args.root_id, apply=False)
    before_by_id = {row["id"]: row for row in before}
    after_by_id = {row["id"]: row for row in after}
    if set(before_by_id) != set(after_by_id):
        raise RuntimeError("Набор ID дневных папок изменился")
    for folder_id, row in before_by_id.items():
        if row["file_count"] != after_by_id[folder_id]["file_count"]:
            raise RuntimeError(f"Изменилось количество файлов в папке {folder_id}")
    if remaining:
        raise RuntimeError(f"В корне осталось дневных папок: {len(remaining)}")
    print(f"moved={len(moved)}")
    print(f"remaining_in_root={len(remaining)}")
    print(f"after_report={after_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
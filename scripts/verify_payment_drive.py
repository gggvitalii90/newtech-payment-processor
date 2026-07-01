from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payment_processor.env import load_env
from payment_processor.google_api import build_drive_service, get_credentials, list_child_files, list_child_folders, load_google_settings
from payment_processor.payment_drive import parse_day_folder_name

ROOT_ID = "1jB4mkAxrfykCC_N5BO4P-jx0QSEsiQhX"
settings = load_google_settings(load_env())
drive = build_drive_service(get_credentials(settings))
root_folders = list_child_folders(drive, ROOT_ID)
day_folders = []
for year in root_folders:
    if not str(year.get("name", "")).isdigit():
        continue
    for month in list_child_folders(drive, str(year["id"])):
        for day in list_child_folders(drive, str(month["id"])):
            if parse_day_folder_name(str(day.get("name", ""))):
                day_folders.append(day)
file_count = sum(len(list_child_files(drive, str(day["id"]))) for day in day_folders)
latest = next((day for day in day_folders if day.get("name") == "2026.06.22"), None)
print({
    "root_folders": sorted(folder.get("name", "") for folder in root_folders),
    "day_folders": len(day_folders),
    "files": file_count,
    "latest_folder": latest,
    "latest_files": len(list_child_files(drive, str(latest["id"]))) if latest else 0,
})
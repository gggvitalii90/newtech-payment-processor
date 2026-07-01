from __future__ import annotations

import os

from payment_processor.env import load_env
from payment_processor.google_api import (
    build_drive_service,
    build_sheets_service,
    get_credentials,
    load_google_settings,
    read_drive_folder_name,
    read_spreadsheet_title,
)


def main() -> None:
    env = dict(os.environ)
    env.update(load_env())
    settings = load_google_settings(env)
    credentials = get_credentials(settings)
    print("OAuth: OK")

    if settings.archive_spreadsheet_id:
        sheets = build_sheets_service(credentials)
        title = read_spreadsheet_title(sheets, settings.archive_spreadsheet_id)
        print(f"Spreadsheet: {title or settings.archive_spreadsheet_id}")
    else:
        print("Spreadsheet: not configured")

    if settings.archive_root_folder_id:
        drive = build_drive_service(credentials)
        name = read_drive_folder_name(drive, settings.archive_root_folder_id)
        print(f"Archive folder: {name or settings.archive_root_folder_id}")
    else:
        print("Archive folder: not configured")


if __name__ == "__main__":
    main()

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payment_processor.env import load_env
from payment_processor.google_api import build_sheets_service, get_credentials, load_google_settings
from payment_processor.google_payments import FINAL_IS_SHEET_NAME, FINAL_SHEET_NAME, PAYMENT_ARCHIVE_SHEET_NAME, setup_payment_sheets


def main() -> None:
    settings = load_google_settings(load_env())
    sheets = build_sheets_service(get_credentials(settings))
    setup_payment_sheets(sheets, settings.archive_spreadsheet_id)
    metadata = sheets.spreadsheets().get(spreadsheetId=settings.archive_spreadsheet_id).execute()
    titles = [sheet["properties"]["title"] for sheet in metadata.get("sheets", [])]
    ranges = [
        f"'{FINAL_SHEET_NAME}'!A1:N1",
        f"'{FINAL_IS_SHEET_NAME}'!A1:N1",
        f"'{PAYMENT_ARCHIVE_SHEET_NAME}'!A1:J1",
        f"'{FINAL_SHEET_NAME}'!A2:N",
        f"'{FINAL_IS_SHEET_NAME}'!A2:N",
        f"'{PAYMENT_ARCHIVE_SHEET_NAME}'!A2:J",
    ]
    values = sheets.spreadsheets().values().batchGet(
        spreadsheetId=settings.archive_spreadsheet_id,
        ranges=ranges,
    ).execute()
    print(json.dumps({
        "titles": titles,
        "headers": [item.get("values", []) for item in values.get("valueRanges", [])[:3]],
        "row_counts": [len(item.get("values", [])) for item in values.get("valueRanges", [])[3:]],
        "url": f"https://docs.google.com/spreadsheets/d/{settings.archive_spreadsheet_id}/edit",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
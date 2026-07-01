from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payment_processor.env import load_env
from payment_processor.google_api import build_sheets_service, get_credentials, load_google_settings
from payment_processor.google_payments import FINAL_IS_SHEET_NAME, FINAL_SHEET_NAME, PAYMENT_ARCHIVE_SHEET_NAME

def parsed_date(value) -> str:
    text = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""

settings = load_google_settings(load_env())
service = build_sheets_service(get_credentials(settings))
sheets = (
    (settings.archive_sheet_name, "A:W", 0, 17),
    (PAYMENT_ARCHIVE_SHEET_NAME, "A:J", 1, 8),
    (FINAL_SHEET_NAME, "A:N", 1, 12),
    (FINAL_IS_SHEET_NAME, "A:N", 1, 12),
)
for title, columns, date_index, link_index in sheets:
    values = service.spreadsheets().values().get(
        spreadsheetId=settings.archive_spreadsheet_id,
        range=f"'{title}'!{columns}",
    ).execute().get("values", [])
    dates = [parsed_date(row[date_index]) for row in values[1:] if len(row) > date_index and row[date_index]]
    dates = [value for value in dates if value]
    link_count = sum(1 for row in values[1:] if len(row) > link_index and row[link_index])
    print({
        "sheet": title,
        "headers": values[0] if values else [],
        "rows": max(0, len(values) - 1),
        "date_min": min(dates) if dates else "",
        "date_max": max(dates) if dates else "",
        "links": link_count,
    })
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payment_processor.app import sync_payment_sheets
from payment_processor.env import load_env
from payment_processor.workflow import read_records_from_workbook


def main() -> None:
    workbook = Path(r"C:\Users\Vitaliy\OneDrive\work\new_tech\_ПП\result.xlsx")
    records = []
    for sheet_name in ("ПСК", "ИС"):
        records.extend(read_records_from_workbook(workbook, sheet_name))
    payment_orders = [
        record for record in records
        if (record.name or "").lower().endswith(".pdf") and record.payment_type != "Наличные"
    ]
    dates = [record.date for record in records if record.date]
    latest_date = max(dates) if dates else ""
    final_records = [record for record in records if record.date == latest_date]
    final_count, archive_count = sync_payment_sheets(final_records, payment_orders, load_env())
    print(json.dumps({
        "latest_date": latest_date,
        "final_rows": final_count,
        "payment_archive_changes": archive_count,
        "payment_orders_found": len(payment_orders),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
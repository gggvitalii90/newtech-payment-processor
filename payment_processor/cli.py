from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config, load_rules
from .dictionaries import load_dictionaries
from .payment_history import reference_lists_from_dictionaries
from .workflow import process_folder, write_records_to_workbook


def main() -> None:
    parser = argparse.ArgumentParser(description="Обработка PDF платежных поручений")
    parser.add_argument("folder", type=Path, help="Папка с PDF")
    parser.add_argument("--mode", choices=["ПСК", "ИС"], default="ПСК", help="Режим обработки")
    parser.add_argument("--output", type=Path, default=None, help="Путь к Excel результату")
    args = parser.parse_args()

    config = load_config()
    rules = load_rules()
    dictionaries = load_dictionaries(prefer_google=True)
    references = reference_lists_from_dictionaries(dictionaries)
    output = args.output or Path(config["output_file"])
    sheet_name = config["modes"][args.mode]["sheet_name"]

    records = process_folder(args.folder, rules)
    write_records_to_workbook(output, sheet_name, records, references)
    print(f"Обработано PDF: {len(records)}")
    print(f"Файл результата: {output}")
    print(f"Лист: {sheet_name}")


if __name__ == "__main__":
    main()

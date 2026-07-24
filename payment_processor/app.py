from __future__ import annotations

import os
import logging
import webbrowser
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import load_config, load_rules
from .cash_archive import cash_records_to_payment_records, create_cash_archive_records, write_cash_archive_xlsx
from .date_picker import DatePicker
from .dictionaries import load_dictionaries
from .env import load_env
from .google_api import build_drive_service, build_sheets_service, get_credentials, load_google_settings, verify_drive_account
from .google_archive import append_archive_records, prepare_records_for_google_drive, read_archive_records, setup_archive_sheet
from .google_payments import final_sheet_name_for_mode, setup_payment_sheets, upsert_final_rows, upsert_payment_archive
from .invoice_archive import create_invoice_archive_records, enrich_invoice_records_from_files, enrich_payment_records_from_archive, invoice_text_operation_records_to_payment_records, mark_paid_records, write_invoice_archive_xlsx
from .logging_setup import configure_logging
from .max_api import (
    MaxApiError,
    MaxApiClient,
    build_client_from_env,
    cash_chat_id_from_env,
    download_chat_files_for_date,
    get_messages_for_date,
    sort_downloaded_files,
)
from .models import PaymentRecord
from .parser import parse_payment_pdf
from .payment_classifier import is_payment_order_pdf
from .payment_drive import ensure_payment_file
from .payment_history import apply_mode_defaults, dedupe_payment_records_by_identity, reference_lists_from_dictionaries
from .references import load_reference_lists
from .table_editor import TableEditor
from .workflow import process_folder, read_records_from_workbook, write_records_to_workbook


class PaymentProcessorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Обработка платежных поручений")
        self.geometry("760x310")
        self.minsize(680, 290)
        self.log_path = configure_logging()
        self.logger = logging.getLogger(__name__)
        self.config_data = load_config()
        self.rules = load_rules()
        self.dictionaries = load_dictionaries(prefer_google=True)
        self.references = reference_lists_from_dictionaries(self.dictionaries)
        self.app_start_date = date.today()

        self.mode_var = tk.StringVar(value="ПСК")
        self.max_date_var = tk.StringVar(value=self.app_start_date.strftime(self.config_data["date_folder_format"]))
        self.folder_var = tk.StringVar(value=str(self.default_folder("ПСК")))
        self.output_var = tk.StringVar(value=self.config_data["output_file"])
        self.status_var = tk.StringVar(value="Готово")

        self._build_ui()
        self.mode_var.trace_add("write", self._mode_changed)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)

        ttk.Label(root, text="Режим").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=6)
        mode_box = ttk.Combobox(root, textvariable=self.mode_var, values=list(self.config_data["modes"]), state="readonly", width=14)
        mode_box.grid(row=0, column=1, sticky="w", pady=6)

        ttk.Label(root, text="Дата MAX").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=6)
        date_row = ttk.Frame(root)
        date_row.grid(row=1, column=1, sticky="w", pady=6)
        self.max_date_entry = ttk.Entry(date_row, textvariable=self.max_date_var, width=16, state="readonly")
        self.max_date_entry.pack(side="left")
        self.max_date_entry.bind("<Button-1>", lambda _event: self.open_date_picker())
        ttk.Button(date_row, text="Календарь", command=self.open_date_picker).pack(side="left", padx=(6, 0))

        ttk.Label(root, text="Папка PDF").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=6)
        ttk.Entry(root, textvariable=self.folder_var).grid(row=2, column=1, sticky="ew", pady=6)
        ttk.Button(root, text="Выбрать...", command=self.choose_folder).grid(row=2, column=2, padx=(10, 0), pady=6)

        ttk.Label(root, text="Excel результат").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=6)
        ttk.Entry(root, textvariable=self.output_var, state="readonly").grid(row=3, column=1, sticky="ew", pady=6)
        ttk.Button(root, text="Открыть Excel", command=self.open_output).grid(row=3, column=2, padx=(10, 0), pady=6)

        button_row = ttk.Frame(root)
        button_row.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(18, 8))
        ttk.Button(button_row, text="Обработать", command=self.process).pack(side="left")
        ttk.Button(button_row, text="Сегодня", command=self.reset_to_today).pack(side="left", padx=(10, 0))
        ttk.Button(button_row, text="Скачать из MAX", command=self.download_from_max).pack(side="left", padx=(10, 0))
        ttk.Button(button_row, text="Разобрать наличку", command=self.parse_cash_from_max).pack(side="left", padx=(10, 0))
        ttk.Button(button_row, text="Открыть архив счетов", command=self.open_google_archive).pack(side="left", padx=(10, 0))
        ttk.Button(button_row, text="Открыть лог", command=self.open_log).pack(side="left", padx=(10, 0))

        ttk.Label(root, textvariable=self.status_var, foreground="#333333").grid(row=5, column=0, columnspan=3, sticky="w", pady=(16, 0))

    def _mode_changed(self, *_args) -> None:
        try:
            selected_date = self._selected_max_date()
        except MaxApiError:
            selected_date = date.today()
        self.folder_var.set(str(self.default_folder(self.mode_var.get(), selected_date)))

    def default_folder(self, mode: str, selected_date: date | None = None) -> Path:
        root = Path(self.config_data["root_folder"])
        suffix = self.config_data["modes"][mode].get("folder_suffix", "")
        selected_date = selected_date or date.today()
        return root / f"{selected_date.strftime(self.config_data['date_folder_format'])}{suffix}"

    def open_date_picker(self) -> None:
        try:
            selected = self._selected_max_date()
        except MaxApiError:
            selected = date.today()
        DatePicker(self, selected, self._set_max_date)

    def _set_max_date(self, selected: date) -> None:
        self.max_date_var.set(selected.strftime(self.config_data["date_folder_format"]))
        self.folder_var.set(str(self.default_folder(self.mode_var.get(), selected)))
    def choose_folder(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.config_data["root_folder"])
        if selected:
            self.folder_var.set(selected)

    def reset_to_today(self) -> None:
        self._set_max_date(date.today())

    def process(self) -> None:
        folder = Path(self.folder_var.get())
        if not folder.exists() or not folder.is_dir():
            messagebox.showerror("Папка не найдена", f"Папка не существует:\n{folder}")
            return

        try:
            mode = self.mode_var.get()
            sheet_name = self.config_data["modes"][mode]["sheet_name"]
            output = Path(self.output_var.get())
            records = process_folder(folder, self.rules)
            enrich_payment_records_from_google(records, load_env())
            selected_date = folder_date_from_path(folder, self.config_data["date_folder_format"])
        except Exception as exc:
            self.logger.exception("Payment folder processing failed: %s", folder)
            messagebox.showerror("Ошибка обработки", str(exc))
            self.status_var.set("Ошибка обработки")
            return

        def save_records(edited_records):
            existing_records = read_records_from_workbook(output, sheet_name)
            merged_records = merge_records_for_date(existing_records, edited_records, selected_date)
            write_records_to_workbook(output, sheet_name, merged_records, self.references)
            sync_payment_sheets(records_for_date(merged_records, selected_date), edited_records, load_env(), archive_file_paths=collect_existing_files(folder), mode=mode)
            self.status_var.set(f"Сохранено: {len(edited_records)} строк -> Excel и Google")

        self.status_var.set(f"Обработано: {len(records)} PDF. Проверьте таблицу и сохраните.")
        TableEditor(self, records, self.references, save_records)

    def download_from_max(self) -> None:
        try:
            selected_date = self._selected_max_date_for_download()
            folder = self.default_folder(self.mode_var.get(), selected_date)
            inbox = Path(self.config_data["root_folder"]) / ".max_inbox" / folder.name
            other = Path(self.config_data["root_folder"]) / "_прочее_MAX" / folder.name
            self.folder_var.set(str(folder))
            env = load_env()
            client, chat_id, count = build_client_from_env(env, self.mode_var.get())
            self.status_var.set("Скачиваю вложения из MAX...")
            self.update_idletasks()
            summary = download_chat_files_for_date(client, chat_id, inbox, selected_date, count=count)
            payment_orders, other_files = sort_downloaded_files(summary.downloaded, folder, other, is_payment_order_pdf)
            misplaced_payment_orders = [path for path in collect_existing_files(other) if is_payment_order_pdf(path)]
            restored_payment_orders, _restored_other = sort_downloaded_files(misplaced_payment_orders, folder, other, is_payment_order_pdf)
            existing_archive_files = [path for path in collect_existing_files(other) if not is_payment_order_pdf(path)]
            payment_orders = [path for path in merge_files_by_name([collect_existing_files(folder), restored_payment_orders, payment_orders]) if path.exists()]
            archive_files = [path for path in merge_files_by_name([existing_archive_files, other_files]) if path.exists() and not is_payment_order_pdf(path)]
            summary.sorted_payment_orders = payment_orders
            summary.sorted_other_files = other_files
            archive_path, google_rows, invoice_records = self._write_invoice_archives(client, chat_id, count, selected_date, archive_files, payment_orders, env)
            sheet_name = self.config_data["modes"][self.mode_var.get()]["sheet_name"]
            output = Path(self.output_var.get())
            payment_records = process_folder(folder, self.rules) if folder.exists() else []
            enrich_payment_records_from_google(payment_records, env)
            archive_payment_records = list(payment_records)
            payment_records.extend(invoice_text_operation_records_to_payment_records(invoice_records))
            cash_archive_path, cash_records = self._write_cash_result(selected_date, env, self.mode_var.get())
            payment_records.extend(cash_records_to_payment_records(cash_records))
            existing_records = read_records_from_workbook(output, sheet_name)
            merged_records = merge_records_for_date(existing_records, payment_records, selected_date)
            write_records_to_workbook(output, sheet_name, merged_records, self.references)
            sync_payment_sheets(records_for_date(merged_records, selected_date), archive_payment_records, env, archive_file_paths=payment_orders, mode=self.mode_var.get())
        except MaxApiError as exc:
            self.logger.exception("MAX download failed")
            messagebox.showerror("MAX API", f"{exc}\n\nСоздайте файл .env рядом с программой по примеру .env.example.")
            self.status_var.set("Ошибка скачивания из MAX")
            return
        except Exception as exc:
            self.logger.exception("Unexpected MAX workflow error")
            messagebox.showerror("MAX API", str(exc))
            self.status_var.set("Ошибка скачивания из MAX")
            return

        self.logger.info(
            "MAX download finished: downloaded=%s payment_orders=%s other_files=%s skipped=%s no_url=%s google_rows=%s archive=%s",
            len(summary.downloaded),
            len(summary.sorted_payment_orders or []),
            len(summary.sorted_other_files or []),
            len(summary.skipped),
            len(summary.no_url),
            google_rows,
            archive_path,
        )
        message = (
            f"Скачано вложений: {len(summary.downloaded)}\n"
            f"Платежных поручений: {len(summary.sorted_payment_orders or [])}\n"
            f"Прочих файлов: {len(summary.sorted_other_files or [])}\n"
            f"Пропущено как уже скачанные: {len(summary.skipped)}\n"
            f"Найдены без ссылки на файл: {len(summary.no_url)}\n"
            f"Черновик архива: {archive_path}\n"
            f"Строк в Google архиве: {google_rows}\n"
            f"Строк в Excel листе {sheet_name}: {len(payment_records)}\n"
            f"Наличка: {len(cash_records)} строк добавлено в лист {sheet_name}\n"
            f"Черновик налички: {cash_archive_path}"
        )
        self.status_var.set(f"MAX: безнал {len(payment_records)} строк, наличка {len(cash_records)} строк")
        messagebox.showinfo("MAX API", message)

    def parse_cash_from_max(self) -> None:
        try:
            selected_date = self._selected_max_date_for_download()
            env = load_env()
            archive_path, records = self._write_cash_result(selected_date, env, self.mode_var.get())
            sheet_name = self.config_data["modes"][self.mode_var.get()]["sheet_name"]
            output = Path(self.output_var.get())
            payment_records = cash_records_to_payment_records(records)
            existing_records = read_records_from_workbook(output, sheet_name)
            combined_records = merge_cash_records_for_date(existing_records, payment_records, selected_date)
            write_records_to_workbook(output, sheet_name, combined_records, self.references)
            sync_payment_sheets(records_for_date(combined_records, selected_date), [], env)
        except Exception as exc:
            self.logger.exception("Cash MAX parsing failed")
            messagebox.showerror("Наличка MAX", str(exc), parent=self)
            self.status_var.set("Ошибка разбора налички")
            return
        self.status_var.set(f"Наличка: {len(records)} строк -> {sheet_name}")
        messagebox.showinfo("Наличка MAX", f"Разобрано строк: {len(records)}\nЧерновик: {archive_path}\nExcel лист: {sheet_name}", parent=self)

        def save_records(edited_records):
            current_records = read_records_from_workbook(Path(self.output_var.get()), sheet_name)
            merged_records = merge_cash_records_for_date(current_records, edited_records, selected_date)
            write_records_to_workbook(Path(self.output_var.get()), sheet_name, merged_records, self.references)
            sync_payment_sheets(records_for_date(merged_records, selected_date), [], env)
            self.status_var.set(f"Сохранено: {len(edited_records)} строк налички -> Excel и Google")

        TableEditor(self, payment_records, self.references, save_records)

    def _selected_max_date(self) -> date:
        try:
            return datetime.strptime(self.max_date_var.get().strip(), self.config_data["date_folder_format"]).date()
        except ValueError as exc:
            raise MaxApiError(f"Дата MAX должна быть в формате {date.today().strftime(self.config_data['date_folder_format'])}") from exc

    def _selected_max_date_for_download(self) -> date:
        return self._selected_max_date()

    def _write_invoice_archives(self, client, chat_id: str, count: int, selected_date: date, other_files: list[Path], payment_orders: list[Path], env: dict[str, str]) -> tuple[Path, int, list]:
        messages = get_messages_for_date(client, chat_id, selected_date, count=count)
        records = create_invoice_archive_records(
            messages=messages,
            mode=self.mode_var.get(),
            chat_id=chat_id,
            dictionaries=self.dictionaries,
            reference_lists=self.references,
        )
        payment_records = parse_payment_order_files(payment_orders, self.rules, self.logger)
        other_names = {path.name for path in other_files}
        records = [record for record in records if not record.file_name or record.file_name in other_names]
        archive_records = [record for record in records if record.file_name]
        local_files_by_name = {path.name: path for path in other_files}
        enrich_invoice_records_from_files(archive_records, local_files_by_name)
        mark_paid_records(archive_records, payment_records)
        google_rows = self._write_google_invoice_archive(archive_records, other_files, env)
        archive_dir = Path(self.config_data["root_folder"]) / "_архив_счетов_черновик"
        archive_path = archive_dir / f"{selected_date.strftime(self.config_data['date_folder_format'])}.xlsx"
        write_invoice_archive_xlsx(archive_path, archive_records)
        return archive_path, google_rows, records

    def _write_cash_result(self, selected_date: date, env: dict[str, str], mode: str = "ПСК") -> tuple[Path, list]:
        token = env.get("MAX_BOT_TOKEN", "").strip()
        chat_id = cash_chat_id_from_env(env, mode)
        count = int(env.get("MAX_MESSAGE_COUNT", "100") or "100")
        if not token:
            raise MaxApiError("Не найден MAX_BOT_TOKEN")
        if not chat_id:
            raise MaxApiError("Не найден MAX_CASH_CHAT_ID")
        client = MaxApiClient(token)
        messages = get_messages_for_date(client, chat_id, selected_date, count=count)
        records = create_cash_archive_records(
            messages=messages,
            chat_id=chat_id,
            dictionaries=self.dictionaries,
            reference_lists=self.references,
        )
        archive_dir = Path(self.config_data["root_folder"]) / "_архив_налички_черновик"
        archive_path = archive_dir / f"{selected_date.strftime(self.config_data['date_folder_format'])}.xlsx"
        write_cash_archive_xlsx(archive_path, records)
        return archive_path, records

    def _write_google_invoice_archive(self, records, other_files: list[Path], env: dict[str, str]) -> int:
        if not records:
            return 0
        try:
            settings = load_google_settings(env)
            if not settings.archive_spreadsheet_id or not settings.archive_root_folder_id:
                return 0
            credentials = get_credentials(settings)
            drive_service = build_drive_service(credentials)
            verify_drive_account(drive_service, env.get("GOOGLE_ALLOWED_EMAIL", "pcknew.tech@gmail.com"))
            sheets_service = build_sheets_service(credentials)
            local_files_by_name = {path.name: path for path in other_files}
            setup_archive_sheet(sheets_service, settings.archive_spreadsheet_id, settings.archive_sheet_name)
            existing_google_records = read_archive_records(
                sheets_service, settings.archive_spreadsheet_id, settings.archive_sheet_name,
            )
            prepare_records_for_google_drive(
                drive_service=drive_service,
                records=records,
                local_files_by_name=local_files_by_name,
                root_folder_id=settings.archive_root_folder_id,
                dictionaries=self.dictionaries,
                existing_records=existing_google_records,
            )
            append_archive_records(sheets_service, settings.archive_spreadsheet_id, settings.archive_sheet_name, records)
            return len(records)
        except Exception as exc:
            self.logger.exception("Google invoice archive update failed")
            messagebox.showwarning("Google архив", f"Локальный архив создан, но Google архив не обновлен:\n{exc}", parent=self)
            return 0

    def open_log(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            self.log_path.write_text("", encoding="utf-8")
        os.startfile(self.log_path)

    def open_output(self) -> None:
        output = Path(self.output_var.get())
        if not output.exists():
            messagebox.showwarning("Файл еще не создан", "Сначала обработайте папку.")
            return
        os.startfile(output)

    def open_google_archive(self) -> None:
        try:
            settings = load_google_settings(load_env())
        except Exception as exc:
            self.logger.exception("Google archive URL open failed")
            messagebox.showerror("Google архив", f"Не удалось прочитать настройки Google архива:\n{exc}", parent=self)
            return
        if not settings.archive_spreadsheet_id:
            messagebox.showwarning("Google архив", "В .env не указан GOOGLE_ARCHIVE_SPREADSHEET_ID.", parent=self)
            return
        webbrowser.open(google_spreadsheet_url(settings.archive_spreadsheet_id))


def main() -> None:
    app = PaymentProcessorApp()
    app.mainloop()


def collect_existing_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return [path for path in folder.iterdir() if path.is_file()]


def merge_files_by_name(file_groups: list[list[Path]]) -> list[Path]:
    merged: dict[str, Path] = {}
    for files in file_groups:
        for path in files:
            merged[path.name] = path
    return list(merged.values())


def parse_payment_order_files(payment_orders: list[Path], rules: dict, logger: logging.Logger):
    records = []
    seen: set[tuple[str, str, str]] = set()
    for path in payment_orders:
        try:
            record = parse_payment_pdf(path, rules)
        except Exception:
            logger.exception("Payment order parse failed for paid-status matching: %s", path)
            continue
        key = (record.counterparty, record.invoice_number, record.amount)
        if key in seen:
            continue
        seen.add(key)
        records.append(record)
    return records


def merge_cash_records_for_date(
    existing_records: list[PaymentRecord],
    cash_records: list[PaymentRecord],
    selected_date: date,
) -> list[PaymentRecord]:
    if not cash_records:
        return existing_records
    date_text = selected_date.strftime("%Y-%m-%d")
    cash_payment_types = {record.payment_type for record in cash_records if record.payment_type}
    cash_names = {record.name for record in cash_records if record.name}
    kept_records = [
        record
        for record in existing_records
        if record.name not in cash_names
        and not (record.date == date_text and record.payment_type in cash_payment_types)
    ]
    return kept_records + cash_records


def merge_records_for_date(
    existing_records: list[PaymentRecord],
    new_records: list[PaymentRecord],
    selected_date: date,
) -> list[PaymentRecord]:
    if not new_records:
        return existing_records
    date_text = selected_date.strftime("%Y-%m-%d")
    new_names = {record.name for record in new_records if record.name}
    new_has_pdf = any((record.name or "").lower().endswith(".pdf") for record in new_records)
    kept_records = []
    for record in existing_records:
        if record.name in new_names:
            continue
        if new_has_pdf and record.date == date_text and (record.name or "").lower().endswith(".pdf"):
            continue
        kept_records.append(record)
    return kept_records + new_records


def folder_date_from_path(folder: Path, date_format: str) -> date:
    token = folder.name.split()[0]
    try:
        return datetime.strptime(token, date_format).date()
    except ValueError:
        return date.today()


def records_for_date(records: list[PaymentRecord], selected_date: date) -> list[PaymentRecord]:
    date_text = selected_date.strftime("%Y-%m-%d")
    return [record for record in records if record.date == date_text]


def sync_payment_sheets(
    final_records: list[PaymentRecord],
    archive_records: list[PaymentRecord],
    env: dict[str, str],
    archive_file_paths: list[Path] | None = None,
    mode: str = "ПСК",
) -> tuple[int, int]:
    settings = load_google_settings(env)
    if not settings.archive_spreadsheet_id:
        raise RuntimeError("В .env не указан GOOGLE_ARCHIVE_SPREADSHEET_ID")
    credentials = get_credentials(settings)
    sheets_service = build_sheets_service(credentials)
    setup_payment_sheets(sheets_service, settings.archive_spreadsheet_id)
    final_rows = apply_mode_defaults(final_records, mode)
    archive_rows = [replace(record, invoice_link="") for record in archive_records]
    paths_by_name = {
        path.name.casefold(): path
        for path in (archive_file_paths or [])
        if path.exists() and path.is_file()
    }
    if paths_by_name and archive_rows:
        drive_service = build_drive_service(credentials)
        verify_drive_account(drive_service, env.get("GOOGLE_ALLOWED_EMAIL", "pcknew.tech@gmail.com"))
        payment_root_id = env.get(
            "GOOGLE_PAYMENT_ROOT_FOLDER_ID",
            "1jB4mkAxrfykCC_N5BO4P-jx0QSEsiQhX",
        ).strip()
        folder_cache = {}
        file_cache = {}
        for record in archive_rows:
            source = paths_by_name.get(record.name.casefold())
            if source is None or not record.date:
                continue
            record.invoice_link = ensure_payment_file(
                drive_service,
                payment_root_id,
                source,
                date.fromisoformat(record.date),
                mode,
                folder_cache,
                file_cache,
            )
    archive_rows = dedupe_payment_records_by_identity(archive_rows)
    final_updated, final_appended = upsert_final_rows(
        sheets_service, settings.archive_spreadsheet_id, final_rows,
        sheet_name=final_sheet_name_for_mode(mode),
    )
    updated, appended = upsert_payment_archive(
        sheets_service, settings.archive_spreadsheet_id, archive_rows,
    )
    return final_updated + final_appended, updated + appended
def enrich_payment_records_from_google(records: list[PaymentRecord], env: dict[str, str]) -> int:
    if not records:
        return 0
    settings = load_google_settings(env)
    if not settings.archive_spreadsheet_id:
        raise RuntimeError("В .env не указан GOOGLE_ARCHIVE_SPREADSHEET_ID")
    sheets_service = build_sheets_service(get_credentials(settings))
    archive_records = read_archive_records(
        sheets_service,
        settings.archive_spreadsheet_id,
        settings.archive_sheet_name,
    )
    return enrich_payment_records_from_archive(records, archive_records)

def google_spreadsheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


if __name__ == "__main__":
    main()

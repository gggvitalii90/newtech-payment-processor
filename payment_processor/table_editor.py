from __future__ import annotations

from collections.abc import Callable
import tkinter as tk
from tkinter import messagebox, ttk

from .models import COLUMNS, PaymentRecord


COPY_EXCLUDED_COLUMNS = {"Name"}


def rows_to_tsv(
    rows: list[list[str]],
    columns: list[str] = COLUMNS,
    excluded_columns: set[str] | None = None,
    include_headers: bool = False,
) -> str:
    excluded_columns = excluded_columns or set()
    included_indices = [idx for idx, column in enumerate(columns) if column not in excluded_columns]
    output_rows: list[list[str]] = []
    if include_headers:
        output_rows.append([columns[idx] for idx in included_indices])
    output_rows.extend([[row[idx] if idx < len(row) else "" for idx in included_indices] for row in rows])
    return "\n".join("\t".join(_clipboard_cell(value) for value in row) for row in output_rows)


def _clipboard_cell(value: str) -> str:
    return str(value or "").replace("\r\n", " ").replace("\n", " ").replace("\t", " ").strip()


class TableEditor(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Tk,
        records: list[PaymentRecord],
        reference_lists: dict[str, list[str]],
        save_callback: Callable[[list[PaymentRecord]], None],
    ) -> None:
        super().__init__(parent)
        self.title("Проверка и редактирование результата")
        self.geometry("1500x760")
        self.minsize(1000, 520)
        self.records = records
        self.rows = [[str(value or "") for value in record.as_row()] for record in records]
        self.reference_lists = reference_lists
        self.save_callback = save_callback
        self.visible_indices = list(range(len(self.rows)))
        self.sort_state: tuple[str, bool] | None = None
        self.active_editor: tk.Widget | None = None

        self.filter_column = tk.StringVar(value="Все колонки")
        self.filter_text = tk.StringVar()
        self.status_var = tk.StringVar(value=f"Строк: {len(self.rows)}")

        self._build_ui()
        self._refresh_table()

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=(10, 8))
        toolbar.pack(fill="x")

        ttk.Label(toolbar, text="Фильтр").pack(side="left", padx=(0, 6))
        columns = ["Все колонки", *COLUMNS]
        ttk.Combobox(toolbar, textvariable=self.filter_column, values=columns, state="readonly", width=24).pack(side="left")
        filter_entry = ttk.Entry(toolbar, textvariable=self.filter_text, width=36)
        filter_entry.pack(side="left", padx=(8, 6))
        filter_entry.bind("<Return>", lambda _event: self.apply_filter())
        ttk.Button(toolbar, text="Применить", command=self.apply_filter).pack(side="left")
        ttk.Button(toolbar, text="Сбросить", command=self.clear_filter).pack(side="left", padx=(6, 18))
        ttk.Button(toolbar, text="Скопировать", command=self.copy_for_transfer).pack(side="left")
        ttk.Button(toolbar, text="Сохранить в Excel", command=self.save).pack(side="left")
        ttk.Label(toolbar, textvariable=self.status_var).pack(side="right")

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(frame, columns=COLUMNS, show="headings")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        widths = {
            "Name": 210,
            "Дата": 100,
            "Тип операции": 110,
            "Тип оплаты": 150,
            "Банк": 160,
            "Контрагент": 340,
            "Номер счета": 130,
            "Объект": 150,
            "Проект": 150,
            "Статья бюджета": 180,
            "Ответственный": 150,
            "Назначение платежа": 420,
            "Ссылка на счет": 140,
            "Сумма": 110,
        }
        for column in COLUMNS:
            self.tree.heading(column, text=column, command=lambda col=column: self.sort_by(col))
            self.tree.column(column, width=widths.get(column, 120), minwidth=70, stretch=False)

        self.tree.bind("<Double-1>", self.start_edit)
        self.tree.bind("<Control-c>", lambda _event: self.copy_for_transfer())
        self.tree.bind("<Control-C>", lambda _event: self.copy_for_transfer())

    def apply_filter(self) -> None:
        needle = self.filter_text.get().strip().lower()
        selected = self.filter_column.get()
        if not needle:
            self.visible_indices = list(range(len(self.rows)))
        elif selected == "Все колонки":
            self.visible_indices = [
                idx for idx, row in enumerate(self.rows) if any(needle in value.lower() for value in row)
            ]
        else:
            col_idx = COLUMNS.index(selected)
            self.visible_indices = [
                idx for idx, row in enumerate(self.rows) if needle in row[col_idx].lower()
            ]
        self._apply_sort_state()
        self._refresh_table()

    def clear_filter(self) -> None:
        self.filter_text.set("")
        self.filter_column.set("Все колонки")
        self.visible_indices = list(range(len(self.rows)))
        self._apply_sort_state()
        self._refresh_table()

    def sort_by(self, column: str) -> None:
        reverse = False
        if self.sort_state and self.sort_state[0] == column:
            reverse = not self.sort_state[1]
        self.sort_state = (column, reverse)
        self._apply_sort_state()
        self._refresh_table()

    def _apply_sort_state(self) -> None:
        if not self.sort_state:
            return
        column, reverse = self.sort_state
        col_idx = COLUMNS.index(column)
        self.visible_indices.sort(key=lambda idx: self._sort_key(self.rows[idx][col_idx]), reverse=reverse)

    @staticmethod
    def _sort_key(value: str) -> tuple[int, float | str]:
        normalized = value.replace(" ", "").replace(",", ".")
        try:
            return (0, float(normalized))
        except ValueError:
            return (1, value.lower())

    def _refresh_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for idx in self.visible_indices:
            self.tree.insert("", "end", iid=str(idx), values=self.rows[idx])
        self.status_var.set(f"Показано: {len(self.visible_indices)} из {len(self.rows)}")

    def start_edit(self, event: tk.Event) -> None:
        if self.active_editor is not None:
            self.active_editor.destroy()
            self.active_editor = None

        row_id = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if not row_id or not column_id:
            return
        col_idx = int(column_id.replace("#", "")) - 1
        if col_idx < 0 or col_idx >= len(COLUMNS):
            return

        bbox = self.tree.bbox(row_id, column_id)
        if not bbox:
            return
        x, y, width, height = bbox
        row_idx = int(row_id)
        column = COLUMNS[col_idx]
        current = self.rows[row_idx][col_idx]

        values = self.reference_lists.get(column)
        if values:
            editor: tk.Widget = ttk.Combobox(self.tree, values=values, state="normal")
            editor.insert(0, current)
        else:
            editor = ttk.Entry(self.tree)
            editor.insert(0, current)
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        if isinstance(editor, ttk.Entry):
            editor.select_range(0, "end")

        def commit(_event: tk.Event | None = None) -> None:
            value = editor.get() if hasattr(editor, "get") else ""
            self.rows[row_idx][col_idx] = value
            self.tree.set(row_id, column, value)
            editor.destroy()
            self.active_editor = None

        def cancel(_event: tk.Event | None = None) -> None:
            editor.destroy()
            self.active_editor = None

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", cancel)
        self.active_editor = editor

    def save(self) -> None:
        try:
            records = [PaymentRecord.from_row(row) for row in self.rows]
            self.save_callback(records)
        except Exception as exc:
            messagebox.showerror("Ошибка сохранения", str(exc), parent=self)
            return
        messagebox.showinfo("Сохранено", "Excel-файл обновлен.", parent=self)

    def copy_for_transfer(self) -> None:
        selected = [int(item) for item in self.tree.selection()]
        row_indices = selected or self.visible_indices
        rows = [self.rows[idx] for idx in row_indices]
        text = rows_to_tsv(rows, excluded_columns=COPY_EXCLUDED_COLUMNS)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_var.set(f"Скопировано: {len(rows)} строк без Name")

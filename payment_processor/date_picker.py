from __future__ import annotations

import calendar
from datetime import date
import tkinter as tk
from tkinter import ttk
from typing import Callable


MONTH_NAMES = (
    "",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)
WEEKDAY_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + delta
    return divmod(index, 12)[0], divmod(index, 12)[1] + 1


def month_grid(year: int, month: int) -> list[list[int]]:
    return calendar.Calendar(firstweekday=calendar.MONDAY).monthdayscalendar(year, month)


class DatePicker(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        selected_date: date,
        on_select: Callable[[date], None],
    ) -> None:
        super().__init__(parent)
        self.title("Выберите дату")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.year = selected_date.year
        self.month = selected_date.month
        self.selected_date = selected_date
        self.on_select = on_select

        self.header = ttk.Frame(self, padding=(8, 8, 8, 2))
        self.header.pack(fill="x")
        ttk.Button(self.header, text="<", width=3, command=lambda: self._move_month(-1)).pack(side="left")
        self.month_label = ttk.Label(self.header, anchor="center", width=20)
        self.month_label.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(self.header, text=">", width=3, command=lambda: self._move_month(1)).pack(side="right")

        self.calendar_frame = ttk.Frame(self, padding=(8, 2, 8, 8))
        self.calendar_frame.pack()
        self._render_month()
        self.grab_set()
        self.focus_set()

    def _move_month(self, delta: int) -> None:
        self.year, self.month = shift_month(self.year, self.month, delta)
        self._render_month()

    def _render_month(self) -> None:
        self.month_label.configure(text=f"{MONTH_NAMES[self.month]} {self.year}")
        for child in self.calendar_frame.winfo_children():
            child.destroy()
        for column, name in enumerate(WEEKDAY_NAMES):
            ttk.Label(self.calendar_frame, text=name, anchor="center", width=4).grid(row=0, column=column, padx=1, pady=1)
        for row_index, week in enumerate(month_grid(self.year, self.month), start=1):
            for column, day in enumerate(week):
                if not day:
                    ttk.Label(self.calendar_frame, text="", width=4).grid(row=row_index, column=column)
                    continue
                button = ttk.Button(
                    self.calendar_frame,
                    text=str(day),
                    width=4,
                    command=lambda value=day: self._choose(value),
                )
                button.grid(row=row_index, column=column, padx=1, pady=1)

    def _choose(self, day: int) -> None:
        selected = date(self.year, self.month, day)
        self.on_select(selected)
        self.destroy()
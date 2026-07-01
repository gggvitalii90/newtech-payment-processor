# Google Payment Workbook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в существующую Google-таблицу листы `Итоговая` и `Архив ПП`, подключить их к рабочим сценариям и завершить разбор конвертации и выбор даты календарем.

**Architecture:** Чистые функции преобразуют `PaymentRecord` в строки Google. Отдельный Google-адаптер создает и форматирует листы, полностью заменяет `Итоговую` и выполняет upsert архива. UI вызывает единый метод синхронизации после формирования или сохранения проверенных записей.

**Tech Stack:** Python 3.13, Tkinter, Google Sheets API v4, pytest.

## Global Constraints

- `Архив счетов` не пересоздавать и не очищать.
- Не добавлять внешнюю зависимость календаря.
- `Итоговая` содержит выбранный день; `Архив ПП` хранит историю.
- Наличка входит только в `Итоговую`.
- Запись идемпотентна; локальный Excel остается резервным.

---

### Task 1: Google-листы платежей

**Files:** Create `payment_processor/google_payments.py`; Test `tests/test_google_payments.py`.

**Interfaces:** `setup_payment_sheets(service, spreadsheet_id)`, `replace_final_rows(...)`, `upsert_payment_archive(...)`.

- [ ] Написать падающие тесты создания двух листов, заголовков и фильтров.
- [ ] Реализовать константы листов и настройку.
- [ ] Написать падающий тест полной замены `Итоговой`.
- [ ] Реализовать очистку данных и запись текущего набора.
- [ ] Написать падающий тест upsert по имени PDF.
- [ ] Реализовать update совпавших ключей и append новых.
- [ ] Запустить `pytest tests/test_google_payments.py -q -p no:cacheprovider`.

### Task 2: Синхронизация сценариев

**Files:** Modify `payment_processor/app.py`; Test `tests/test_app_google_payments.py`.

**Interfaces:** `sync_payment_sheets(final_records, archive_records, env) -> tuple[int, int]`.

- [ ] Написать падающий тест построения Google service и передачи наборов.
- [ ] Реализовать `sync_payment_sheets`.
- [ ] Подключить после сохранения PDF, загрузки MAX и сохранения налички.
- [ ] Проверить, что наличка не попадает в `Архив ПП`.
- [ ] Запустить тесты app и Google-слоя.

### Task 3: Конвертация налички

**Files:** Modify `payment_processor/cash_archive.py`; Test `tests/test_cash_archive.py`.

- [ ] Добавить падающий тест `Конвертация ... выдано - 400 000` без слов `Расход/Приход`.
- [ ] Реализовать раннюю ветку: операция/объект/проект `Конвертация`, сумма `400000`, описание в назначении.
- [ ] Проверить `create_cash_archive_records` и весь набор тестов налички.

### Task 4: Календарь

**Files:** Create `payment_processor/date_picker.py`; Modify `payment_processor/app.py`; Test `tests/test_date_picker.py`.

**Interfaces:** `shift_month(year, month, delta)`, `month_grid(year, month)`, `DatePicker`.

- [ ] Написать падающие тесты сетки и перехода декабря/января.
- [ ] Реализовать календарную модель и Tkinter popup.
- [ ] Заменить ручной Entry на readonly-поле с кнопкой календаря.
- [ ] При выборе обновлять дату и путь папки.
- [ ] Запустить тесты календаря и приложения.

### Task 5: Проверка и живые листы

**Files:** Modify `README.md`.

- [ ] Запустить целевые и полный pytest.
- [ ] Создать `Итоговая` и `Архив ПП` через текущие OAuth-настройки.
- [ ] Перечитать metadata и заголовки листов.
- [ ] Обновить README и перезапустить GUI.
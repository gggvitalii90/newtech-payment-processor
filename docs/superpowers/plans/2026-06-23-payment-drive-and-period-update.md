# Payment Drive Structure And Period Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Переместить существующие дневные папки ПП в структуру год/месяц/дата, добавить ссылки на ПП, исправить схему `Архива ПП` и обработать 20–22 июня 2026 года.

**Architecture:** `payment_drive.py` отвечает только за структуру и файлы Drive. `google_payments.py` хранит отдельные схемы строк для `Итоговой` и `Архива ПП`. Исторический загрузчик сначала обеспечивает файлы и ссылки ПП, затем пишет листы; отдельный периодический сценарий обновляет счета, ПП и итоговые операции.

**Tech Stack:** Python 3.13, Google Drive API v3, Google Sheets API v4, MAX API, pytest.

## Global Constraints

- Существующие дневные папки только перемещаются; они не копируются и не создаются заново.
- Целевая структура: `Год / Месяц / Дата`.
- Суффикс `ИС` у дневной папки сохраняется.
- `Архив ПП` содержит 10 утвержденных колонок и `Ссылку на ПП`.
- ПП без найденного счета сохраняется в `Архиве ПП` и `Итоговой`.
- Все операции должны быть идемпотентными и формировать отчет до массовой записи.

---

### Task 1: Отдельная схема Архива ПП

**Files:**
- Modify: `payment_processor/google_payments.py`
- Modify: `payment_processor/payment_history.py`
- Modify: `scripts/setup_payment_sheets.py`
- Modify: `scripts/verify_payment_sheets.py`
- Test: `tests/test_google_payments.py`

**Interfaces:**
- Produces: `PAYMENT_ARCHIVE_COLUMNS`, `payment_archive_row(record)`, отдельные диапазоны A:J для `Архива ПП`.

- [ ] Написать тесты, что `Архив ПП` использует колонки `№, Дата, Тип операции, Тип оплаты, Банк, Контрагент, Номер счета, Назначение платежа, Ссылка на ПП, Сумма`, а `Итоговая` сохраняет A:N.
- [ ] Запустить `python -m pytest tests/test_google_payments.py -q` и получить падение новых тестов.
- [ ] Реализовать отдельное преобразование строки и независимые диапазоны/форматирование листов.
- [ ] Запустить тесты и подтвердить их прохождение.

### Task 2: Безопасная реорганизация Google Drive

**Files:**
- Create: `payment_processor/payment_drive.py`
- Create: `scripts/reorganize_payment_drive.py`
- Create: `tests/test_payment_drive.py`

**Interfaces:**
- Produces: `plan_folder_moves(children)`, `ensure_year_month_folders(...)`, `move_day_folder(...)`, CSV-отчеты до и после перемещения.

- [ ] Написать тесты разбора имен `YYYY.MM.DD` и `YYYY.MM.DD ИС`, формирования года и месяца, пропуска посторонних папок и неизменности ID дневной папки.
- [ ] Запустить тесты и получить ожидаемое падение.
- [ ] Реализовать dry-run план, создание только папок годов/месяцев и изменение родителей существующих дневных папок через `addParents/removeParents`.
- [ ] Проверить тестами идемпотентность повторного запуска.
- [ ] Выполнить dry-run на папке `1jB4mkAxrfykCC_N5BO4P-jx0QSEsiQhX`, сохранить CSV и сверить число исходных папок.
- [ ] Выполнить перемещение и перечитать родителей всех дневных папок.

### Task 3: Загрузка ПП и ссылки на файлы

**Files:**
- Modify: `payment_processor/payment_drive.py`
- Modify: `scripts/backfill_payment_history.py`
- Modify: `payment_processor/google_payments.py`
- Test: `tests/test_payment_drive.py`
- Test: `tests/test_backfill_payment_history.py`

**Interfaces:**
- Produces: `ensure_payment_file(drive, root_id, path, payment_date, mode) -> str`, где результат — `webViewLink` существующего или загруженного файла.

- [ ] Написать тесты повторного использования файла по имени и MD5, загрузки недостающего файла в нужную дату и сохранения ссылки в архивной строке.
- [ ] Запустить тесты и получить ожидаемое падение.
- [ ] Реализовать индекс файлов в дневных папках и идемпотентную загрузку отсутствующих PDF.
- [ ] Заполнить `invoice_link` ссылкой на ПП только при записи `Архива ПП`; в `Итоговой` ссылка продолжает означать ссылку на счет.
- [ ] Запустить целевые тесты.

### Task 4: Обработка 20–22 июня 2026 года

**Files:**
- Modify: `scripts/backfill_payment_history.py`
- Reuse: `scripts/repair_google_invoice_archive_period.py`
- Create: `scripts/update_all_archives_period.py`
- Test: `tests/test_backfill_payment_history.py`

**Interfaces:**
- Consumes: Drive-ссылки Task 3 и существующий разбор MAX.
- Produces: идемпотентное обновление `Архива счетов`, `Архива ПП`, `Итоговой` за заданный период.

- [ ] Добавить тест оркестрации порядка: счета → ПП и ссылки → итоговые строки.
- [ ] Реализовать периодический сценарий с аргументами `--start 2026-06-20 --end 2026-06-22 --dry-run`.
- [ ] Выполнить dry-run и проверить отчеты, обязательные поля и число операций.
- [ ] Выполнить рабочий запуск только после чистого dry-run.

### Task 5: Живая миграция и итоговая проверка

**Files:**
- Modify: `README.md`
- Reuse: `scripts/verify_payment_sheets.py`

**Interfaces:**
- Produces: проверенную структуру Drive и согласованные Google-листы.

- [ ] Перезаписать `Архив ПП` по новой 10-колоночной схеме с историческими ссылками на ПП.
- [ ] Перечитать заголовки и строки `Архива ПП`, `Архива счетов`, `Итоговой`.
- [ ] Проверить отсутствие обязательных пустых полей и наличие ссылок на доступные ПП.
- [ ] Запустить `python -m pytest -q` и `python -m compileall -q payment_processor scripts`.
- [ ] Обновить README рабочими командами и итоговыми ограничениями.
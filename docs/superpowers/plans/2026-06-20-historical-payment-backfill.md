# Historical Payment Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Детерминированно заполнить `Архив ПП` и `Итоговую` всеми операциями с 01.04.2026 и сохранить возможность безопасного продолжения ежедневной работы.

**Architecture:** Модуль `payment_history.py` собирает локальные и MAX-источники, дедуплицирует PDF по SHA-256, распознает ПП и строит итоговые записи. Google-адаптер поддерживает отдельные bulk-replace функции для исторической пересборки и upsert для ежедневной работы. CLI-скрипт сохраняет checkpoint и отчеты до любой полной замены Google-листов.

**Tech Stack:** Python 3.13, MAX API, pypdf, Google Sheets API v4, pytest.

## Global Constraints

- Период начинается строго `2026-04-01` и заканчивается включительно выбранной датой.
- Ни одна распознанная операция не исключается из `Итоговой` из-за отсутствия счета.
- `Архив счетов` не изменяется и не очищается.
- Наличка не записывается в `Архив ПП`.
- Полная запись Google выполняется только после локального снимка и отчета.
- Повторный запуск не создает дубли.

---

### Task 1: Историческая семантика Google-листов

**Files:** Modify `payment_processor/google_payments.py`, `payment_processor/app.py`; Test `tests/test_google_payments.py`, `tests/test_app_google_payments.py`.

**Interfaces:** `replace_payment_archive_rows(service, spreadsheet_id, records) -> int`; `upsert_final_rows(service, spreadsheet_id, records) -> tuple[int, int]`; `sync_payment_sheets` использует upsert для обоих листов.

- [ ] Добавить падающий тест, что ежедневная синхронизация не вызывает `replace_final_rows`.
- [ ] Добавить падающий тест полного replace `Архива ПП` и составного ключа, различающего одинаковые имена в разные дни.
- [ ] Реализовать bulk replace и общий upsert по составному ключу источника, даты, контрагента, номера, суммы и назначения.
- [ ] Запустить целевые тесты Google-слоя.

### Task 2: Чистая историческая сборка

**Files:** Create `payment_processor/payment_history.py`; Test `tests/test_payment_history.py`.

**Interfaces:** `collect_payment_pdfs(root, start, end)`, `dedupe_paths_by_sha256(paths)`, `parse_payment_history(paths, rules)`, `build_final_history(payment_records, invoice_records, cash_records, direct_records)`.

- [ ] Тестом зафиксировать включение обычных и `ИС` папок только внутри периода.
- [ ] Тестом зафиксировать SHA-дедупликацию одинакового файла с разными именами.
- [ ] Тестом зафиксировать, что несовпавшее ПП остается в итоговом наборе.
- [ ] Реализовать функции и стабильную сортировку по дате, имени и сумме.
- [ ] Добавить валидацию обязательных PDF-полей и структуру issue-отчета.

### Task 3: MAX-дозагрузка и checkpoint

**Files:** Create `payment_processor/history_backfill.py`; Test `tests/test_history_backfill.py`.

**Interfaces:** `BackfillState.load/save`, `download_missing_days(...)`, `collect_direct_and_cash_operations(...)`.

- [ ] Тестом зафиксировать продолжение после уже завершенного дня.
- [ ] Тестом зафиксировать атомарную запись JSON checkpoint.
- [ ] Реализовать дневную дозагрузку через существующие `.max_downloaded.json` и `sort_downloaded_files`.
- [ ] Получить сообщения обоих чатов за диапазон и построить безфайловые и наличные записи.
- [ ] Сохранять счетчики и ошибки после каждого дня.

### Task 4: Команда полной пересборки

**Files:** Create `scripts/backfill_payment_history.py`; Modify `README.md`; Test `tests/test_backfill_payment_history.py`.

**Interfaces:** CLI `--start`, `--end`, `--skip-download`, `--dry-run`; локальные CSV и JSON summary; Google bulk replace после успешной проверки.

- [ ] Написать тест dry-run без Google-записи.
- [ ] Реализовать загрузку настроек, справочников, `Архива счетов` и вызов чистой сборки.
- [ ] Сохранить CSV снимки и issue-отчет UTF-8 BOM.
- [ ] При отсутствии критических ошибок полностью заменить два Google-листа и перечитать их.
- [ ] Документировать повторный запуск и файлы отчетов.

### Task 5: Исполнение истории

**Files:** Runtime outputs under `reports/` and existing `_ПП` folders.

- [ ] Запустить целевые тесты и синтаксическую проверку.
- [ ] Запустить dry-run с `2026-04-01` по `2026-06-20`.
- [ ] Проверить missing-field и unmatched статистику; исправить системные шаблоны распознавания тестами до записи.
- [ ] Запустить рабочую пересборку с дозагрузкой.
- [ ] Перечитать `Архив ПП!A1:N` и `Итоговая!A1:N`, проверить даты, количество строк и отсутствие пустых обязательных PDF-полей.
- [ ] Перезапустить GUI с ежедневным upsert.
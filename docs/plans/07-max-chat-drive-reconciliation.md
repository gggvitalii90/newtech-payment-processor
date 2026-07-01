# MAX Chat and Drive Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Пересчитать только чатовые поля архива счетов, проверяя каждый документ по Drive ID строки и не сопоставляя файлы по имени.

**Architecture:** Разбор MAX разделяет внешний `body` и `link.message`. Новый модуль сверки хеширует содержимое файлов из Google Drive и MAX, связывает строки с MAX-вложениями по SHA-256, а подписи назначает только по точной связи или единственной взаимной паре до/после файла. Сначала создается резервная копия и CSV-план, затем отдельный режим применяет только пять чатовых колонок и статус.

**Tech Stack:** Python 3.13, MAX Bot API, Google Drive API v3, Google Sheets API v4, pytest.

---

### Task 1: Разделить внешний body и linked message

**Files:**
- Modify: `payment_processor/max_api.py`
- Modify: `payment_processor/invoice_archive.py`
- Test: `tests/test_max_api.py`
- Test: `tests/test_invoice_archive.py`

- [x] **Step 1: Написать падающий тест внешних вложений**

```python
def test_extract_file_candidates_does_not_take_linked_attachment() -> None:
    message = {
        "body": {"mid": "outer", "text": "Объект: ПСК", "attachments": []},
        "link": {"type": "reply", "message": {"mid": "inner", "attachments": [
            {"type": "file", "filename": "invoice.pdf", "payload": {"url": "https://files/1", "token": "f1"}}
        ]}},
    }
    assert extract_file_candidates(message, allowed_extensions=()) == []
```

- [x] **Step 2: Запустить тест и подтвердить текущую ошибку**

Run: `python -m pytest tests/test_max_api.py::test_extract_file_candidates_does_not_take_linked_attachment -q`

Expected: FAIL, потому что текущий `_walk_dicts(message)` находит `link.message.attachments`.

- [x] **Step 3: Ограничить извлечение внешним body**

```python
def extract_file_candidates(message, allowed_extensions=(".pdf",)):
    body = message.get("body")
    if not isinstance(body, dict):
        return []
    return extract_body_file_candidates(body, allowed_extensions, timestamp=message.get("timestamp"))
```

Добавить отдельную функцию `extract_linked_file_candidates(message)`, которая читает только `link.message` и сохраняет `link.type` и `inner mid` у вызывающей стороны.

- [x] **Step 4: Разделить текст**

`extract_message_text()` возвращает только `body.text`. Новый `extract_linked_message_text()` возвращает только `link.message.text`. Не объединять строки автоматически.

- [x] **Step 5: Проверить тесты MAX и архива**

Run: `python -m pytest tests/test_max_api.py tests/test_invoice_archive.py -q`

Expected: PASS.

### Task 2: Реализовать безопасное назначение подписей

**Files:**
- Create: `payment_processor/max_message_matching.py`
- Test: `tests/test_max_message_matching.py`

- [x] **Step 1: Написать тесты направления и неоднозначности**

```python
def test_unique_signature_before_file_is_matched():
    matches = match_signatures([signature("s1", seq=10), file("f1", seq=11)])
    assert matches["f1"].signature_message_id == "s1"
    assert matches["f1"].confidence == "unique_pair"

def test_unique_signature_after_file_is_matched():
    matches = match_signatures([file("f1", seq=10), signature("s1", seq=11)])
    assert matches["f1"].signature_message_id == "s1"

def test_reply_signature_is_exact():
    matches = match_signatures([file("f1", seq=10), reply_signature("s1", "f1", seq=20)])
    assert matches["f1"].confidence == "exact_link"

def test_two_files_and_two_signatures_are_ambiguous_without_evidence():
    matches = match_signatures([
        signature("s1", seq=10), signature("s2", seq=11),
        file("f1", seq=12), file("f2", seq=13),
    ])
    assert matches["f1"].confidence == "ambiguous"
    assert matches["f2"].confidence == "ambiguous"
```

Каждый тест использует `body.seq`; время используется только для ограничения окна, а не для выбора направления.

- [x] **Step 2: Запустить тесты и увидеть RED**

Run: `python -m pytest tests/test_max_message_matching.py -q`

Expected: ERROR импорта отсутствующего модуля.

- [x] **Step 3: Добавить модели и точные связи**

```python
@dataclass(frozen=True)
class SignatureMatch:
    file_message_id: str
    signature_message_id: str
    confidence: str  # exact_body, exact_link, unique_pair, ambiguous
    signature: dict[str, str]

CHAT_FIELDS = ("object_name", "project", "budget_item", "responsible", "purpose")
```

Точный `body` и `reply/forward` имеют приоритет. Уже использованная точная подпись не участвует в эвристическом назначении.

- [x] **Step 4: Добавить взаимно-однозначное сопоставление**

Для неподтвержденных сообщений собрать кандидатов и до, и после файла в пределах 30 минут. Принять пару только когда файл имеет одного кандидата и этот кандидат имеет один файл. Иначе вернуть `ambiguous` без подписи.

- [x] **Step 5: Запустить тесты сопоставления**

Run: `python -m pytest tests/test_max_message_matching.py -q`

Expected: PASS.

### Task 3: Читать документы строго по Drive ID и связывать с MAX по хешу

**Files:**
- Modify: `payment_processor/google_api.py`
- Create: `payment_processor/archive_reconciliation.py`
- Test: `tests/test_archive_reconciliation.py`

- [x] **Step 1: Написать падающий тест Drive ID**

```python
def test_reconciliation_matches_drive_and_max_by_sha256_not_name(tmp_path):
    drive = b"invoice-a"
    max_files = {"same.pdf": b"invoice-b", "other-name.pdf": b"invoice-a"}
    result = match_by_content(drive, max_files)
    assert result.max_name == "other-name.pdf"
```

- [x] **Step 2: Добавить скачивание файла по ID**

```python
def download_drive_file(drive_service, file_id: str, destination: Path) -> Path:
    request = drive_service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with destination.open("wb") as handle:
        downloader = MediaIoBaseDownload(handle, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return destination
```

- [x] **Step 3: Реализовать SHA-256 индекс**

`archive_reconciliation.py` извлекает Drive ID из каждой ссылки, скачивает этот файл, вычисляет SHA-256 и сопоставляет только с MAX-вложениями с тем же хешем. Имя хранится в отчете, но не участвует в ключе.

- [x] **Step 4: Запустить тесты сверки**

Run: `python -m pytest tests/test_archive_reconciliation.py -q`

Expected: PASS.

### Task 4: Сформировать резервную копию и план обновления

**Files:**
- Create: `scripts/reconcile_google_archive_chat_fields.py`
- Test: `tests/test_archive_reconciliation.py`
- Output: `reports/archive_chat_reconciliation_backup_2026-06-17.csv`
- Output: `reports/archive_chat_reconciliation_plan_2026-06-17.csv`

- [x] **Step 1: Добавить режим audit по умолчанию**

```python
parser.add_argument("--apply", action="store_true")
parser.add_argument("--start", default="2026-04-01")
parser.add_argument("--end", default="2026-06-15")
```

Без `--apply` скрипт не вызывает методы записи Sheets.

- [x] **Step 2: Сохранить полный снимок листа**

Backup содержит номер строки и все колонки до любой обработки.

- [x] **Step 3: Сформировать план**

Для каждой строки записать Drive ID, SHA-256, MAX message/file IDs, старые и новые пять чатовых полей, `confidence`, кандидатов и причину неоднозначности.

- [x] **Step 4: Запустить аудит**

Run: `python scripts/reconcile_google_archive_chat_fields.py --start 2026-04-01 --end 2026-06-15`

Expected: созданы backup и plan; Google Sheet не изменен.

- [x] **Step 5: Проверить инварианты**

Документные колонки в backup и плане совпадают. Строки `ambiguous` сохраняют
прежние чатовые поля и статус; неоднозначность отражается только в CSV-плане.

### Task 5: Применить и проверить Google Sheet

**Files:**
- Modify: `scripts/reconcile_google_archive_chat_fields.py`
- Output: `reports/archive_chat_reconciliation_applied_2026-06-17.csv`

- [x] **Step 1: Запустить полный тестовый набор затронутых модулей**

Run: `python -m pytest tests/test_max_api.py tests/test_invoice_archive.py tests/test_max_message_matching.py tests/test_archive_reconciliation.py tests/test_google_api.py tests/test_google_archive.py -q`

Expected: PASS.

- [x] **Step 2: Применить план**

Run: `python scripts/reconcile_google_archive_chat_fields.py --start 2026-04-01 --end 2026-06-15 --apply`

Записывать `RAW` только пять чатовых колонок и `Статус разбора` по конкретному номеру строки.

- [x] **Step 3: Перечитать измененные строки**

Сравнить каждую записанную ячейку с планом. При расхождении завершить скрипт с ненулевым кодом и перечислить строки.

- [x] **Step 4: Подтвердить неизменность документных данных**

Сравнить backup с текущим листом для даты, типа оплаты, контрагента, номера счета, суммы, Drive-ссылки и статуса оплаты. Ожидается ноль отличий.

Примечание: рабочая папка не является Git-репозиторием, поэтому шаги commit отсутствуют.

## Результат выполнения 2026-06-17

- Проверено 386 строк архива за период 2026-04-01 — 2026-06-15.
- Первоначальный строгий план нашел 43 однозначные подписи: 31 `unique_pair` и 12 `exact_link`.
- Ошибочная политика очистила неоднозначные чатовые поля в 343 строках; это изменение отменено из полного backup.
- Восстановлено 1969 отдельных ячеек только в пяти чатовых колонках и `Статус разбора`.
- После восстановления перечитаны и проверены все 386 строк; документные колонки не изменились.
- Новая политика `preserve_ambiguous_v2` сохраняет неоднозначные строки без изменений и блокирует применение старых планов.
- Полный набор тестов: 148 passed.

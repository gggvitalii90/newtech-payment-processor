# Безопасный архив счетов: план реализации

> **Для исполнителя:** ОБЯЗАТЕЛЬНЫЙ НАВЫК: выполнять план последовательно через `superpowers:executing-plans`. Шаги используют флажки (`- [ ]`) для контроля.

**Цель:** Исправить сопоставление MAX, строго нормализовать регламентированные поля, восстановить документные данные и удалённые Drive-ссылки, после чего подготовить безопасный read-only аудит без автоматического запуска записи.

**Архитектура:** MAX-связи определяются отдельным модулем доказательств с приоритетом точных `reply` и блоков одного автора. Документные реквизиты извлекаются независимо от чатовых полей, а регламентированные значения проходят строгий справочник. Единый сценарий аудита формирует backup и план; запись в Sheets и Drive остаётся отдельным явно подтверждаемым этапом.

**Технологии:** Python 3.13, pytest, MAX Bot API, Google Drive API v3, Google Sheets API v4, openpyxl только в существующем загрузчике справочника.

**Ограничение среды:** Каталог не является Git-репозиторием, поэтому шаги commit заменены фиксацией результатов тестов и проверкой списка изменённых файлов.

---

### Задача 1: Зафиксировать старые правила регрессионными тестами

**Файлы:**
- Modify: `tests/test_invoice_archive.py`
- Modify: `tests/test_archive_reconciliation.py`
- Read: `dictionaries.json`
- Read: `rules.json`

- [ ] **Шаг 1: Добавить параметризованный тест действующих правил**

```python
@pytest.mark.parametrize(
    ("signature", "expected"),
    [
        ({"object_name": "ПСК", "project": "Конвертация", "budget_item": "Родин"}, {"object_name": "Конвертация", "project": "Конвертация", "budget_item": ""}),
        ({"object_name": "ПР", "budget_item": "Обеспечение ПР", "purpose": "Интернет"}, {"project": "Производственные расходы", "budget_item": "Аренда помещения"}),
    ],
)
def test_existing_signature_rules_remain_supported(signature, expected):
    result = normalize_signature_rules(signature, load_dictionaries())
    for key, value in expected.items():
        assert result.get(key, "") == value
```

- [ ] **Шаг 2: Запустить тест и зафиксировать текущее расхождение**

Run: `python -m pytest tests/test_invoice_archive.py::test_existing_signature_rules_remain_supported -q`

Expected: FAIL только для неполного правила `Конвертация`, которое пока не устанавливает проект во всех вариантах.

- [ ] **Шаг 3: Запустить существующий регрессионный набор до изменений**

Run: `python -m pytest tests/test_invoice_archive.py tests/test_archive_reconciliation.py tests/test_max_message_matching.py tests/test_google_archive.py -q`

Expected: существующие тесты проходят; новый тест фиксирует требуемое изменение отдельно.

---

### Задача 2: Сопоставлять подписи по `reply` и блокам одного автора

**Файлы:**
- Modify: `payment_processor/max_message_matching.py`
- Modify: `payment_processor/archive_reconciliation.py`
- Modify: `tests/test_max_message_matching.py`
- Modify: `tests/test_archive_reconciliation.py`

- [ ] **Шаг 1: Добавить автора в модель доказательств**

```python
@dataclass(frozen=True)
class MessageEvidence:
    message_id: str
    seq: int
    timestamp: float | None
    author_id: str = ""
    signature: dict[str, str] = field(default_factory=dict)
    file_ids: tuple[str, ...] = ()
    linked_message_ids: tuple[str, ...] = ()
```

- [ ] **Шаг 2: Написать падающие тесты авторского блока и отложенного reply**

```python
def test_signature_claims_preceding_unsigned_files_from_same_author_only():
    evidence = [
        file("a1", "author-a", seq=10),
        file("a2", "author-a", seq=11),
        file("b1", "author-b", seq=12),
        signature("sa", "author-a", seq=13, object_name="Аларм Моторс"),
        signature("sb", "author-b", seq=14, object_name="ПСК Ньютек"),
    ]
    matches = match_signatures_by_sequence(evidence)
    assert matches["a1"].signature_message_id == "sa"
    assert matches["a2"].signature_message_id == "sa"
    assert matches["b1"].signature_message_id == "sb"


def test_delayed_reply_is_exact_even_outside_time_window():
    evidence = [
        file("f1", "author-a", seq=10, timestamp=1),
        reply_signature("s1", "author-a", linked="file-message", seq=100, timestamp=100_000),
    ]
    match = match_signatures_by_sequence(evidence, window_seconds=60)["f1"]
    assert match.confidence == "exact_link"
```

- [ ] **Шаг 3: Запустить тесты и увидеть RED**

Run: `python -m pytest tests/test_max_message_matching.py -q`

Expected: FAIL, потому что модель не знает автора и допускает одну подпись только для одного файла.

- [ ] **Шаг 4: Реализовать приоритет точных связей и авторские блоки**

```python
def _assign_author_blocks(ordered, matches, matched_files, used_signatures):
    pending: dict[str, list[str]] = {}
    for item in ordered:
        if item.file_ids:
            pending.setdefault(item.author_id, []).extend(
                file_id for file_id in item.file_ids if file_id not in matched_files
            )
        if not item.signature or item.message_id in used_signatures:
            continue
        file_ids = pending.get(item.author_id, [])
        if not file_ids:
            continue
        for file_id in file_ids:
            matches[file_id] = SignatureMatch(
                file_id, item.message_id, "author_block", dict(item.signature)
            )
            matched_files.add(file_id)
        pending[item.author_id] = []
        used_signatures.add(item.message_id)
```

Точные `exact_body` и `exact_link` выполняются раньше `_assign_author_blocks`. Участники чужих авторов остаются в собственных очередях.

- [ ] **Шаг 5: Передать `sender.user_id` в `MessageEvidence.author_id`**

```python
def _author_id(message: dict[str, Any]) -> str:
    sender = message.get("sender") if isinstance(message.get("sender"), dict) else {}
    return str(sender.get("user_id") or sender.get("name") or "").strip()
```

- [ ] **Шаг 6: Запустить тесты сопоставления**

Run: `python -m pytest tests/test_max_message_matching.py tests/test_archive_reconciliation.py -q`

Expected: PASS.

---

### Задача 3: Сохранять SHA и распознавать смысловые дубли

**Файлы:**
- Modify: `payment_processor/archive_reconciliation.py`
- Modify: `payment_processor/max_message_matching.py`
- Modify: `tests/test_archive_reconciliation.py`
- Modify: `tests/test_max_message_matching.py`

- [ ] **Шаг 1: Добавить модель идентичности документа**

```python
@dataclass(frozen=True)
class DocumentIdentity:
    counterparty: str
    invoice_number: str
    invoice_date: str
    amount: str

    def complete(self) -> bool:
        return all((self.counterparty, self.invoice_number, self.invoice_date, self.amount))
```

- [ ] **Шаг 2: Написать падающие тесты двух уровней дублей**

```python
def test_equal_sha_is_exact_duplicate():
    groups = group_document_duplicates({"a": "sha-1", "b": "sha-1"}, {})
    assert groups["b"] == DuplicateOf("a", "sha_exact")


def test_different_sha_with_equal_complete_identity_is_semantic_duplicate():
    identity = DocumentIdentity('ООО "Руспан"', "699ПР-0925", "2025-09-25", "2236496,61")
    groups = group_document_duplicates(
        {"a": "sha-1", "b": "sha-2"}, {"a": identity, "b": identity}
    )
    assert groups["b"] == DuplicateOf("a", "document_identity")


def test_incomplete_identity_is_not_semantically_deduplicated():
    identity = DocumentIdentity('ООО "Руспан"', "", "", "")
    assert group_document_duplicates(
        {"a": "sha-1", "b": "sha-2"}, {"a": identity, "b": identity}
    ) == {}
```

- [ ] **Шаг 3: Запустить тесты и увидеть RED**

Run: `python -m pytest tests/test_archive_reconciliation.py -q`

Expected: FAIL, функции группировки отсутствуют.

- [ ] **Шаг 4: Реализовать группировку без отказа от SHA**

```python
@dataclass(frozen=True)
class DuplicateOf:
    canonical_file_id: str
    confidence: str


def group_document_duplicates(sha_by_file, identity_by_file):
    result: dict[str, DuplicateOf] = {}
    canonical_by_sha: dict[str, str] = {}
    canonical_by_identity: dict[DocumentIdentity, str] = {}
    for file_id in sha_by_file:
        sha = sha_by_file[file_id]
        if sha in canonical_by_sha:
            result[file_id] = DuplicateOf(canonical_by_sha[sha], "sha_exact")
            continue
        canonical_by_sha[sha] = file_id
        identity = identity_by_file.get(file_id)
        if identity and identity.complete() and identity in canonical_by_identity:
            result[file_id] = DuplicateOf(canonical_by_identity[identity], "document_identity")
            continue
        if identity and identity.complete():
            canonical_by_identity[identity] = file_id
    return result
```

- [ ] **Шаг 5: Исключить дубли из распределения подписей, но оставить их в отчёте**

В `match_signatures_by_sequence` передать `duplicate_of_by_file`. Для дубля вернуть отдельную уверенность `duplicate`, ссылку на основной файл и не добавлять его в авторскую очередь.

- [ ] **Шаг 6: Запустить тесты дублей и сопоставления**

Run: `python -m pytest tests/test_archive_reconciliation.py tests/test_max_message_matching.py -q`

Expected: PASS.

---

### Задача 4: Ввести строгую нормализацию и бизнес-правила полей

**Файлы:**
- Modify: `payment_processor/dictionaries.py`
- Modify: `payment_processor/invoice_archive.py`
- Modify: `payment_processor/archive_reconciliation.py`
- Modify: `dictionaries.json`
- Modify: `tests/test_invoice_archive.py`
- Modify: `tests/test_archive_reconciliation.py`

- [ ] **Шаг 1: Написать падающий тест строгого режима**

```python
def test_strict_normalization_does_not_return_unknown_source_value():
    result = normalize_value(
        "projects", "Кмд корректировка",
        {"unresolved_status": "Нужно разобрать", "projects": {"ПИР": ["кмд"]}},
        strict=True,
    )
    assert result.value == ""
    assert result.status == "Нужно разобрать"
    assert result.matched is False
```

- [ ] **Шаг 2: Запустить тест и увидеть RED**

Run: `python -m pytest tests/test_archive_reconciliation.py::test_strict_normalization_does_not_return_unknown_source_value -q`

Expected: FAIL, параметр `strict` отсутствует.

- [ ] **Шаг 3: Реализовать строгий режим без изменения старых вызовов**

```python
def normalize_value(category, value, dictionaries, reference_values=None, required=False, strict=False):
    # существующие точные проверки словаря и справочника сохраняются
    if matched_value is not None:
        return NormalizationResult(matched_value, "", original, True)
    status = unresolved_status if required or strict else ""
    return NormalizationResult("" if strict else original, status, original, False)
```

- [ ] **Шаг 4: Использовать `strict=True` для объекта, проекта, статьи и ответственного**

```python
object_result = normalize_value("objects", value, dictionaries, refs["Объект"], required=True, strict=True)
project_result = normalize_value("projects", value, dictionaries, refs["Проект"], strict=True)
budget_result = normalize_value("budget_items", value, dictionaries, refs["Статья бюджета"], strict=True)
responsible_result = normalize_value("responsibles", value, dictionaries, refs["Ответственный"], strict=True)
```

- [ ] **Шаг 5: Добавить тесты каждого согласованного преобразования**

```python
@pytest.mark.parametrize(
    ("source", "expected_project", "expected_budget", "purpose_part"),
    [
        ("КМ", "КМ ( М )", "", ""),
        ("КМ монтаж", "КМ ( М )", "", ""),
        ("КМ изготовление", "КМ ( ПР )", "", ""),
        ("АР (Цоколь)", "АР", "", ""),
        ("Кмд", "ПИР", "Подрядчик", "КМД"),
        ("мурсал", "", "", "мурсал"),
        ("Обеспечение ПР", "Производственные расходы", "", ""),
        ("обучение", "Офис", "", ""),
        ("пск фот", "ФОТ", "", ""),
        ("СРО. Стройка", "Офис", "", ""),
        ("станки", "Производственные расходы", "", ""),
        ("участок", "Инвестиции", "", ""),
        ("it обслуживание", "Офис", "", ""),
    ],
)
def test_project_business_rules(source, expected_project, expected_budget, purpose_part):
    result = normalize_signature_rules({"project": source}, load_dictionaries())
    assert result.get("project", "") == expected_project
    assert result.get("budget_item", "") == expected_budget
    assert purpose_part in result.get("purpose", "")
```

- [ ] **Шаг 6: Добавить явные псевдонимы объектов в `dictionaries.json`**

Добавить к существующим каноническим объектам варианты `Аларм Моторс Моторс`, `Владрусхолод / ЭСК`, `Егунян`, `Егунян 2`, `Егунян 2 Моторс`, `Бойков`, `Бойков Моторс`, `Ривербоатс 6`. Канонические значения брать только из актуального `Справочник.xlsx`.

- [ ] **Шаг 7: Исправить правило `Конвертация`**

```python
if project_key in conversion_keys or object_key in conversion_keys:
    details = [value for value in (object_name, project, budget_item, purpose) if value and _normalize_archive_key(value) not in conversion_keys]
    result.update({
        "object_name": "Конвертация",
        "project": "Конвертация",
        "budget_item": "",
        "purpose": ", ".join(dict.fromkeys(details)),
    })
```

- [ ] **Шаг 8: Запустить тесты нормализации**

Run: `python -m pytest tests/test_invoice_archive.py tests/test_archive_reconciliation.py -q`

Expected: PASS.

---

### Задача 5: Подставлять автора вместо отсутствующего ответственного и `ЭДО`

**Файлы:**
- Modify: `payment_processor/archive_reconciliation.py`
- Modify: `scripts/reconcile_google_archive_chat_fields.py`
- Modify: `tests/test_archive_reconciliation.py`

- [ ] **Шаг 1: Написать падающие тесты**

```python
@pytest.mark.parametrize("source", ["", "ЭДО"])
def test_responsible_falls_back_to_file_sender(source):
    updated = build_chat_updated_row(
        headers, row_with_responsible(source), signature_without_responsible(),
        "author_block", dictionaries, references,
        fallback_author="Николай Соловцов",
    )
    assert updated[headers.index("Ответственный")] == "Соловцов Н."
```

- [ ] **Шаг 2: Запустить тест и увидеть RED**

Run: `python -m pytest tests/test_archive_reconciliation.py::test_responsible_falls_back_to_file_sender -q`

Expected: FAIL, `fallback_author` отсутствует.

- [ ] **Шаг 3: Передать автора файла в решение строки**

Добавить `author` в данные кандидата/решения и использовать его, только если подтверждённая подпись не содержит корректного ответственного либо текущее значение равно `ЭДО`.

- [ ] **Шаг 4: Проверить строгий справочник ответственных**

Если автор не нормализуется по `Справочник.xlsx`, сохранить старую ячейку в плане и установить `Нужно разобрать`, не записывая имя как есть.

- [ ] **Шаг 5: Запустить тесты**

Run: `python -m pytest tests/test_archive_reconciliation.py tests/test_invoice_archive.py -q`

Expected: PASS.

---

### Задача 6: Исправить контрагента, сумму и резервное назначение из документа

**Файлы:**
- Modify: `payment_processor/invoice_archive.py`
- Modify: `tests/test_invoice_archive.py`
- Modify: `payment_processor/archive_reconciliation.py`
- Modify: `tests/test_archive_reconciliation.py`

- [ ] **Шаг 1: Написать тесты организационно-правовых форм и мусора**

```python
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Индивидуальный предприниматель Иванов Иван Иванович", "ИП Иванов Иван Иванович"),
        ('Общество с ограниченной ответственностью "Торговый дом"', 'ООО "Торговый дом"'),
    ],
)
def test_counterparty_abbreviates_legal_form(source, expected):
    assert clean_invoice_counterparty(source) == expected


@pytest.mark.parametrize("source", ["180 000,00 ₽ Без НДС", "получатель платежа", "вправе отказаться от заключения договора"])
def test_counterparty_rejects_non_names(source):
    assert clean_invoice_counterparty(source) == ""
```

- [ ] **Шаг 2: Написать тесты дополнительных форматов суммы**

```python
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("К\nоплате 2 236 496,61", "2236496,61"),
        ("Всего к оплате: 80 000.00", "80000,00"),
        ("Сумма 52 470", "52470"),
    ],
)
def test_extract_invoice_amount_from_total_labels(text, expected):
    assert extract_invoice_details_from_text(text)["amount"] == expected
```

- [ ] **Шаг 3: Написать тест безопасного числа без метки**

```python
def test_unlabeled_amount_requires_layout_evidence():
    assert extract_unlabeled_total("ИНН 7810930046 Счет 40702810000000000000") == ""
    assert extract_unlabeled_total("1 Товар\nИтого без подписи\n52 470,00", final_table_row=True) == "52470,00"
```

- [ ] **Шаг 4: Написать тест назначения из документа**

```python
def test_document_purpose_is_used_only_when_chat_purpose_missing():
    fields = extract_invoice_details_from_text("Назначение платежа: ремонт подъёмника\nИтого: 10 000")
    assert fields["purpose"] == "ремонт подъёмника"
```

- [ ] **Шаг 5: Запустить новые тесты и увидеть RED**

Run: `python -m pytest tests/test_invoice_archive.py -q`

Expected: FAIL на новых форматах и резервном назначении.

- [ ] **Шаг 6: Расширить очистку контрагента**

Перед валидацией заменить полные формы регулярными выражениями с границами слов, затем применить существующие проверки мусорных маркеров. Не сокращать слова внутри названия.

- [ ] **Шаг 7: Расширить `_extract_invoice_amount`**

Нормализовать переносы внутри меток и проверять метки в заданном приоритете. Неподписанный итог вынести в отдельную функцию, принимающую структурный признак последней строки таблицы; без признака возвращать пустую строку.

- [ ] **Шаг 8: Добавить `purpose` в документные поля**

Извлекать только текст после `Назначение платежа`, `Основание` или описания итоговой позиции до банковского/итогового раздела. В `build_chat_updated_row` применять его только при отсутствии назначения в подтверждённой подписи.

- [ ] **Шаг 9: Запустить тесты извлечения**

Run: `python -m pytest tests/test_invoice_archive.py tests/test_archive_reconciliation.py -q`

Expected: PASS.

---

### Задача 7: Проверять и восстанавливать Drive-ссылки без создания папок

**Файлы:**
- Modify: `payment_processor/google_archive.py`
- Modify: `payment_processor/google_api.py`
- Modify: `tests/test_google_archive.py`
- Modify: `scripts/reconcile_google_archive_chat_fields.py`

- [ ] **Шаг 1: Написать тест существующей и удалённой ссылки**

```python
def test_existing_drive_link_is_preserved():
    result = plan_drive_recovery(drive_with_file("id-1"), row(link="https://drive.google.com/file/d/id-1/view"), candidate())
    assert result.action == "preserve"


def test_deleted_link_uploads_to_existing_target_folder():
    drive = drive_without_file_with_existing_path()
    result = recover_drive_file(drive, deleted_row(), max_candidate_path(), dictionaries)
    assert result.action == "replace_link"
    assert drive.created_folders == []
```

- [ ] **Шаг 2: Написать тест существующей папки `Нужно разобрать`**

```python
def test_unknown_route_uses_existing_review_folder_without_creating_it():
    drive = drive_with_review_folder("review-id")
    result = recover_drive_file(drive, unresolved_row(), max_candidate_path(), dictionaries)
    assert result.folder_id == "review-id"
    assert drive.created_folders == []
```

- [ ] **Шаг 3: Написать тест отсутствующей папки разбора**

```python
def test_missing_review_folder_blocks_upload():
    drive = drive_without_review_folder()
    result = recover_drive_file(drive, unresolved_row(), max_candidate_path(), dictionaries)
    assert result.action == "blocked"
    assert drive.uploads == []
    assert drive.created_folders == []
```

- [ ] **Шаг 4: Запустить тесты и увидеть RED**

Run: `python -m pytest tests/test_google_archive.py -q`

Expected: FAIL, текущий код создаёт папку разбора через `ensure_child_folder_id`.

- [ ] **Шаг 5: Добавить read-only проверку ссылки**

```python
def drive_file_exists(drive_service, file_id: str) -> bool:
    try:
        meta = drive_service.files().get(fileId=file_id, fields="id,trashed", supportsAllDrives=True).execute()
    except HttpError as exc:
        if exc.resp.status == 404:
            return False
        raise
    return not bool(meta.get("trashed"))
```

- [ ] **Шаг 6: Удалить создание папок из архивного маршрута**

Заменить `ensure_child_folder_id` на `find_child_folder_id` для папки `Нужно разобрать`. Обычный маршрут уже ищет объект, год и месяц без создания; сохранить это поведение.

- [ ] **Шаг 7: Разделить планирование и загрузку**

Read-only аудит возвращает `DriveRecoveryPlan(action, old_link, source_file, folder_id, reason)` и не вызывает upload. Отдельная apply-функция принимает только уже проверенный план.

- [ ] **Шаг 8: Запустить тесты Drive**

Run: `python -m pytest tests/test_google_archive.py tests/test_google_api.py -q`

Expected: PASS и во всех тестах `created_folders == []`.

---

### Задача 8: Расширить read-only аудит на все исправляемые столбцы

**Файлы:**
- Modify: `scripts/reconcile_google_archive_chat_fields.py`
- Modify: `payment_processor/archive_reconciliation.py`
- Modify: `tests/test_archive_reconciliation.py`

- [ ] **Шаг 1: Расширить решение строки без немедленной записи**

```python
@dataclass(frozen=True)
class RowDecision:
    row_number: int
    old_row: list[str]
    new_row: list[str]
    content_confidence: str
    signature_confidence: str
    duplicate_of: str
    drive_action: str
    drive_target_folder_id: str
    reason: str
```

- [ ] **Шаг 2: Написать тест полного плана строки**

```python
def test_read_only_plan_contains_document_and_chat_changes_without_writes():
    decision = decide_row(fixture_row(), fixture_evidence(), fixture_document(), fixture_refs())
    assert changed_columns(decision) == {
        "Контрагент", "Объект", "Проект", "Статья бюджета",
        "Ответственный", "Назначение", "Сумма", "Google Drive ссылка", "Статус разбора",
    }
    assert fake_sheets.writes == []
    assert fake_drive.uploads == []
```

- [ ] **Шаг 3: Добавить стоп-условия аудита**

```python
def validate_plan(decisions, reference_sets):
    assert_no_unknown_regulated_values(decisions, reference_sets)
    assert_no_ambiguous_writes(decisions)
    assert_no_unjustified_clears(decisions)
    assert_no_folder_creation_actions(decisions)
```

- [ ] **Шаг 4: Версионировать новый формат плана**

Установить новую `POLICY_VERSION`, например `safe_archive_v3`, и запретить применение планов предыдущих версий.

- [ ] **Шаг 5: Добавить отдельные флаги `--audit` и `--apply-plan`**

По умолчанию и при `--audit` разрешены только чтение и создание локальных отчётов. `--apply-plan` требует путь к плану текущей версии и повторную проверку защищённых данных.

- [ ] **Шаг 6: Запустить тесты аудита**

Run: `python -m pytest tests/test_archive_reconciliation.py -q`

Expected: PASS.

---

### Задача 9: Полная проверка и подготовка контрольного запуска

**Файлы:**
- Verify: `tests/`
- Output: `reports/archive_safe_audit_<date>.csv`
- Output: `reports/archive_safe_summary_<date>.json`

- [ ] **Шаг 1: Запустить весь тестовый набор проекта**

Run: `python -m pytest -q`

Expected: все тесты PASS, 0 failures.

- [ ] **Шаг 2: Запустить read-only аудит исторического периода**

Run: `python scripts/reconcile_google_archive_chat_fields.py --start 2026-04-01 --end 2026-06-15 --audit`

Expected: созданы только локальные backup/plan/summary; Sheets writes = 0, Drive uploads = 0, folder creates = 0.

- [ ] **Шаг 3: Проверить известные проблемные строки**

Проверить в отчёте файлы `1557357629.193091834023531932.1.3.pdf`, `1557357629.193091834023531932.1.2.pdf` и `1557357629.193091834023531890.1.3.pdf`: два уникальных счёта получают одну подпись, смысловой дубль отмечен, но строка не удалена.

- [ ] **Шаг 4: Проверить сводные стоп-условия**

В summary должны присутствовать счётчики: неизвестные объекты/проекты/статьи/ответственные, неоднозначные подписи, смысловые дубли, пустые суммы, битые ссылки, целевые папки разбора и число предлагаемых изменений по каждому столбцу.

- [ ] **Шаг 5: Показать пользователю результаты до запуска**

Не выполнять `--apply-plan`. Сообщить пользователю результаты тестов и аудита, отдельно перечислить остаточные неоднозначности и запросить разрешение на контрольный запуск на небольшом новом диапазоне дат.


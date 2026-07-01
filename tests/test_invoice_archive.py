from pathlib import Path

def _ru(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")

import pytest
import payment_processor.invoice_archive as invoice_archive
from payment_processor.dictionaries import load_dictionaries
from payment_processor.invoice_archive import (
    InvoiceArchiveRecord,
    create_invoice_archive_records,
    enrich_invoice_records_from_files,
    enrich_payment_records_from_archive,
    extract_invoice_details_from_text,
    extract_invoice_details_from_filename,
    extract_message_text,
    extract_linked_message_text,
    invoice_text_operation_records_to_payment_records,
    mark_paid_records,
    parse_official_mochalov_payment,
    parse_max_signature,
)
from payment_processor.models import PaymentRecord
from payment_processor.max_api import FileCandidate


@pytest.mark.parametrize(
    ("source", "project", "budget_item", "purpose"),
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
def test_normalize_signature_applies_project_business_rules(
    source: str, project: str, budget_item: str, purpose: str
) -> None:
    result = invoice_archive.normalize_signature_rules({"project": source}, load_dictionaries())

    assert result.get("project", "") == project
    assert result.get("budget_item", "") == budget_item
    assert purpose in result.get("purpose", "")


def test_new_invoice_record_does_not_write_unknown_regulated_values() -> None:
    record = invoice_archive.create_invoice_archive_record(
        FileCandidate("invoice.pdf", "https://files/invoice.pdf", "file-1", "mid-1", ""),
        {
            "object_name": "неизвестный объект",
            "project": "неизвестный проект",
            "budget_item": "Какая?",
            "responsible": "ЭДО",
        },
        "ПСК",
        "chat",
        "Неизвестный автор",
        "2026-06-19 10:00:00",
        "mid-1",
        {"unresolved_status": "Нужно разобрать"},
        {
            "Объект": ["ПСК Ньютек"],
            "Проект": ["Офис"],
            "Статья бюджета": ["Расходники"],
            "Ответственный": ["Соловцов Н."],
        },
    )

    assert record.object_name == ""
    assert record.project == ""
    assert record.budget_item == ""
    assert record.responsible == ""
    assert record.analysis_status == "Нужно разобрать"

def test_normalize_signature_moves_auto_household_from_project_to_object() -> None:
    result = invoice_archive.normalize_signature_rules(
        {"object_name": "ПСК Ньютек", "project": "автохозяйство"},
        load_dictionaries(),
    )

    assert result["object_name"] == "Автохозяйство"
    assert result["project"] == ""

def test_normalize_signature_completes_conversion_rule() -> None:
    result = invoice_archive.normalize_signature_rules(
        {"object_name": "Конвертация", "project": "мурсал", "budget_item": "Материал"},
        load_dictionaries(),
    )

    assert result["object_name"] == "Конвертация"
    assert result["project"] == "Конвертация"
    assert result["budget_item"] == ""
    assert "мурсал" in result["purpose"]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Индивидуальный предприниматель Иванов Иван Иванович", "ИП Иванов Иван Иванович"),
        ('Общество с ограниченной ответственностью "Торговый дом"', 'ООО "Торговый дом"'),
    ],
)
def test_clean_invoice_party_abbreviates_legal_form(source: str, expected: str) -> None:
    assert invoice_archive._clean_invoice_party(source) == expected


def test_clean_invoice_party_removes_payment_metadata_tail() -> None:
    source = 'ООО "СДЭК-ГЛОБАЛ" Вид оп. 01 Срок плат. Наз. пл. Очер. плат. 5'

    assert invoice_archive._clean_invoice_party(source) == 'ООО "СДЭК-ГЛОБАЛ"'

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("К\nоплате 2 236 496,61", "2236496,61"),
        ("Всего к оплате: 80 000.00", "80000,00"),
        ("Сумма: 52 470", "52470"),
    ],
)
def test_extract_invoice_amount_supports_total_labels(text: str, expected: str) -> None:
    assert extract_invoice_details_from_text(text)["amount"] == expected


def test_extract_invoice_purpose_from_document() -> None:
    details = extract_invoice_details_from_text(
        "Назначение платежа: ремонт подъёмника\nИтого: 10 000,00"
    )

    assert details["purpose"] == "ремонт подъёмника"



def test_parse_max_signature_accepts_soft_sign_object_label() -> None:
    parsed = parse_max_signature("\u041e\u0431\u044c\u0435\u043a\u0442: \u043f\u0441\u043a \u0444\u043e\u0442\n\u041f\u0440\u043e\u0435\u043a\u0442: \u0416\u0438\u043b\u0438\u043d")

    assert parsed["object_name"] == "\u043f\u0441\u043a \u0444\u043e\u0442"
    assert parsed["project"] == "\u0416\u0438\u043b\u0438\u043d"


def test_normalize_signature_uses_project_encoded_in_object_name() -> None:
    result = invoice_archive.normalize_signature_rules(
        {"object_name": "\u043f\u0441\u043a \u0444\u043e\u0442", "project": "\u0416\u0438\u043b\u0438\u043d", "budget_item": "\u043b\u0438\u0437\u0438\u043d\u0433 \u0414\u0436\u0438\u043b\u0438 \u0410\u0442\u043b\u0430\u0441"},
        load_dictionaries(),
    )

    assert result["object_name"] == "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
    assert result["project"] == "\u0424\u041e\u0422"

def test_parse_max_signature_handles_labels_and_multiline_values() -> None:
    text = """
    Объект: пск.
    Проект: АР монтаж.
    Статья: Расходники
    Назначение: Расходники по счету
    Ответственный: Родин.
    Контрагент: Все инструменты
    продолжение названия
    """

    parsed = parse_max_signature(text)

    assert parsed["object_name"] == "пск"
    assert parsed["project"] == "АР монтаж"
    assert parsed["budget_item"] == "Расходники"
    assert parsed["purpose"] == "Расходники по счету"
    assert parsed["responsible"] == "Родин"
    assert parsed["counterparty"] == "Все инструменты продолжение названия"


def test_parse_max_signature_splits_multiple_labels_on_one_line() -> None:
    text = (
        "Объект: Лидерстрой Металлострой. Проект: АР. Статья: Расходники, дальномер. "
        "Ответственный: Документы Николай. Контрагент: Все инструменты. В долг"
    )

    parsed = parse_max_signature(text)

    assert parsed["object_name"] == "Лидерстрой Металлострой"
    assert parsed["project"] == "АР"
    assert parsed["budget_item"] == "Расходники, дальномер"
    assert parsed["responsible"] == "Документы Николай"
    assert parsed["counterparty"] == "Все инструменты. В долг"


def test_parse_max_signature_supports_dot_and_capitalized_space_separators() -> None:
    text = (
        "Объект: Риверботс 6\n"
        "Проект: КМ ПР. Статья. Расходники\n"
        "Назначение: ХВ Ответственный Документы Николай. Контрагент. Рестарт"
    )

    parsed = parse_max_signature(text)

    assert parsed["object_name"] == "Риверботс 6"
    assert parsed["project"] == "КМ ПР"
    assert parsed["budget_item"] == "Расходники"
    assert parsed["purpose"] == "ХВ"
    assert parsed["responsible"] == "Документы Николай"
    assert parsed["counterparty"] == "Рестарт"


def test_extract_message_text_keeps_body_and_linked_text_separate() -> None:
    message = {"body": {"text": "Объект: пск"}, "link": {"message": {"text": "Контрагент: Ромашка"}}}

    assert extract_message_text(message) == "Объект: пск"
    assert extract_linked_message_text(message) == "Контрагент: Ромашка"


def test_create_invoice_archive_records_normalizes_signature() -> None:
    message = {
        "timestamp": 1781025181546,
        "body": {
            "mid": "mid.1",
            "text": "Объект: пск\nПроект: офис\nСтатья: топливо\nОтветственный: родин\nКонтрагент: ООО Ромашка",
            "attachments": [
                {
                    "type": "file",
                    "filename": "invoice.pdf",
                    "url": "https://files.example/invoice.pdf",
                    "file_id": "file-1",
                }
            ],
        },
        "sender": {"name": "Николай"},
    }
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"ПСК Ньютек": ["пск"]},
        "projects": {"Офис": ["офис"]},
        "budget_items": {"Топливо": ["топливо"]},
        "responsibles": {"Родин.К": ["родин"]},
        "counterparties": {},
    }

    records = create_invoice_archive_records([message], "ПСК", "-1", dictionaries)

    assert len(records) == 1
    record = records[0]
    assert record.max_date.startswith("2026-06-09")
    assert record.author == "Николай"
    assert record.object_name == "ПСК Ньютек"
    assert record.project == "Офис"
    assert record.budget_item == "Топливо"
    assert record.responsible == "Родин.К"
    assert record.counterparty == ""
    assert record.status == ""
    assert record.max_message_id == "mid.1"
    assert record.max_file_id == "file-1"


def test_create_invoice_archive_records_links_previous_max_text_to_file() -> None:
    messages = [
        {
            "timestamp": 1781025000000,
            "body": {
                "mid": "mid.text",
                "text": "Объект: пск\nПроект: офис\nСтатья: топливо\nОтветственный: родин\nКонтрагент: ООО Ромашка",
            },
            "sender": {"name": "Светлана"},
        },
        {
            "timestamp": 1781025060000,
            "body": {
                "mid": "mid.file",
                "attachments": [
                    {
                        "type": "file",
                        "filename": "Счет №12048 от 20.05.2026.pdf",
                        "url": "https://files.example/invoice.pdf",
                        "file_id": "file-1",
                    }
                ],
            },
            "sender": {"name": "Светлана"},
        },
    ]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"ПСК Ньютек": ["пск"]},
        "projects": {"Офис": ["офис"]},
        "budget_items": {"Топливо": ["топливо"]},
        "responsibles": {"Родин.К": ["родин"]},
        "counterparties": {},
    }

    records = create_invoice_archive_records(messages, "ПСК", "-1", dictionaries)

    assert len(records) == 1
    record = records[0]
    assert record.object_name == "ПСК Ньютек"
    assert record.project == "Офис"
    assert record.budget_item == "Топливо"
    assert record.counterparty == ""
    assert record.invoice_number == ""
    assert record.invoice_date == ""
    assert record.status == ""


def test_create_invoice_archive_records_does_not_use_nearer_signature_from_another_author() -> None:
    messages = [
        {
            "timestamp": 1781025000000,
            "body": {
                "mid": "mid.file",
                "attachments": [{"type": "file", "filename": "invoice.pdf", "url": "https://files.example/invoice.pdf", "file_id": "file-1"}],
            },
            "sender": {"name": "\u041a\u0438\u0440\u0438\u043b\u043b"},
        },
        {
            "timestamp": 1781025010000,
            "body": {"mid": "mid.other-signature", "text": "\u041e\u0431\u044a\u0435\u043a\u0442: \u041f\u0421\u041a\n\u041f\u0440\u043e\u0435\u043a\u0442: \u041c\u043e\u043d\u0442\u0430\u0436\n\u041e\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0439: \u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430"},
            "sender": {"name": "Julia"},
        },
        {
            "timestamp": 1781025020000,
            "body": {"mid": "mid.own-signature", "text": "\u041e\u0431\u044a\u0435\u043a\u0442: \u0410\u043d\u0442\u0430\u0440\u0435\u0441\n\u041f\u0440\u043e\u0435\u043a\u0442: \u041a\u041c\n\u041e\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0439: \u041a\u0438\u0440\u0438\u043b\u043b"},
            "sender": {"name": "\u041a\u0438\u0440\u0438\u043b\u043b"},
        },
    ]
    dictionaries = {
        "unresolved_status": "\u041d\u0443\u0436\u043d\u043e \u0440\u0430\u0437\u043e\u0431\u0440\u0430\u0442\u044c",
        "objects": {"\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a": ["\u041f\u0421\u041a"], "\u0410\u043d\u0442\u0430\u0440\u0435\u0441": ["\u0410\u043d\u0442\u0430\u0440\u0435\u0441"]},
        "projects": {"\u041a\u041c ( \u041c )": ["\u041c\u043e\u043d\u0442\u0430\u0436", "\u041a\u041c"]},
        "responsibles": {"\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e.": ["\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430"], "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a": ["\u041a\u0438\u0440\u0438\u043b\u043b"]},
        "counterparties": {},
    }

    records = create_invoice_archive_records(messages, "\u041f\u0421\u041a", "-1", dictionaries)

    assert len(records) == 1
    assert records[0].object_name == "\u0410\u043d\u0442\u0430\u0440\u0435\u0441"
    assert records[0].responsible == "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a"


def test_create_invoice_archive_records_keeps_distinct_files_with_same_name() -> None:
    messages = [
        {
            "timestamp": 1781025038000,
            "body": {"mid": "mid.file.1", "attachments": [{"type": "file", "filename": "Счет №12048 от 20.05.2026.pdf", "url": "https://files.example/1.pdf", "file_id": "file-1"}]},
            "sender": {"name": "Светлана"},
        },
        {
            "timestamp": 1781025038000,
            "body": {"mid": "mid.text.1", "text": "Объект: ПР\nСтатья: Обеспечение ПР\nНазначение: Интернет\nОтветственный: Раздрогина\nКонтрагент: ТТС"},
            "sender": {"name": "Светлана"},
        },
        {
            "timestamp": 1781044219000,
            "body": {"mid": "mid.file.2", "text": "В оплату Ekaterina", "attachments": [{"type": "file", "filename": "Счет №12048 от 20.05.2026.pdf", "url": "https://files.example/2.pdf", "file_id": "file-2"}]},
            "sender": {"name": "Кирилл"},
        },
        {
            "timestamp": 1781044152000,
            "body": {"mid": "mid.file.3", "attachments": [{"type": "file", "filename": "createPaymentInvoice.pdf", "url": "https://files.example/3.pdf", "file_id": "file-3"}]},
            "sender": {"name": "Светлана"},
        },
    ]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"ПР": ["ПР"]},
        "projects": {"Производственные расходы": ["Производственные расходы"]},
        "budget_items": {"Аренда помещения": ["Аренда помещения"]},
        "budget_as_project": {"Обеспечение ПР": "Производственные расходы"},
        "purpose_budget_items": {"Интернет": "Аренда помещения"},
        "responsibles": {"Раздрогина.С": ["Раздрогина", "Светлана"]},
        "counterparties": {},
    }

    records = create_invoice_archive_records(messages, "ПСК", "-1", dictionaries)

    assert len(records) == 3
    by_file_id = {record.max_file_id: record for record in records}
    assert set(by_file_id) == {"file-1", "file-2", "file-3"}
    assert by_file_id["file-1"].project == "Производственные расходы"
    assert by_file_id["file-1"].budget_item == "Аренда помещения"
    assert by_file_id["file-1"].responsible == "Раздрогина.С"


def test_create_invoice_archive_records_deduplicates_repeated_image_url() -> None:
    messages = [
        {
            "timestamp": 1781025038000,
            "body": {"mid": "mid.file", "attachments": [{"type": "image", "filename": "i", "url": "https://images.example/same", "file_id": "image-token-1"}]},
        },
        {
            "timestamp": 1781025098000,
            "body": {"mid": "mid.signature", "text": "Объект: ПСК\nПроект: Офис", "attachments": [{"type": "image", "filename": "i", "url": "https://images.example/same", "file_id": "image-token-2"}]},
        },
    ]

    records = create_invoice_archive_records(
        messages,
        "ПСК",
        "-1",
        {"objects": {"ПСК Ньютек": ["ПСК"]}, "projects": {"Офис": ["Офис"]}},
    )

    assert len(records) == 1
    assert records[0].max_message_id == "mid.signature"
    assert records[0].object_name == "ПСК Ньютек"


def test_mark_paid_records_sets_paid_by_invoice_number() -> None:
    records = create_invoice_archive_records(
        [
            {
                "timestamp": 1781025038000,
                "body": {"mid": "mid.file.1", "text": "Объект: ПР\nКонтрагент: ТТС\nНомер счета: 12048", "attachments": [{"type": "file", "filename": "Счет №12048 от 20.05.2026.pdf", "url": "https://files.example/1.pdf", "file_id": "file-1"}]},
                "sender": {"name": "Светлана"},
            }
        ],
        "ПСК",
        "-1",
        {"unresolved_status": "Нужно разобрать", "objects": {"ПР": ["ПР"]}, "responsibles": {}, "counterparties": {}},
    )
    records[0].invoice_number = "12048"
    payment = PaymentRecord(
        name="Платежное поручение.pdf",
        date="2026-06-10",
        operation_type="Расход",
        payment_type="Безналичные с НДС",
        bank="б/н Сбербанк",
        counterparty='ООО "ТСС"',
        invoice_number="12048",
        object_name="",
        project="",
        budget_item="",
        responsible="",
        purpose="",
        invoice_link="",
        amount="7000",
    )

    mark_paid_records(records, [payment])

    assert records[0].payment_status == "Оплачен"
    assert records[0].operation_type == "Расход"
    assert records[0].payment_type == ""
    assert records[0].bank == ""
    assert records[0].amount == ""


def test_extract_invoice_details_from_text_reads_invoice_fields_from_invoice_body() -> None:
    details = extract_invoice_details_from_text(
        """
        Поставщик: ООО "Роп на связи", ИНН 1234567890
        Банк Точка
        Cчет №А1001023 от 31.03.2026
        Всего к оплате: 12 345,67
        """
    )

    assert details["counterparty"] == 'ООО "Роп на связи"'
    assert details["invoice_number"] == "А1001023"
    assert details["invoice_date"] == "2026-03-31"
    assert details.get("bank", "") == ""
    assert details["amount"] == "12345,67"


def test_extract_invoice_details_from_text_reads_ozon_amount() -> None:
    details = extract_invoice_details_from_text(
        """
        Счёт на оплату № 0225583270-0013 от 13.04.2026
        Итого: 5 224,00
        Всего к оплате с учетом НДС: 5 224,00
        Сумма к оплате: 5 224,00
        """
    )

    assert details["amount"] == "5224,00"


def test_extract_invoice_details_from_text_reads_megafon_prepayment_amount() -> None:
    details = extract_invoice_details_from_text(
        """
        Счёт на оплату № 133167607987 от 02.05.2026
        Наименование Сумма с НДС Сумма без НДС НДС
        Предоплата за услуги связи 16000,0013114,75 2885,25
        Итого 16000,0013114,75 2885,25
        """
    )

    assert details["amount"] == "16000,00"
    assert details["payment_type"] == "Безналичные с НДС"


def test_extract_invoice_details_from_text_handles_no_space_invoice_number_and_unknown_bank() -> None:
    details = extract_invoice_details_from_text(
        """
        Продавец Покупатель
        АЛФЕРУК НИКОЛАЙ СЕРГЕЕВИЧ Режим НО
        АО "ТБАНК"
        Счёт на оплату №28138226 от 8 апреля 2026 г.
        """
    )

    assert details["counterparty"] == "АЛФЕРУК НИКОЛАЙ СЕРГЕЕВИЧ"
    assert details["invoice_number"] == "28138226"
    assert details["invoice_date"] == "2026-04-08"
    assert details.get("bank", "") == ""


def test_extract_invoice_details_from_text_reads_common_number_date_variants() -> None:
    assert extract_invoice_details_from_text("№ 01-2065669837 от 13 апреля 2026 г.")["invoice_number"] == "01-2065669837"
    assert extract_invoice_details_from_text("Счёт N 0VT/3043142/63459237 от 24.03.2026") == {
        "invoice_number": "0VT/3043142/63459237",
        "invoice_date": "2026-03-24",
    }
    details = extract_invoice_details_from_text("Счёт на оплату № 01705202630891 услуг по публичной оферте от 15.04.2026")

    assert details["invoice_number"] == "01705202630891"
    assert details["invoice_date"] == "2026-04-15"


def test_extract_invoice_details_from_text_reads_two_digit_invoice_year() -> None:
    details = extract_invoice_details_from_text("Счет на оплату № 97 от 04.04.26 Без налога (НДС)")

    assert details["invoice_number"] == "97"
    assert details["invoice_date"] == "2026-04-04"
    assert details["payment_type"] == "Безналичные без НДС"


def test_extract_invoice_details_from_text_reads_colon_invoice_number() -> None:
    details = extract_invoice_details_from_text(
        'Поставщик: ООО "АВТОДОК-СЕТЬ", ИНН 1234567890\n'
        "Счет на оплату № SP-70013: 93 от 02.04.2026\n"
        "В том числе НДС: 123,45"
    )

    assert details["counterparty"] == 'ООО "АВТОДОК-СЕТЬ"'
    assert details["invoice_number"] == "SP-70013: 93"
    assert details["invoice_date"] == "2026-04-02"
    assert details["payment_type"] == "Безналичные с НДС"


def test_extract_invoice_details_from_text_reads_table_invoice_patterns() -> None:
    assert extract_invoice_details_from_text("СЧЕТ № 14190 21.04.2026") == {
        "invoice_number": "14190",
        "invoice_date": "2026-04-21",
    }
    assert extract_invoice_details_from_text("Счет № / Invoice: 42485/04/2026 Дата / Date: 01.04.26") == {
        "invoice_number": "42485/04/2026",
        "invoice_date": "2026-04-01",
    }
    assert extract_invoice_details_from_text("Оплата по счету на оплату 793BJ10214/1 от 24.04.2026 за товар") == {
        "invoice_number": "793BJ10214/1",
        "invoice_date": "2026-04-24",
    }
    assert extract_invoice_details_from_text("Счет на оплату № от 76888 18.05.2026 г.") == {
        "invoice_number": "76888",
        "invoice_date": "2026-05-18",
    }
    assert extract_invoice_details_from_text("СЧЕТ №16 от 25 мая 2026 года") == {
        "invoice_number": "16",
        "invoice_date": "2026-05-25",
    }
    assert extract_invoice_details_from_text("Счет № 1153274 Сетка сварная от производителя. от 20 Апреля 2026 г.") == {
        "invoice_number": "1153274",
        "invoice_date": "2026-04-20",
    }
    assert extract_invoice_details_from_text("Счет № 1169484 Сетка сварная от производителя. от21 Апреля 2026 г.") == {
        "invoice_number": "1169484",
        "invoice_date": "2026-04-21",
    }
    assert extract_invoice_details_from_text("Счет-договор №: V050624945 от 12 мая 2026 г.") == {
        "invoice_number": "V050624945",
        "invoice_date": "2026-05-12",
    }
    assert extract_invoice_details_from_text("счёт #сп15413 oT 21 Мая 2026 г.") == {
        "invoice_number": "сп15413",
        "invoice_date": "2026-05-21",
    }
    assert extract_invoice_details_from_text("Счет на оплату № 137 000-604283/7818 от 5 июня 2026 г.") == {
        "invoice_number": "137 000-604283/7818",
        "invoice_date": "2026-06-05",
    }
    assert extract_invoice_details_from_text("СЧЕТ № НК ТП-585245-1 от «21» мая 2026 г.") == {
        "invoice_number": "НК ТП-585245-1",
        "invoice_date": "2026-05-21",
    }


def test_extract_invoice_details_from_text_reads_rotated_invoice_total() -> None:
    details = extract_invoice_details_from_text(
        """
        Получатель ООО "ВЕКТОР"
        счет № 2234 от 14 апреля 2026 года
        В том числе НДС 22% 14 498,36
        Всего наименований 1, на сумму 80 400,00
        """
    )

    assert details["counterparty"] == 'ООО "ВЕКТОР"'
    assert details["payment_type"] == "Безналичные с НДС"
    assert details["amount"] == "80400,00"


def test_extract_invoice_details_from_text_reads_act_balance() -> None:
    details = extract_invoice_details_from_text(
        """
        Акт сверки взаимных расчетов № ЦБ-522 от 11 июня 2026 г.
        между ООО "Меркатор" и ООО "ПСК НЬЮТЕК"
        Сальдо конечное 367 452,00
        долг ООО "ПСК НЬЮТЕК" в валюте руб. 367 452,00
        """
    )

    assert details["invoice_number"] == "акт"
    assert details["invoice_date"] == "2026-06-11"
    assert details["counterparty"] == 'ООО "Меркатор"'
    assert details["amount"] == "367452,00"


def test_extract_invoice_details_from_text_reads_word_controls_and_duty_amount() -> None:
    lawyer = extract_invoice_details_from_text(
        "Получатель\x07Адвокат Евдокимов Алексей Анатольевич\x07СЧЕТ №15 от 21 мая 2026 года\x07Всего к оплате:\x0730 000\x07НДС не облагается"
    )
    duty = extract_invoice_details_from_text(
        "Реквизиты для оплаты государственной пошлины в 2025 году: 17 475 рублей За рассмотрение иска"
    )

    assert lawyer["counterparty"] == "Адвокат Евдокимов Алексей Анатольевич"
    assert lawyer["amount"] == "30000"
    assert duty["amount"] == "17475"


def test_extract_invoice_details_from_text_does_not_treat_bikmeev_as_bik() -> None:
    details = extract_invoice_details_from_text(
        "Исполнитель: ИП Бикмеев В.Ф., ИНН 025000102011, тел.: +7 (911) 905-27-70"
    )

    assert details["counterparty"] == "ИП Бикмеев В.Ф"


def test_extract_invoice_details_from_text_reads_duty_amount_after_kbk() -> None:
    details = extract_invoice_details_from_text(
        "Реквизиты для оплаты государственной пошлины в 2025 году: КБК 182 1 08 01000 01 1050 110 17 475 рублей За рассмотрение иска"
    )

    assert details["amount"] == "17475"


def test_extract_invoice_details_from_text_reads_ocr_negative_amount() -> None:
    details = extract_invoice_details_from_text(
        'ООО "ХЭДХАНТЕР" —8 5012 Пополнение лицевого счета. Счёт № 9401004/116 от 26.05.2026. В том числе НДС 22%'
    )

    assert details["amount"] == "8501"


def test_pdf_ocr_checks_rotations_when_initial_pass_has_only_date(monkeypatch, tmp_path) -> None:
    pdf_path = tmp_path / "rotated.pdf"
    document = invoice_archive.fitz.open()
    document.new_page()
    document.save(pdf_path)
    document.close()
    calls = []

    def fake_ocr(_images, rotations):
        calls.append(rotations)
        if rotations == (0,):
            return {"invoice_number": "2234", "invoice_date": "2026-04-14"}
        return {
            "counterparty": 'ООО "ВЕКТОР"',
            "invoice_number": "2234",
            "invoice_date": "2026-04-14",
            "amount": "80400,00",
            "payment_type": "Безналичные с НДС",
        }

    monkeypatch.setattr(invoice_archive, "_ocr_images_to_details", fake_ocr)

    details = invoice_archive._extract_invoice_details_from_pdf_ocr_at_scale(pdf_path, 1.0)

    assert calls == [(0,), (270, 180, 90)]
    assert details["amount"] == "80400,00"


def test_pdf_ocr_checks_fallback_scale_when_first_scale_is_incomplete(monkeypatch) -> None:
    calls = []

    def fake_scale(_path, scale):
        calls.append(scale)
        if scale == 2.5:
            return {"invoice_number": "2234", "invoice_date": "2026-04-14", "amount": "80400,00"}
        return {
            "counterparty": 'ООО "ВЕКТОР"',
            "invoice_number": "2234",
            "invoice_date": "2026-04-14",
            "amount": "80400,00",
            "payment_type": "Безналичные с НДС",
        }

    monkeypatch.setattr(invoice_archive, "_extract_invoice_details_from_pdf_ocr_at_scale", fake_scale)

    details = invoice_archive.extract_invoice_details_from_pdf_ocr(Path("invoice.pdf"))

    assert calls == [2.5, 2.0]
    assert details["payment_type"] == "Безналичные с НДС"


def test_extract_invoice_details_from_file_reads_webp_without_extension(monkeypatch, tmp_path) -> None:
    image_path = tmp_path / "i"
    invoice_archive.Image.new("RGB", (20, 20), "white").save(image_path, format="WEBP")
    monkeypatch.setattr(
        invoice_archive.pytesseract,
        "image_to_string",
        lambda *_args, **_kwargs: 'Получатель ООО "ХЭДХАНТЕР" Счёт № 9401004/116 от 26.05.2026 -8 501 ₽ В том числе НДС 22%',
    )

    details = invoice_archive.extract_invoice_details_from_file(image_path)

    assert details["counterparty"] == 'ООО "ХЭДХАНТЕР"'
    assert details["invoice_number"] == "9401004/116"
    assert details["invoice_date"] == "2026-05-26"
    assert details["payment_type"] == "Безналичные с НДС"
    assert details["amount"] == "8501"


def test_rtf_to_plain_text_decodes_unicode_controls() -> None:
    text = invoice_archive._rtf_to_plain_text(r"{\rtf1 \u1057\'3f\u1095\'3f\u1077\'3f\u1090\'3f №16 от 25 мая 2026 года}")

    assert "Счет №16 от 25 мая 2026 года" in text


def test_merge_ocr_details_does_not_copy_counterparty() -> None:
    merged = invoice_archive._merge_ocr_details(
        {"payment_type": "Безналичные с НДС"},
        {"counterparty": "распознанный мусор", "invoice_number": "3706", "invoice_date": "2026-04-01", "payment_type": "Безналичные без НДС"},
    )

    assert merged == {
        "payment_type": "Безналичные с НДС",
        "invoice_number": "3706",
        "invoice_date": "2026-04-01",
    }


def test_merge_ocr_details_copies_confident_legal_counterparty() -> None:
    merged = invoice_archive._merge_ocr_details(
        {},
        {"counterparty": 'ООО "НЭТСТОР"', "invoice_number": "Ф9-0003706/У", "invoice_date": "2026-04-01"},
    )

    assert merged["counterparty"] == 'ООО "НЭТСТОР"'


def test_merge_ocr_details_copies_ip_with_initials() -> None:
    merged = invoice_archive._merge_ocr_details(
        {},
        {"counterparty": "ИП Бикмеев В.Ф", "invoice_number": "PLm0010725", "invoice_date": "2026-03-16"},
    )

    assert merged["counterparty"] == "ИП Бикмеев В.Ф"


def test_enrich_invoice_records_from_files_reads_xls_details(monkeypatch, tmp_path) -> None:
    record = create_invoice_archive_records(
        [
            {
                    "body": {"mid": "mid.file", "text": "Объект: ПР", "attachments": [{"type": "file", "filename": "invoice.xls", "url": "https://files.example/invoice.xls"}]},
            }
        ],
        "ПСК",
        "-1",
        {"unresolved_status": "Нужно разобрать", "objects": {"ПР": ["ПР"]}, "responsibles": {}, "counterparties": {}},
    )[0]
    file_path = tmp_path / "invoice.xls"
    file_path.write_bytes(b"fake xls")

    monkeypatch.setattr(
        invoice_archive,
        "extract_invoice_details_from_xls",
        lambda _path: {"invoice_number": "SP-70013: 93", "invoice_date": "2026-04-02", "payment_type": "Безналичные с НДС"},
    )

    enrich_invoice_records_from_files([record], {"invoice.xls": file_path})

    assert record.invoice_number == "SP-70013: 93"
    assert record.invoice_date == "2026-04-02"
    assert record.payment_type == "Безналичные с НДС"


def test_file_archive_record_does_not_use_chat_counterparty_or_bank(tmp_path) -> None:
    messages = [
        {
                "body": {"mid": "mid.file", "text": "Объект: ПР\nКонтрагент: Автодок\nБанк: б/н Альфа\nНомер счета: 123\nДата счета: 01.04.2026", "attachments": [{"type": "file", "filename": "invoice.xls", "url": "https://files.example/invoice.xls"}]},
        }
    ]
    records = create_invoice_archive_records(
        messages,
        "ПСК",
        "-1",
        {"unresolved_status": "Нужно разобрать", "objects": {"ПР": ["ПР"]}, "responsibles": {}, "counterparties": {}},
    )
    file_path = tmp_path / "invoice.xls"
    file_path.write_bytes(b"not a pdf")

    enrich_invoice_records_from_files(records, {"invoice.xls": file_path})

    assert records[0].counterparty == ""
    assert records[0].bank == ""
    assert records[0].invoice_number == ""
    assert records[0].invoice_date == ""


def test_clean_invoice_party_removes_executor_prefix() -> None:
    details = extract_invoice_details_from_text('Поставщик: (Исполнитель): ИП Самыгин Алексей, ИНН 123456789012')

    assert details["counterparty"] == "ИП Самыгин Алексей"


def test_clean_invoice_party_removes_bank_tail_and_skips_own_company() -> None:
    details = extract_invoice_details_from_text(
        'Продавец Покупатель НИКОЛАЕВА ВАСИЛИСА КОНСТАНТИНОВНАРежим НО: НПДИНН 782510150582СЕВЕРО-ЗАПАДНЫЙ БАНК'
    )

    assert details["counterparty"] == "НИКОЛАЕВА ВАСИЛИСА КОНСТАНТИНОВНА"
    assert "counterparty" not in extract_invoice_details_from_text('Покупатель: ООО "ПСК НЬЮТЕК", ИНН 7810930046')


def test_clean_invoice_party_removes_address_after_quoted_name() -> None:
    details = extract_invoice_details_from_text(
        'Поставщик: АС "ПОМОЩЬ". 123376, Москва г, Красная Пресня ул, дом 28 Покупатель: ООО "ПСК НЬЮТЕК"'
    )

    assert details["counterparty"] == 'АС "ПОМОЩЬ"'


def test_extract_invoice_counterparty_from_bank_recipient_when_supplier_is_own_company() -> None:
    details = extract_invoice_details_from_text(
        """
        Получатель Банк получателя ИНН 532204361675 Сч. №
        ИП ЛЕБЕДЕВА ВЕРА ЕВГЕНЬЕВНА Сч. № 044525974
        Счет на оплату № 131 от 30 марта 2026 г.
        Поставщик: ООО "ПСК НЬЮТЕК", ИНН 7810930046
        НДС 22% 6 852,46
        """
    )

    assert details["counterparty"] == "ИП ЛЕБЕДЕВА ВЕРА ЕВГЕНЬЕВНА"
    assert details["payment_type"] == "Безналичные с НДС"


def test_extract_invoice_counterparty_from_top_legal_entity() -> None:
    details = extract_invoice_details_from_text(
        """
        ООО "Бегет" Юридический адрес: Санкт-Петербург
        ИНН/КПП: 7801451618/780601001
        ООО "ПСК НЬЮТЕК"
        Счет №11140425 от 19.03.2026
        В том числе НДС (22%) 4208.85
        """
    )

    assert details["counterparty"] == 'ООО "Бегет"'
    assert details["payment_type"] == "Безналичные с НДС"


def test_extract_invoice_counterparty_normalizes_latin_ooo_prefix() -> None:
    details = extract_invoice_details_from_text(
        """
        OOO "Бегет" Юридический адрес: Санкт-Петербург
        ООО "ПСК НЬЮТЕК"
        Счет №11140425 от 19.03.2026
        """
    )

    assert details["counterparty"] == 'ООО "Бегет"'


def test_extract_invoice_counterparty_from_regular_recipient_label() -> None:
    details = extract_invoice_details_from_text(
        """
        Счет №26-12571375016 от 04.05.2026 г.
        Получатель: ООО "Деловые Линии" ИНН 7826156685 КПП 997650001
        Плательщик: ООО "ПСК НЬЮТЕК"
        В том числе НДС 234,60
        """
    )

    assert details["counterparty"] == 'ООО "Деловые Линии"'
    assert details["payment_type"] == "Безналичные с НДС"


def test_extract_invoice_payment_type_from_npd_mode() -> None:
    details = extract_invoice_details_from_text(
        """
        Продавец Покупатель НИКОЛАЕВА ВАСИЛИСА КОНСТАНТИНОВНА Режим НО: НПД
        ИНН 782510150582
        Счёт на оплату №27995514 от 4 апреля 2026 г.
        """
    )

    assert details["counterparty"] == "НИКОЛАЕВА ВАСИЛИСА КОНСТАНТИНОВНА"
    assert details["payment_type"] == "Безналичные без НДС"


def test_clean_invoice_counterparty_trims_ip_address_and_leading_inn_kpp() -> None:
    ip_details = extract_invoice_details_from_text(
        "Исполнитель ИП ЛЕБЕДЕВА ВЕРА ЕВГЕНЬЕВНА, улица улица, д. 40 Заказчик ООО ПСК НЬЮТЕК"
    )
    company_details = extract_invoice_details_from_text(
        'Получатель 7736207543/997750001 ООО "ЯНДЕКС" Сч. № 40702810000000000000'
    )

    assert ip_details["counterparty"] == "ИП ЛЕБЕДЕВА ВЕРА ЕВГЕНЬЕВНА"
    assert company_details["counterparty"] == 'ООО "ЯНДЕКС"'


def test_extract_invoice_counterparty_ignores_payment_purpose_as_counterparty() -> None:
    details = extract_invoice_details_from_text(
        "Получатель Назначение платежа: Целевой членский взнос по счету №3636 от 05.05.26, НДС не обл Сч. № 40702810000000000000"
    )

    assert "counterparty" not in details


def test_clean_invoice_counterparty_extracts_legal_entity_from_ocr_prefix() -> None:
    details = extract_invoice_details_from_text(
        'Счет № Ф9-0003706/У от 1 апреля 2026 г. Поставщик OOO "НЭТСТОР" ИНН 1234567890'
    )

    assert details["counterparty"] == 'ООО "НЭТСТОР"'


def test_clean_invoice_counterparty_rejects_long_contract_clause() -> None:
    details = extract_invoice_details_from_text(
        "Получатель вправе в одностороннем порядке увеличить цену Товара, уведомив об этом Покупателя, и потребовать доплаты пропорционально изменению курса рубль доллар США. В этом случае Сч. № 40702810000000000000"
    )

    assert "counterparty" not in details


def test_create_invoice_archive_records_uses_author_as_default_responsible() -> None:
    message = {
        "timestamp": 1781025060000,
        "body": {
            "mid": "mid.file",
            "text": "Объект: пск\nПроект: офис\nСтатья: топливо\nКонтрагент: ООО Ромашка",
            "attachments": [
                {
                    "type": "file",
                    "filename": "invoice.pdf",
                    "url": "https://files.example/invoice.pdf",
                    "file_id": "file-1",
                }
            ],
        },
        "sender": {"name": "Светлана"},
    }
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"ПСК Ньютек": ["пск"]},
        "projects": {"Офис": ["офис"]},
        "budget_items": {"Топливо": ["топливо"]},
        "responsibles": {"Раздрогина.С": ["светлана"]},
        "counterparties": {},
    }

    records = create_invoice_archive_records([message], "ПСК", "-1", dictionaries)

    assert records[0].responsible == "Раздрогина.С"


def test_signature_rules_normalize_production() -> None:
    message = {
        "timestamp": 1781025060000,
        "body": {
            "mid": "mid.file",
            "text": "Объект: Нужды ПР\nПроект: Производственные расходы\nСтатья: Родин\nНазначение: Газы\nОтветственный: Документы Николай\nКонтрагент: Старт",
            "attachments": [{"type": "file", "filename": "invoice.pdf", "url": "https://files.example/invoice.pdf", "file_id": "file-1"}],
        },
        "sender": {"name": "Николай Соловцов"},
    }
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"Производство": ["ПР", "Нужды ПР"]},
        "projects": {"Производственные расходы": ["Производственные расходы"]},
        "budget_items": {"Родин К.": ["Родин"]},
        "responsibles": {"Соловцов Н.": ["Документы Николай", "Николай Соловцов"]},
        "counterparties": {},
    }

    records = create_invoice_archive_records([message], "ПСК", "-1", dictionaries)

    assert records[0].object_name == "Производство"
    assert records[0].project == "Производственные расходы"
    assert records[0].budget_item == "Родин К."
    assert records[0].responsible == "Соловцов Н."


def test_signature_rules_skip_budget_item_for_conversion_only() -> None:
    message = {
        "timestamp": 1781025060000,
        "body": {
            "mid": "mid.file",
            "text": "Объект: ПСК\nПроект: Конвертация\nСтатья: Родин\nНазначение: пила\nОтветственный: Родин\nКонтрагент: бм",
            "attachments": [{"type": "file", "filename": "invoice.pdf", "url": "https://files.example/invoice.pdf", "file_id": "file-1"}],
        },
        "sender": {"name": "Родин"},
    }
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"ПСК Ньютек": ["ПСК"], "Конвертация": ["Конвертация"]},
        "projects": {"Конвертация": ["Конвертация"]},
        "budget_items": {"Родин К.": ["Родин"]},
        "conversion_values": ["Конвертация"],
        "responsibles": {"Родин.К": ["Родин"]},
        "counterparties": {},
    }

    records = create_invoice_archive_records([message], "ПСК", "-1", dictionaries)

    assert records[0].object_name == "Конвертация"
    assert records[0].project == "Конвертация"
    assert records[0].budget_item == ""
    assert records[0].responsible == "Родин.К"


def test_extract_invoice_details_from_filename_supports_numeric_and_text_dates() -> None:
    assert extract_invoice_details_from_filename("Счет №12048 от 20.05.2026.pdf") == {
        "invoice_number": "12048",
        "invoice_date": "2026-05-20",
    }
    assert extract_invoice_details_from_filename("Счет на оплату № 1552 от 9 июня 2026.pdf") == {
        "invoice_number": "1552",
        "invoice_date": "2026-06-09",
    }


def test_create_invoice_archive_records_marks_missing_object() -> None:
    message = {
        "body": {"mid": "mid.2", "text": "Назначение: проверить счет", "attachments": [{"type": "file", "filename": "invoice.xlsx", "url": "https://files.example/invoice.xlsx"}]},
    }
    dictionaries = {"unresolved_status": "Нужно разобрать", "objects": {}}

    records = create_invoice_archive_records([message], "ПСК", "-1", dictionaries)

    assert records[0].status == "Нужно разобрать"


def test_parse_official_mochalov_payment_creates_salary_and_tax_items() -> None:
    parsed = parse_official_mochalov_payment("ИП Мочалов\nзп - 20 880,00\nналоги - 18 144,00")

    assert parsed == [
        {"project": "ФОТ", "budget_item": "Официальная ЗП", "purpose": "Официальная ЗП", "amount": "20880,00"},
        {"project": "Налоги", "budget_item": "Налоги НДФЛ", "purpose": "Налоги НДФЛ", "amount": "18144,00"},
    ]


def test_parse_official_payment_reads_short_tax_lines() -> None:
    parsed = parse_official_mochalov_payment(
        _ru(r"\u041d\u0414\u0421,\u041d\u041f \u0418\u043d\u0432\u0435\u0441\u0442 - 32274,00\n\u0423\u0421\u041d \u0418\u041f \u0420\u043e\u0434\u0438\u043d - 39419,00")
    )

    assert parsed == [
        {
            "object_name": _ru(r"\u041f\u0421\u041a \u0418\u043d\u0432\u0435\u0441\u0442"),
            "project": _ru(r"\u041d\u0430\u043b\u043e\u0433\u0438"),
            "budget_item": _ru(r"\u041d\u0430\u043b\u043e\u0433\u0438 \u041d\u0414\u0421"),
            "purpose": _ru(r"\u041d\u0414\u0421,\u041d\u041f \u0418\u043d\u0432\u0435\u0441\u0442"),
            "amount": "32274,00",
            "counterparty": _ru(r"\u041d\u0430\u043b\u043e\u0433\u0438"),
            "bank": _ru(r"\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a"),
        },
        {
            "object_name": _ru(r"\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"),
            "project": _ru(r"\u041d\u0430\u043b\u043e\u0433\u0438"),
            "budget_item": _ru(r"\u041d\u0430\u043b\u043e\u0433\u0438 \u041d\u0414\u0424\u041b"),
            "purpose": _ru(r"\u0423\u0421\u041d \u0418\u041f \u0420\u043e\u0434\u0438\u043d"),
            "amount": "39419,00",
            "counterparty": _ru(r"\u0418\u041f \u0420\u043e\u0434\u0438\u043d"),
            "bank": _ru(r"\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a"),
        },
    ]


def test_create_invoice_archive_records_uses_tax_label_for_vat_and_bank() -> None:
    message = {
        "timestamp": 1781025181546,
        "body": {
            "mid": "mid.tax",
            "text": _ru(
                r"\u043d\u0430\u043b\u043e\u0433\u0438 \u041f\u0421\u041a - \u041d\u0414\u0421, \u041d\u041f - 399 502,02\n"
                r"\u043d\u0430\u043b\u043e\u0433\u0438 \u0418\u041f \u041c\u043e\u0447\u0430\u043b\u043e\u0432 - \u041d\u0414\u0421, 1%, \u0444\u0438\u043a\u0441 - 579 059,78"
            ),
        },
        "sender": {"name": _ru(r"\u041a\u0438\u0440\u0438\u043b\u043b \u041c\u043e\u0447\u0430\u043b\u043e\u0432")},
    }
    dictionaries = {
        "unresolved_status": _ru(r"\u041d\u0443\u0436\u043d\u043e \u0440\u0430\u0437\u043e\u0431\u0440\u0430\u0442\u044c"),
        "objects": {_ru(r"\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"): [_ru(r"\u043f\u0441\u043a")]},
        "projects": {_ru(r"\u041d\u0430\u043b\u043e\u0433\u0438"): [_ru(r"\u043d\u0430\u043b\u043e\u0433\u0438")]},
        "budget_items": {
            _ru(r"\u041d\u0430\u043b\u043e\u0433\u0438 \u041d\u0414\u0421"): [_ru(r"\u043d\u0430\u043b\u043e\u0433\u0438 \u043d\u0434\u0441")],
            _ru(r"\u041d\u0430\u043b\u043e\u0433\u0438 \u041d\u0414\u0424\u041b"): [_ru(r"\u043d\u0430\u043b\u043e\u0433\u0438 \u043d\u0434\u0444\u043b")],
        },
        "responsibles": {_ru(r"\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a"): [_ru(r"\u043a\u0438\u0440\u0438\u043b\u043b \u043c\u043e\u0447\u0430\u043b\u043e\u0432")]},
        "counterparties": {_ru(r"\u0418\u041f \u041c\u043e\u0447\u0430\u043b\u043e\u0432"): [_ru(r"\u0438\u043f \u043c\u043e\u0447\u0430\u043b\u043e\u0432")], _ru(r"\u041d\u0430\u043b\u043e\u0433\u0438"): [_ru(r"\u043d\u0430\u043b\u043e\u0433\u0438")]},
    }

    records = create_invoice_archive_records([message], "???", "-1", dictionaries)

    assert [record.bank for record in records] == [_ru(r"\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a"), _ru(r"\u0431/\u043d \u0418\u041f \u041c\u043e\u0447\u0430\u043b\u043e\u0432")]
    assert [record.budget_item for record in records] == [_ru(r"\u041d\u0430\u043b\u043e\u0433\u0438 \u041d\u0414\u0421"), _ru(r"\u041d\u0430\u043b\u043e\u0433\u0438 \u041d\u0414\u0421")]
    assert [record.purpose for record in records] == [_ru(r"\u043d\u0430\u043b\u043e\u0433\u0438 \u041f\u0421\u041a - \u041d\u0414\u0421, \u041d\u041f"), _ru(r"\u043d\u0430\u043b\u043e\u0433\u0438 \u0418\u041f \u041c\u043e\u0447\u0430\u043b\u043e\u0432 - \u041d\u0414\u0421, 1%, \u0444\u0438\u043a\u0441")]

def test_create_invoice_archive_records_includes_official_payment_without_file() -> None:
    message = {
        "timestamp": 1781025181546,
        "body": {"mid": "mid.3", "text": "ИП Мочалов\nзп - 20 880,00\nналоги - 18 144,00"},
        "sender": {"name": "Катя Ионова"},
    }
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"ПСК Ньютек": ["пск"]},
        "projects": {"ФОТ": ["фот"], "Налоги": ["налоги"]},
        "budget_items": {"Официальная ЗП": ["зп"], "Налоги НДФЛ": ["налоги"]},
        "responsibles": {"Мочалов К.": ["мочалов к"]},
        "counterparties": {},
    }

    records = create_invoice_archive_records([message], "ПСК", "-1", dictionaries)

    assert len(records) == 2
    assert [record.file_type for record in records] == ["сообщение", "сообщение"]
    assert [record.counterparty for record in records] == ["ИП Мочалов", "ИП Мочалов"]
    assert [record.operation_type for record in records] == ["Расход", "Расход"]
    assert [record.payment_type for record in records] == ["Безналичные без НДС", "Безналичные без НДС"]
    assert [record.bank for record in records] == ["б/н ИП Мочалов", "б/н ИП Мочалов"]
    assert [record.invoice_number for record in records] == ["б/сч", "б/сч"]
    assert [record.object_name for record in records] == ["ПСК Ньютек", "ПСК Ньютек"]
    assert [record.project for record in records] == ["ФОТ", "Налоги"]
    assert [record.budget_item for record in records] == ["Официальная ЗП", "Налоги НДФЛ"]
    assert [record.responsible for record in records] == ["Мочалов К.", "Мочалов К."]
    assert [record.invoice_date for record in records] == ["2026-06-09", "2026-06-09"]
    assert [record.amount for record in records] == ["20880,00", "18144,00"]


def test_create_invoice_archive_records_includes_grouped_official_payments() -> None:
    message = {
        "timestamp": 1781025181546,
        "body": {
            "mid": "mid.4",
            "text": "пск\nзп - 114 840,00\nналоги - 108 403,93\n\nпск инвест\nзп - 13 050,00\nналоги - 19 468,00",
        },
        "sender": {"name": "Катя Ионова"},
    }
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"ПСК Ньютек": ["пск"], "ПСК Инвест": ["пск инвест"]},
        "projects": {"ФОТ": ["фот"], "Налоги": ["налоги"]},
        "budget_items": {"Официальная ЗП": ["зп"], "Налоги НДФЛ": ["налоги"]},
        "responsibles": {"Мочалов К.": ["мочалов к"]},
        "counterparties": {},
    }

    records = create_invoice_archive_records([message], "ПСК", "-1", dictionaries)

    assert len(records) == 4
    assert [record.object_name for record in records] == ["ПСК Ньютек", "ПСК Ньютек", "ПСК Инвест", "ПСК Инвест"]
    assert [record.project for record in records] == ["ФОТ", "Налоги", "ФОТ", "Налоги"]
    assert [record.budget_item for record in records] == ["Официальная ЗП", "Налоги НДФЛ", "Официальная ЗП", "Налоги НДФЛ"]
    assert [record.amount for record in records] == ["114840,00", "108403,93", "13050,00", "19468,00"]

    payment_records = invoice_text_operation_records_to_payment_records(records)

    assert len(payment_records) == 4
    assert [record.name for record in payment_records] == ["mid.4", "mid.4", "mid.4", "mid.4"]
    assert [record.payment_type for record in payment_records] == ["Безналичные без НДС"] * 4


def test_enrich_payments_from_archive_matches_invoice_number_and_counterparty() -> None:
    payment = PaymentRecord(
        "payment.pdf", "2026-06-20", "Расход", "Безналичные без НДС", "б/н Альфа",
        "Индивидуальный предприниматель Соколов Виктор Сергеевич", "№ 291",
        "", "", "", "", "Банковское назначение", "", "1000",
    )
    archive = InvoiceArchiveRecord(
        "2026-06-19 12:00:00", "ПСК", "-1", "Николай", "invoice.pdf", "pdf",
        "Расход", "Безналичные без НДС", "", "ИП Соколов Виктор Сергеевич", "291",
        "2026-06-18", "ПСК Ньютек", "Офис", "Обеспечение офиса", "Соловцов Н.",
        "Аренда автовышки", "1000", "Новый", "https://drive.google.com/file/d/invoice",
        "mid.1", "file.1", "ОК",
    )

    matched = enrich_payment_records_from_archive([payment], [archive])

    assert matched == 1
    assert payment.object_name == "ПСК Ньютек"
    assert payment.project == "Офис"
    assert payment.budget_item == "Обеспечение офиса"
    assert payment.responsible == "Соловцов Н."
    assert payment.purpose == "Банковское назначение"
    assert payment.invoice_link == "https://drive.google.com/file/d/invoice"



def test_enrich_payments_from_archive_matches_petrovich_abbreviation() -> None:
    payment = PaymentRecord(
        "Платежное_поручение_№50.pdf", "2026-06-18", "Расход", "Безналичные с НДС", "б/н Альфа",
        'ООО "СТД "ПЕТРОВИЧ"', "ТШЭ00346414",
        "", "", "", "", "Арматура", "", "103306,78",
    )
    archive = InvoiceArchiveRecord(
        "2026-06-17 16:58:15", "ИС", "-1", "Кирилл Родин", "1557357629.193373309000243294.1.2.pdf", "pdf",
        "Расход", "Безналичные с НДС", "", 'ООО "Строительный Торговый Дом "Петрович"', "ТШЭ00346414",
        "2026-06-17", "ПСК ИС", "Конвертация", "Подрядчик", "Родин.К",
        "стройматериал", "103306,78", "Новый", "https://drive.google.com/file/d/petrovich",
        "mid.file", "file.3937299194", "ОК",
    )

    matched = enrich_payment_records_from_archive([payment], [archive])

    assert matched == 1
    assert payment.object_name == "ПСК ИС"
    assert payment.project == "Конвертация"
    assert payment.budget_item == "Подрядчик"
    assert payment.responsible == "Родин.К"
    assert payment.purpose == "Арматура"
    assert payment.invoice_link == "https://drive.google.com/file/d/petrovich"


def test_enrich_payments_from_archive_ignores_branch_prefix_in_counterparty() -> None:
    payment = PaymentRecord(
        "payment.pdf", "2026-05-28", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0418\u041d\u0412\u0415\u0421\u0422\u0421\u0422\u0420\u041e\u0419",
        "\u0424\u0418\u041b\u0418\u0410\u041b \u041e\u041e\u041e \"\u0425\u042d\u0414\u0425\u0410\u041d\u0422\u0415\u0420\"", "12814587/2", "", "", "", "", "\u041f\u043e\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u0435", "", "8501",
    )
    archive = InvoiceArchiveRecord(
        "", "\u0418\u0421", "-1", "", "bill_28708631.pdf", "pdf", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "",
        "\u041e\u041e\u041e \"\u0425\u044d\u0434\u0445\u0430\u043d\u0442\u0435\u0440\"", "12814587/2", "2026-05-28", "\u041f\u0421\u041a \u0418\u0421", "\u041e\u0444\u0438\u0441", "\u041d\u0430\u0439\u043c", "\u0420\u0430\u0437\u0434\u0440\u043e\u0433\u0438\u043d\u0430.\u0421",
        "\u043f\u043e\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u0435 \u0441\u0447\u0435\u0442\u0430 \u041d\u041d", "8501,00", "\u041d\u043e\u0432\u044b\u0439", "https://drive/invoice", "mid", "file", "\u041e\u041a",
    )

    matched = enrich_payment_records_from_archive([payment], [archive])

    assert matched == 1
    assert payment.project == "\u041e\u0444\u0438\u0441"
    assert payment.invoice_link == "https://drive/invoice"


def test_enrich_payments_from_archive_uses_unique_invoice_number_when_counterparty_is_missing_or_noisy() -> None:
    payment = PaymentRecord(
        "payment.pdf", "2026-06-18", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0418\u041d\u0412\u0415\u0421\u0422\u0421\u0422\u0420\u041e\u0419",
        "\u041e\u041e\u041e \"\u0413\u0410\u0417\u041f\u0420\u041e\u041c\u0411\u0410\u041d\u041a \u0410\u0412\u0422\u041e\u041b\u0418\u0417\u0418\u041d\u0413\"", "\u0414\u041b-427682-26", "", "", "", "", "\u043b\u0438\u0437\u0438\u043d\u0433", "", "124953,50",
    )
    archive = InvoiceArchiveRecord(
        "", "\u0418\u0421", "-1", "", "IMG_20260518_160208.jpg", "jpg", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "",
        "", "\u0414\u041b-427682-26", "2026-05-18", "\u041f\u0421\u041a \u0418\u0421", "\u0410\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e", "\u041b\u0438\u0437\u0438\u043d\u0433", "\u0420\u043e\u0434\u0438\u043d.\u041a",
        "\u043b\u0438\u0437\u0438\u043d\u0433 \u0425\u0430\u043d\u043a\u0432\u0438", "3896985,00", "\u041d\u043e\u0432\u044b\u0439", "https://drive/invoice", "mid", "file", "\u041e\u041a",
    )

    matched = enrich_payment_records_from_archive([payment], [archive])

    assert matched == 1
    assert payment.project == "\u0410\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e"
    assert payment.invoice_link == "https://drive/invoice"
def test_enrich_payments_matches_company_legal_form_written_as_suffix() -> None:
    payment = PaymentRecord(
        "payment.pdf", "2026-06-23", "", "", "", "\u041e\u041e\u041e \"\u0418\u041d\u0422\u0415\u0420\u041d\u0415\u0422 \u0420\u0415\u0428\u0415\u041d\u0418\u042f\"", "0225583270-0019",
        "", "", "", "", "", "", "5389",
    )
    archive = InvoiceArchiveRecord(
        "2026-06-23 14:04:40", "\u041f\u0421\u041a", "-1", "", "invoice.pdf", "pdf", "", "", "",
        "\u0418\u043d\u0442\u0435\u0440\u043d\u0435\u0442 \u0420\u0435\u0448\u0435\u043d\u0438\u044f, \u041e\u041e\u041e", "0225583270-0019", "2026-06-23", "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", "\u041e\u0444\u0438\u0441",
        "\u041e\u0431\u0435\u0441\u043f\u0435\u0447\u0435\u043d\u0438\u0435 \u043e\u0444\u0438\u0441\u0430", "\u0420\u0430\u0437\u0434\u0440\u043e\u0433\u0438\u043d\u0430.\u0421", "\u041a\u0430\u043d\u0446\u0435\u043b\u044f\u0440\u0438\u044f", "5389", "", "link", "mid", "file", "\u041e\u041a",
    )

    matched = enrich_payment_records_from_archive([payment], [archive])

    assert matched == 1
    assert payment.project == "\u041e\u0444\u0438\u0441"


def test_enrich_payments_from_archive_prefers_complete_duplicate_invoice() -> None:
    payment = PaymentRecord(
        "payment.pdf", "2026-06-23", "", "", "", "\u0418\u041f \u0421\u043e\u0431\u043e\u043b\u0435\u0432", "1195",
        "", "", "", "", "", "", "52523",
    )
    complete = InvoiceArchiveRecord(
        "2026-06-22 10:45:24", "\u041f\u0421\u041a", "-1", "", "invoice.pdf", "pdf", "", "", "",
        "\u0418\u041f \u0421\u043e\u0431\u043e\u043b\u0435\u0432", "1195", "2026-06-19", "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", "\u041e\u0444\u0438\u0441",
        "\u0410\u0440\u0435\u043d\u0434\u0430 \u043f\u043e\u043c\u0435\u0449\u0435\u043d\u0438\u044f", "\u0420\u0430\u0437\u0434\u0440\u043e\u0433\u0438\u043d\u0430.\u0421", "\u0410\u0440\u0435\u043d\u0434\u0430", "152523,00", "", "link", "mid.old", "file.old", "\u041e\u041a",
    )
    incomplete = InvoiceArchiveRecord(
        "2026-06-23 13:12:17", "\u041f\u0421\u041a", "-1", "", "invoice.pdf", "pdf", "", "", "",
        "\u0418\u041f \u0421\u043e\u0431\u043e\u043b\u0435\u0432", "1195", "2026-06-19", "", "", "", "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a", "", "152523,00", "", "", "mid.new", "file.new", "\u041d\u0443\u0436\u043d\u043e \u0440\u0430\u0437\u043e\u0431\u0440\u0430\u0442\u044c",
    )

    matched = enrich_payment_records_from_archive([payment], [complete, incomplete])

    assert matched == 1
    assert payment.object_name == "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
    assert payment.invoice_link == "link"


def test_enrich_payments_without_invoice_number_uses_same_day_counterparty_invoice() -> None:
    payment = PaymentRecord(
        "payment.pdf", "2026-06-23", "", "", "", "\u041e\u041e\u041e \"\u0420\u041e\u041f \u041d\u0410 \u0421\u0412\u042f\u0417\u0418\"", "\u0431/\u0441\u0447",
        "", "", "", "", "", "", "141750",
    )
    old = InvoiceArchiveRecord(
        "2026-04-06 14:36:26", "\u041f\u0421\u041a", "-1", "", "old.pdf", "pdf", "", "", "",
        "\u041e\u041e\u041e \"\u0420\u043e\u043f \u043d\u0430 \u0441\u0432\u044f\u0437\u0438\"", "\u04101001023", "2026-03-31", "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", "\u041c\u0430\u0440\u043a\u0435\u0442\u0438\u043d\u0433", "CRM", "\u0413\u043e\u043d\u0447\u0430\u0440\u043e\u0432 \u0412.", "old", "141750", "", "old-link", "mid.old", "file.old", "\u041e\u041a",
    )
    today = InvoiceArchiveRecord(
        "2026-06-23 13:33:14", "\u041f\u0421\u041a", "-1", "", "today.pdf", "pdf", "", "", "",
        "\u041e\u041e\u041e \"\u0420\u043e\u043f \u043d\u0430 \u0441\u0432\u044f\u0437\u0438\"", "\u04101001091", "2026-06-09", "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", "\u041c\u0430\u0440\u043a\u0435\u0442\u0438\u043d\u0433", "CRM", "\u0413\u043e\u043d\u0447\u0430\u0440\u043e\u0432 \u0412.", "today", "141750", "", "today-link", "mid.today", "file.today", "\u041e\u041a",
    )

    matched = enrich_payment_records_from_archive([payment], [old, today])

    assert matched == 1
    assert payment.purpose == "today"
    assert payment.invoice_link == "today-link"


def test_enrich_payments_from_archive_does_not_match_number_without_counterparty() -> None:
    payment = PaymentRecord("payment.pdf", "2026-06-20", "Расход", "", "", 'ООО "Альфа"', "77", "", "", "", "", "", "", "")
    archive = InvoiceArchiveRecord("", "ПСК", "-1", "", "invoice.pdf", "pdf", "", "", "", 'ООО "Бета"', "77", "", "ПСК Ньютек", "Офис", "Расходники", "Родин.К", "Оплата", "", "Новый", "link", "mid", "file", "ОК")

    matched = enrich_payment_records_from_archive([payment], [archive])

    assert matched == 0
    assert payment.object_name == ""

def test_file_uses_closest_signature_instead_of_first_direction() -> None:
    messages = [
        {"timestamp": 1000, "body": {"mid": "old-signature", "text": "Объект: ПСК\nПроект: Офис\nКонтрагент: Плаза Телеком"}},
        {"timestamp": 1500, "body": {"mid": "file", "attachments": [{"type": "file", "filename": "6327.pdf", "url": "https://files/6327.pdf"}]}},
        {"timestamp": 1525, "body": {"mid": "new-signature", "text": "Объект: Антарес\nПроект: км монтаж\nКонтрагент: Автокран аренда"}},
    ]
    records = create_invoice_archive_records(
        messages,
        "ПСК",
        "-1",
        {
            "unresolved_status": "Нужно разобрать",
            "objects": {"ПСК Ньютек": ["ПСК"], "Антарес": ["Антарес"]},
            "projects": {"Офис": ["Офис"], "КМ ( М )": ["км монтаж"]},
            "responsibles": {},
            "counterparties": {},
        },
    )
    file_record = next(record for record in records if record.file_name == "6327.pdf")
    assert file_record.object_name == "Антарес"
    assert file_record.project == "КМ ( М )"


def test_extract_invoice_details_from_text_reads_europlan_header_before_power_of_attorney():
    details = extract_invoice_details_from_text('Получатель ООО "ЕВРОПЛАН СЕРВИС"\nСЧЕТ № ЛЛЛ36460363 Для оплаты счёта отсканируйте QR-код в приложении банка.от 17.06.2026\nДата платежа Начислено по полису Погашено по полису К оплате\n17.06.2026 26 870,95 руб 0,00 руб 26 870,95 руб\nИТОГО К ОПЛАТЕ: 26 870,95 руб\nНДС не облагается.\nВ назначении платежа указать: Оплата полиса ОСАГО по счету ЛЛЛ36460363 через уполномоченного агента ООО "ЕВРОПЛАН СЕРВИС".\n(Действующий на основании Доверенности №2681/2025 от 05.09.2025 г.)')

    assert details["counterparty"] == 'ООО "ЕВРОПЛАН СЕРВИС"'
    assert details["invoice_number"] == "ЛЛЛ36460363"
    assert details["invoice_date"] == "2026-06-17"
    assert details["amount"] == "26870,95"
    assert details["payment_type"] == "Безналичные без НДС"



def test_enrich_payments_preserves_nonempty_payment_purpose():
    payment = PaymentRecord(
        "payment.pdf", "2026-06-23", "Расход", "Безналичные с НДС", "б/н Сбербанк",
        'ООО "СТАРТ"', "1941", "", "", "", "", "Смесь аргон/углекислота в баллонах", "", "22000",
    )
    archive = InvoiceArchiveRecord(
        "2026-06-18 10:00:00", "ПСК", "-1", "", "invoice.pdf", "pdf", "Расход",
        "Безналичные с НДС", "", 'ООО "СТАРТ"', "1941", "2026-06-18", "Производство",
        "Производственные расходы", "Расходники", "Соловцов Н.", "Газы", "22000", "",
        "link", "mid", "file", "ОК",
    )
    matched = enrich_payment_records_from_archive([payment], [archive])
    assert matched == 1
    assert payment.purpose == "Смесь аргон/углекислота в баллонах"
    assert payment.project == "Производственные расходы"


def test_enrich_missing_invoice_prefers_latest_complete_counterparty_record():
    payment = PaymentRecord(
        "payment.pdf", "2026-06-23", "Расход", "Безналичные с НДС", "б/н Сбербанк",
        'ООО "КАРД-ИНФО СЕРВИС"', "б/сч", "", "", "", "", "мурсал", "", "100000",
    )
    complete = InvoiceArchiveRecord(
        "2026-06-17 12:00:00", "ПСК", "-1", "", "old.pdf", "pdf", "Расход",
        "Безналичные с НДС", "", 'ООО "КАРД-ИНФО СЕРВИС"', "1", "2026-06-17",
        "Конвертация", "Конвертация", "", "Мочалов.К", "мурсал", "100000", "",
        "old-link", "mid.old", "file.old", "ОК",
    )
    incomplete_today = InvoiceArchiveRecord(
        "2026-06-23 12:00:00", "ПСК", "-1", "", "today.pdf", "pdf", "Расход",
        "Безналичные с НДС", "", 'ООО "КАРД-ИНФО СЕРВИС"', "1", "2026-06-23",
        "", "", "", "", "", "238100", "", "today-link", "mid.today", "file.today",
        "Нужно разобрать",
    )
    matched = enrich_payment_records_from_archive([payment], [complete, incomplete_today])
    assert matched == 1
    assert payment.object_name == "Конвертация"
    assert payment.project == "Конвертация"
    assert payment.invoice_link == "old-link"



def test_extract_invoice_details_treats_five_percent_vat_as_without_vat():
    details = extract_invoice_details_from_text(
        '\u041e\u041e\u041e "\u041f\u0420\u0418\u041c\u0415\u0420" \u0421\u0447\u0451\u0442 \u2116 10 \u043e\u0442 23.06.2026. \u0412 \u0442\u043e\u043c \u0447\u0438\u0441\u043b\u0435 \u041d\u0414\u0421 5 % - 476.19 \u0440\u0443\u0431.'
    )
    assert details["payment_type"] == "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0431\u0435\u0437 \u041d\u0414\u0421"


def test_extract_invoice_details_from_text_normalizes_common_ocr_invoice_symbols():
    details = extract_invoice_details_from_text(
        "\u0421\u0447\u0435\u0442 \u2116 \u00a99-0007124/Y "
        "\u043e\u0442 22 \u0438\u044e\u043d\u044f 2026 \u0433. "
        '\u041f\u043e\u0441\u0442\u0430\u0432\u0449\u0438\u043a \u041e\u041e\u041e "\u041d\u042d\u0422\u0421\u0422\u041e\u0420"'
    )

    assert details["invoice_number"] == "\u04249-0007124/\u0423"


def test_enrich_payments_uses_archive_purpose_for_technical_bank_purpose():
    payment = PaymentRecord(
        "payment.pdf", "2026-06-29", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0431\u0435\u0437 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a",
        "\u041e\u041e\u041e \"\u041b\u041a \u0410\u041b\"", "LK1774798", "", "", "", "", "\u043d\u0430\u0447\u0438\u0441\u043b\u0435\u043d\u043d\u044b\u0435 \u043d\u0430 25.06.2026\u0433", "", "1000",
    )
    archive = InvoiceArchiveRecord(
        "2026-06-29 12:39:21", "\u041f\u0421\u041a", "-1", "", "invoice.pdf", "pdf", "\u0420\u0430\u0441\u0445\u043e\u0434",
        "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0431\u0435\u0437 \u041d\u0414\u0421", "", "\u041e\u041e\u041e \"\u041b\u041a \u0410\u041b\"", "LK1774798", "2026-06-29", "\u0422\u0424\u0417",
        "\u041a\u0416", "\u041f\u043e\u0434\u0440\u044f\u0434\u0447\u0438\u043a", "\u0420\u043e\u0434\u0438\u043d.\u041a", "\u043f\u0435\u043d\u0438 \u0445\u0430\u0432\u0430\u043b \u0434\u0436\u0443\u043b\u0438\u043e\u043d \u0438\u044e\u043d\u044c", "1000", "\u041e\u043f\u043b\u0430\u0447\u0435\u043d",
        "link", "mid", "file", "\u041e\u041a",
    )

    matched = enrich_payment_records_from_archive([payment], [archive])

    assert matched == 1
    assert payment.purpose == "\u043f\u0435\u043d\u0438 \u0445\u0430\u0432\u0430\u043b \u0434\u0436\u0443\u043b\u0438\u043e\u043d \u0438\u044e\u043d\u044c"

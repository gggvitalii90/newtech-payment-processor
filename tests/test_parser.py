from pathlib import Path

import pytest

from payment_processor.parser import parse_payment_pdf as _parse_payment_pdf, parse_payment_text


ROOT = Path(__file__).resolve().parents[1] / ".staging" / "payment-history" / "payments"


def parse_payment_pdf(path: Path):
    if not path.exists():
        pytest.skip(f"Нет внешнего PDF-образца: {path.name}")
    return _parse_payment_pdf(path)


def test_parses_alfa_psk_payment_with_multiline_purpose():
    record = parse_payment_pdf(ROOT / "2026.06.08" / "Платежное_поручение_№195.pdf")

    assert record.name == "Платежное_поручение_№195.pdf"
    assert record.date == "2026-06-08"
    assert record.bank == "б/н Альфа"
    assert record.counterparty == 'ООО "КЕХ ЕКОММЕРЦ"'
    assert record.invoice_number == "01-2111666464"
    assert record.purpose == "Внесение аванса в личный кабинет 129460940 по счёту № 01-2111666464 от 04.06.2026 в сумме 100 000,00 руб"
    assert record.amount == "100000"


def test_parses_ip_mochalov_payment():
    record = parse_payment_pdf(ROOT / "2026.06.08" / "Платежное_поручение_№151_08.06.2026.pdf")

    assert record.date == "2026-06-08"
    assert record.bank == "б/н ИП Мочалов"
    assert record.counterparty == "ИП Корякин Максим Станиславович"
    assert record.invoice_number == "90"
    assert record.payment_type == "Безналичные без НДС"
    assert record.amount == "212800"


def test_parses_sber_payment_order():
    record = parse_payment_pdf(ROOT / "2026.06.08" / "2901071427_08.06.2026_522.PDF")

    assert record.date == "2026-06-08"
    assert record.bank == "б/н Сбербанк"
    assert record.counterparty.startswith("УФК по Архангельской области и Ненецкому автономному округу")
    assert record.invoice_number == "б/сч"
    assert record.payment_type == "Безналичные без НДС"
    assert record.amount == "562,50"


def test_parses_tochka_payment_order():
    record = parse_payment_pdf(
        ROOT / "2026.02.04" / "Платежное_поручение_№10_от_04_02_2026_на_сумму_22400.pdf"
    )

    assert record.date == "2026-02-04"
    assert record.bank == "б/н Точка"
    assert record.counterparty
    assert record.amount == "22400"


def test_parses_investstroy_payment_order():
    record = parse_payment_pdf(ROOT / "2026.03.06 ИС" / "Платежное_поручение_№18.pdf")

    assert record.date == "2026-03-06"
    assert record.bank == "б/н ИНВЕСТСТРОЙ"
    assert record.amount == "14700"


def test_parses_uppercase_invoice_number_after_first_sentence():
    record = parse_payment_pdf(ROOT / "2026.05.27" / "Платежное_поручение_№159.pdf")

    assert record.counterparty == 'ООО "ФИРМА "СЕВЗАПМЕТАЛЛ"'
    assert record.purpose == "Профиль сварной"
    assert record.invoice_number == "19971"


def test_parses_multiline_counterparty_and_purpose():
    record = parse_payment_pdf(ROOT / "2026.04.22" / "Платежное_поручение_№106.pdf")

    assert record.counterparty == 'АНО ДПО "ПЕРВЫЙ ЦЕНТР ПОВЫШЕНИЯ КВАЛИФИКАЦИИ И ПРОФЕССИОНАЛЬНОЙ ПОДГОТОВКИ"'
    assert record.invoice_number == "387"
    assert record.purpose == "Обучение по курсу: Безопасные методы и приемы выполнения работ на высоте"


def test_uses_actual_payer_bank_and_payer_identity_for_mochalov_sber_file():
    record = parse_payment_pdf(ROOT / "2026.03.12" / "531101979997_12.03.2026_31.PDF")

    assert record.bank == "б/н ИП Мочалов Сбер"
    assert record.counterparty == "СОКОЛОВ ВИКТОР СЕРГЕЕВИЧ"
    assert record.invoice_number == "262"


def test_does_not_put_purpose_line_into_counterparty():
    record = parse_payment_pdf(ROOT / "2026.02.10" / "7805532233_10.02.2026_147.PDF")

    assert record.counterparty == 'ООО "ГАРАНТМЕД"'
    assert record.invoice_number == "15"
    assert record.purpose == "Проведение предрейсовых медицинских осмотров с января 2026 г по декабрь 2026 г"


def test_sber_return_payment_uses_top_amount_and_no_contract_word_as_invoice():
    text = """
    ПЛАТЕЖНОЕ ПОРУЧЕНИЕ № 528
    11.06.2026
    Сумма
    300000-00
    Сч. № 40702810855000125967
    Плательщик
    Банк Плательщика
    БИК 044030653
    Банк Получателя
    ООО "Банк Точка"
    Получатель
    ООО "АКТЕК"
    Вовзрат на основании СОГЛАШЕНИЕ
    о расторжении договора подряда№ 101-12/27-АА от 27 декабря 2024 года. В том числе НДС 22 % - 54098.36 рублей.
    Назначение платежа
    """

    record = parse_payment_text(text, "sample.pdf")

    assert record.amount == "300000"
    assert record.invoice_number == "б/сч"
    assert record.payment_type == "Безналичные с НДС"


def test_applies_first_matching_classification_rule():
    text = """
    ПЛАТЁЖНОЕ ПОРУЧЕНИЕ 1 08.06.2026
    Сумма 30000-00
    Сч. № 40702810532250003784
    Банк плательщика
    ИНН 000 КПП 000 Сч. № 40702810022130000548
    ООО "ППР"
    Получатель
    Оплата по договору №77600100024090422 от 14 апреля 2022 г. В том числе НДС 20%, 5833.33 руб.
    Назначение платежа
    """
    rules = {
        "classification_rules": [
            {
                "counterparty_contains": "ППР",
                "object": "ПСК Ньютек",
                "project": "Офис",
                "budget_item": "Топливо",
                "responsible": "Родин.К",
            }
        ]
    }

    record = parse_payment_text(text, "sample.pdf", rules)

    assert record.object_name == "ПСК Ньютек"
    assert record.project == "Офис"
    assert record.budget_item == "Топливо"
    assert record.responsible == "Родин.К"


def test_rules_from_power_query_classify_kaktus():
    text = """
    ПЛАТЁЖНОЕ ПОРУЧЕНИЕ 1 08.06.2026
    Сумма 1000-00
    Сч. № 40702810532250003784
    Банк плательщика
    ИНН 000 КПП 000 Сч. № 40702810022130000548
    ООО "КАКТУС"
    Получатель
    Оплата рекламы. Счет на оплату № 1 от 08 июня 2026 г. В том числе НДС 20%, 100 руб.
    Назначение платежа
    """
    rules = {
        "classification_rules": [
            {
                "counterparty_contains": "КАКТУС",
                "object": "ПСК Ньютек",
                "project": "Маркетинг",
                "budget_item": "Авито",
                "responsible": "Гончаров В.",
            }
        ]
    }

    record = parse_payment_text(text, "sample.pdf", rules)

    assert record.object_name == "ПСК Ньютек"
    assert record.project == "Маркетинг"
    assert record.budget_item == "Авито"
    assert record.responsible == "Гончаров В."


def test_payment_without_vat_marker_defaults_to_cashless_without_vat():
    text = """
    ПЛАТЁЖНОЕ ПОРУЧЕНИЕ 166 28.05.2026
    Сумма 399503-00
    Сч. № 40702810532250003784
    Казначейство России (ФНС России)
    Получатель
    ЕНП
    Назначение платежа
    """
    record = parse_payment_text(text, "tax.pdf")
    assert record.payment_type == "Безналичные без НДС"


def test_split_vat_tail_is_included_in_payment_purpose():
    text = """
    ПЛАТЁЖНОЕ ПОРУЧЕНИЕ 223 15.06.2026
    Сумма 1958-00
    Сч. № 40702810532250003784
    ООО "ВСЕИНСТРУМЕНТЫ.РУ"
    Получатель
    Полиуретановый герметик. Счет на оплату № 2606-183866-65535 от 15 июня 2026 года. В том числе НДС
    22%, 353.08 руб.
    Назначение платежа
    """
    record = parse_payment_text(text, "tools.pdf")
    assert record.payment_type == "Безналичные с НДС"
    assert record.purpose == "Полиуретановый герметик"



def test_extracts_contract_number_with_words_between_contract_and_number_sign():
    text = """
    \u041f\u041b\u0410\u0422\u0401\u0416\u041d\u041e\u0415 \u041f\u041e\u0420\u0423\u0427\u0415\u041d\u0418\u0415 51 18.06.2026
    \u0421\u0443\u043c\u043c\u0430 1158000-00
    \u041e\u041e\u041e "\u041f\u041e\u041b\u0423\u0427\u0410\u0422\u0415\u041b\u042c"
    \u041f\u043e\u043b\u0443\u0447\u0430\u0442\u0435\u043b\u044c
    \u041e\u043f\u043b\u0430\u0442\u0430 \u0430\u0432\u0430\u043d\u0441\u043e\u0432\u043e\u0433\u043e \u043f\u043b\u0430\u0442\u0435\u0436\u0430 \u043f\u043e \u0434\u043e\u0433\u043e\u0432\u043e\u0440\u0443 \u0444\u0438\u043d\u0430\u043d\u0441\u043e\u0432\u043e\u0439 \u0430\u0440\u0435\u043d\u0434\u044b \u2116 AA0201470055. \u0412 \u0442\u043e\u043c \u0447\u0438\u0441\u043b\u0435 \u041d\u0414\u0421 22%, 208819.67 \u0440\u0443\u0431.
    \u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u043f\u043b\u0430\u0442\u0435\u0436\u0430
    """
    record = parse_payment_text(text, "sample.pdf")
    assert record.invoice_number == "AA0201470055"


def test_extracts_leasing_contract_number_from_payment_purpose():
    text = """
    \u041f\u041b\u0410\u0422\u0401\u0416\u041d\u041e\u0415 \u041f\u041e\u0420\u0423\u0427\u0415\u041d\u0418\u0415 47 17.06.2026
    \u0421\u0443\u043c\u043c\u0430 124953-50
    \u041e\u041e\u041e "\u0413\u0410\u0417\u041f\u0420\u041e\u041c\u0411\u0410\u041d\u041a \u0410\u0412\u0422\u041e\u041b\u0418\u0417\u0418\u041d\u0413"
    \u041f\u043e\u043b\u0443\u0447\u0430\u0442\u0435\u043b\u044c
    4\u044b\u0439 \u043f\u043b\u0430\u0442\u0435\u0436 \u043f\u043e \u0414\u043e\u0433\u043e\u0432\u043e\u0440\u0443 \u0430\u0440\u0435\u043d\u0434\u044b \u043b\u0438\u0437\u0438\u043d\u0433\u0430 \u2116 \u0414\u041b-427682-26 \u043e\u0442 13.03.2026 \u0433. \u0412 \u0442\u043e\u043c \u0447\u0438\u0441\u043b\u0435 \u041d\u0414\u0421 22%, 22686.33 \u0440\u0443\u0431.
    \u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u043f\u043b\u0430\u0442\u0435\u0436\u0430
    """
    record = parse_payment_text(text, "sample.pdf")
    assert record.invoice_number == "\u0414\u041b-427682-26"
def test_extracts_order_number_from_payment_purpose() -> None:
    text = (
        "\u041f\u041b\u0410\u0422\u0415\u0416\u041d\u041e\u0415 \u041f\u041e\u0420\u0423\u0427\u0415\u041d\u0418\u0415 541 23.06.2026\n"
        "\u0421\u0443\u043c\u043c\u0430 5389-00\n"
        "\u041e\u041e\u041e \"\u0418\u041d\u0422\u0415\u0420\u041d\u0415\u0422 \u0420\u0415\u0428\u0415\u041d\u0418\u042f\"\n"
        "\u041f\u043e\u043b\u0443\u0447\u0430\u0442\u0435\u043b\u044c\n"
        "\u041e\u043f\u043b\u0430\u0442\u0430 \u043f\u043e \u0437\u0430\u043a\u0430\u0437\u0443 0225583270-0019 \u043e\u0442 23.06.2026\u0433\n"
        "\u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u043f\u043b\u0430\u0442\u0435\u0436\u0430"
    )

    record = parse_payment_text(text, "sample.pdf")

    assert record.invoice_number == "0225583270-0019"


def test_extracts_invoice_number_from_payment_purpose_without_number_sign():
    text = 'ПЛАТЕЖНОЕ ПОРУЧЕНИЕ № 49\n18.06.2026\nСумма\n26870-95\nООО "ЕВРОПЛАН СЕРВИС"\nПолучатель\nОплата полиса ОСАГО ООО "СК "СОГЛАСИЕ" на ТС LFPH4CPP8S1K26361 по счету ЛЛЛ36460363 через уполномоченного агента ООО "ЕВРОПЛАН СЕРВИС". НДС не облагается\nНазначение платежа'

    record = parse_payment_text(text, "sample.pdf")

    assert record.invoice_number == "ЛЛЛ36460363"


def test_extracts_invoice_number_from_wrapped_payment_purpose_with_company_name():
    text = 'ПЛАТЕЖНОЕ ПОРУЧЕНИЕ № 49\n18.06.2026\nСумма\n26870-95\nООО "ЕВРОПЛАН СЕРВИС"\nПолучатель\nОплата полиса ОСАГО ООО "СК "СОГЛАСИЕ" на ТС LFPH4CPP8S1K26361 по счету ЛЛЛ36460363 через\nуполномоченного агента ООО "ЕВРОПЛАН СЕРВИС". НДС не облагается\nНазначение платежа'

    record = parse_payment_text(text, "sample.pdf")

    assert record.invoice_number == "ЛЛЛ36460363"



def test_parses_mixed_case_person_as_counterparty_before_recipient_marker():
    text = """
    ПЛАТЁЖНОЕ ПОРУЧЕНИЕ 166 23.06.2026
    Сумма 45000-00
    Сч. № 40802810932180010224
    СЕВЕРО-ЗАПАДНЫЙ БАНК ПАО СБЕРБАНК г Санкт-Петербург
    Банк получателя
    БИК 044030653
    Сч. № 30101810500000000653
    ИНН 522102010833 КПП Сч. № 40817810355177733473
    Рысев Игорь Васильевич
    Получатель
    SEO продвижение сайта. Счёт на оплату №30566930 от 22 июня 2026 г. НДС не облагается
    Назначение платежа
    """
    record = parse_payment_text(text, "payment.pdf")
    assert record.counterparty == "Рысев Игорь Васильевич"
    assert record.invoice_number == "30566930"


def test_extracts_invoice_number_after_abbreviated_account_reference():
    text = """
    ПЛАТЁЖНОЕ ПОРУЧЕНИЕ 167 23.06.2026
    Сумма 141750-00
    ООО "РОП НА СВЯЗИ"
    Получатель
    Сопровождение отдела продаж. Оплата по сч. №А1001091 от 09.06.2026г. В том числе НДС 5%, 6750.00 руб.
    Назначение платежа
    """
    record = parse_payment_text(text, "payment.pdf")
    assert record.invoice_number == "А1001091"


def test_classification_rule_can_override_payment_type():
    text = """
    ПЛАТЁЖНОЕ ПОРУЧЕНИЕ 1 23.06.2026
    Сумма 5542-00
    ИП СОБОЛЕВ ГЕРМАН РУСЛАНОВИЧ
    Получатель
    Электроэнергия. Счет №207. В том числе НДС 5%, 263.90 руб.
    Назначение платежа
    """
    rules = {"classification_rules": [{
        "counterparty_contains": "СОБОЛЕВ",
        "invoice_number_contains": "207",
        "payment_type": "Безналичные без НДС",
    }]}
    record = parse_payment_text(text, "payment.pdf", rules)
    assert record.payment_type == "Безналичные без НДС"



def test_five_percent_vat_is_treated_as_without_vat():
    text = """
    \u041f\u041b\u0410\u0422\u0401\u0416\u041d\u041e\u0415 \u041f\u041e\u0420\u0423\u0427\u0415\u041d\u0418\u0415 10 23.06.2026
    \u0421\u0443\u043c\u043c\u0430 10000-00
    \u041e\u041e\u041e "\u041f\u0420\u0418\u041c\u0415\u0420"
    \u041f\u043e\u043b\u0443\u0447\u0430\u0442\u0435\u043b\u044c
    \u041e\u043f\u043b\u0430\u0442\u0430 \u0443\u0441\u043b\u0443\u0433. \u0412 \u0442\u043e\u043c \u0447\u0438\u0441\u043b\u0435 \u041d\u0414\u0421 5,00 % - 476.19 \u0440\u0443\u0431.
    \u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u043f\u043b\u0430\u0442\u0435\u0436\u0430
    """
    record = parse_payment_text(text, "payment.pdf")
    assert record.payment_type == "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0431\u0435\u0437 \u041d\u0414\u0421"

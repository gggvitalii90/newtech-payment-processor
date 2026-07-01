from payment_processor.cash_archive import create_cash_archive_records, parse_cash_message, parse_cash_message_entries


def _ru(value: str) -> str:
    return value.encode("ascii").decode("unicode_escape")


def test_parse_cash_expense_message() -> None:
    parsed = parse_cash_message("Расход\n\nОбъект: пр\nПроект: фот\nСтатья:\nНазначение: зп март\n- 80 000")

    assert parsed == {
        "operation_type": "Расход",
        "object_name": "пр",
        "project": "фот",
        "budget_item": "",
        "purpose": "зп март",
        "amount": "80000",
    }


def test_parse_cash_ignores_chat_noise() -> None:
    assert parse_cash_message("Виталий Гончаров\nхочу расходы занести") is None
    assert parse_cash_message("остаток ноль") is None
    assert parse_cash_message("Остаток 225 035\nПод отчет Родину\n- 145 220 р.") is None
    assert parse_cash_message("Под отчет Родину\n- 145 220 р.") is None
    assert parse_cash_message("У меня Кирилл\nПриход\n+ 5 000 000 Хидир, км пр, работы") is None


def test_parse_cash_splits_freeform_multiple_expenses() -> None:
    entries = parse_cash_message_entries(
        "Расход\n"
        "- 79 000 ПСК, фот, Болотин Д. Зп февраль\n"
        "- 21 534 ПСК, командировочные расходы\n"
        "- 50 990 ПСК, фот, Косичкин А. Зп февраль\n"
        "- 128 476 ПСК, фот, Косичкин А. Зп март"
    )

    assert len(entries) == 4
    assert entries[0] == {
        "operation_type": "Расход",
        "amount": "79000",
        "object_name": "ПСК",
        "project": "фот",
        "budget_item": "Болотин Д",
        "purpose": "Зп февраль",
    }
    assert entries[1] == {
        "operation_type": "Расход",
        "amount": "21534",
        "object_name": "ПСК",
        "project": "командировочные расходы",
    }



def test_parse_cash_splits_structured_multiple_expenses_in_one_message() -> None:
    entries = parse_cash_message_entries(
        _ru(
            r"\u0420\u0430\u0441\u0445\u043e\u0434\n\n"
            r"\u041e\u0431\u044a\u0435\u043a\u0442: \u043f\u0441\u043a\n"
            r"\u041f\u0440\u043e\u0435\u043a\u0442: \u0424\u043e\u0442\n"
            r"\u0421\u0442\u0430\u0442\u044c\u044f: \u041a\u043e\u0441\u0438\u0447\u043a\u0438\u043d\n"
            r"\u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435: \u0437\u043f \u041c\u0430\u0439\n"
            r"- 50 000 \u0440.\n\n"
            r"\u041e\u0431\u044a\u0435\u043a\u0442: \u043f\u0441\u043a\n"
            r"\u041f\u0440\u043e\u0435\u043a\u0442: \u0424\u043e\u0442\n"
            r"\u0421\u0442\u0430\u0442\u044c\u044f: \u0420\u043e\u0434\u0438\u043d\n"
            r"\u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435: \u0437\u043f \u041c\u0430\u0439\n"
            r"- 30 000 \u0440."
        )
    )

    assert entries == [
        {
            "operation_type": _ru(r"\u0420\u0430\u0441\u0445\u043e\u0434"),
            "object_name": _ru(r"\u043f\u0441\u043a"),
            "project": _ru(r"\u0424\u043e\u0442"),
            "budget_item": _ru(r"\u041a\u043e\u0441\u0438\u0447\u043a\u0438\u043d"),
            "purpose": _ru(r"\u0437\u043f \u041c\u0430\u0439"),
            "amount": "50000",
        },
        {
            "operation_type": _ru(r"\u0420\u0430\u0441\u0445\u043e\u0434"),
            "object_name": _ru(r"\u043f\u0441\u043a"),
            "project": _ru(r"\u0424\u043e\u0442"),
            "budget_item": _ru(r"\u0420\u043e\u0434\u0438\u043d"),
            "purpose": _ru(r"\u0437\u043f \u041c\u0430\u0439"),
            "amount": "30000",
        },
    ]


def test_parse_cash_keeps_accountable_income() -> None:
    parsed = parse_cash_message("Приход + 10 000 под отчет")

    assert parsed == {
        "operation_type": "Приход",
        "payment_type": "Наличные",
        "responsible": "",
        "amount": "+10000",
        "purpose": "Под отчет",
    }


def test_parse_cash_accountable_income_with_source_object() -> None:
    parsed = parse_cash_message("Приход\n+ 70 000 под отчет с ПСК")

    assert parsed == {
        "operation_type": "Приход",
        "payment_type": "Наличные",
        "responsible": "",
        "amount": "+70000",
        "purpose": "Под отчет",
        "object_name": "ПСК",
    }


def test_create_cash_archive_records_normalizes_today_messages() -> None:
    messages = [
        {
            "timestamp": 1781015264000,
            "body": {"mid": "mid.1", "text": "Расход\n\nОбъект: пск\nПроект: Фот\nСтатья: Родин\nНазначение: зп Май \n- 66 000 р."},
            "sender": {"name": "Кирилл"},
        },
        {
            "timestamp": 1781015272000,
            "body": {"mid": "mid.2", "text": "Объект: пск\nПроект: Конвертация\nСтатья: Родин\nНазначение: плантер\nКонтрагент: Геодвор\n\nПод отчет + 5 300 Родин"},
            "sender": {"name": "Кирилл"},
        },
    ]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"ПСК Ньютек": ["пск"], "Конвертация": ["Конвертация"]},
        "projects": {"ФОТ": ["фот"], "Конвертация": ["Конвертация"]},
        "budget_items": {"Родин К.": ["Родин"]},
        "conversion_values": ["Конвертация"],
        "responsibles": {"Родин.К": ["Родин"], "Мочалов К.": ["Кирилл"]},
        "counterparties": {},
    }

    records = create_cash_archive_records(messages, "-1", dictionaries)

    assert len(records) == 2
    assert records[0].object_name == "ПСК Ньютек"
    assert records[0].project == "ФОТ"
    assert records[0].budget_item == "Родин К."
    assert records[0].amount == "66000"
    assert records[1].object_name == "Конвертация"
    assert records[1].project == "Конвертация"
    assert records[1].operation_type == "Конвертация"
    assert records[1].budget_item == ""
    assert records[1].counterparty == "Геодвор"
    assert records[1].amount == "+5300"


def test_cash_rules_fill_salary_for_production_fot_without_budget() -> None:
    messages = [
        {
            "timestamp": 1781015264000,
            "body": {"mid": "mid.1", "text": "Расход\n\nОбъект: пр\nПроект: фот\nСтатья:\nНазначение: зп март\n- 80 000"},
            "sender": {"name": "Кирилл"},
        }
    ]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"Производство": ["пр"]},
        "projects": {"ФОТ": ["фот"]},
        "budget_items": {"Зарплата": ["зарплата"]},
        "responsibles": {},
        "counterparties": {},
    }

    records = create_cash_archive_records(messages, "-1", dictionaries)

    assert records[0].object_name == "Производство"
    assert records[0].project == "ФОТ"
    assert records[0].budget_item == "Зарплата"
    assert records[0].responsible == "Кирилл"


def test_cash_rules_fix_crane_repair_case() -> None:
    messages = [
        {
            "timestamp": 1781015264000,
            "body": {
                "mid": "mid.1",
                "text": "Расход\n\nОбъект: пск\nПроект: автохояйство\nСтатья: подрядчик\nНазначение: ремонт крана\n- 10 000",
            },
            "sender": {"name": "Кирилл"},
        }
    ]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"ПСК Ньютек": ["пск"], "Автохозяйство": ["автохояйство"]},
        "projects": {"Кран": ["кран"]},
        "budget_items": {"Ремонт ТО": ["ремонт то"]},
        "responsibles": {},
        "counterparties": {},
    }

    records = create_cash_archive_records(messages, "-1", dictionaries)

    assert records[0].object_name == "Автохозяйство"
    assert records[0].project == "Кран"
    assert records[0].budget_item == "Ремонт ТО"


def test_cash_income_for_sergeev_uses_works_budget() -> None:
    messages = [
        {
            "timestamp": 1781015264000,
            "body": {
                "mid": "mid.1",
                "text": "Приход\n\nОбъект: Ип Сергеев\nПроект: Км пр, ар изг\nОплата: нал\nНазначение: металл+ работы, сендвич панели\nПод отчет\n+3 199 398",
            },
            "sender": {"name": "Кирилл"},
        }
    ]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"ИП Сергеев": ["ип сергеев"]},
        "projects": {"КМ (ПР)": ["км пр, ар изг"]},
        "budget_items": {"Работы": ["работы"]},
        "responsibles": {},
        "counterparties": {},
    }

    records = create_cash_archive_records(messages, "-1", dictionaries)

    assert records[0].operation_type == "Приход"
    assert records[0].object_name == "ИП Сергеев"
    assert records[0].project == "КМ (ПР)"
    assert records[0].budget_item == "Работы"
    assert records[0].analysis_status == ""


def test_cash_income_for_crane_maps_to_auto_crane_works() -> None:
    messages = [
        {
            "timestamp": 1781015264000,
            "body": {
                "mid": "mid.1",
                "text": "Приход\n\nОбъект: Кран\nПроект: км монтаж\nОплата: нал\nНазначение: работа крана\nПод отчет\n+ 21 000",
            },
            "sender": {"name": "Кирилл"},
        }
    ]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"Автохозяйство": ["кран"]},
        "projects": {"Кран": ["кран"]},
        "budget_items": {"Работы": ["работы"]},
        "responsibles": {},
        "counterparties": {},
    }

    records = create_cash_archive_records(messages, "-1", dictionaries)

    assert records[0].operation_type == "Приход"
    assert records[0].object_name == "Автохозяйство"
    assert records[0].project == "Кран"
    assert records[0].budget_item == "Работы"
    assert records[0].analysis_status == ""


def test_cash_freeform_auto_fuel_and_office_travel_rules() -> None:
    messages = [
        {
            "timestamp": 1781015264000,
            "body": {"mid": "mid.1", "text": "Расход\n- 6000 пск, автохозяйство, топливо, на кран"},
            "sender": {"name": "Кирилл Мочалов"},
        },
        {
            "timestamp": 1781015264001,
            "body": {"mid": "mid.2", "text": "Расход\n- 28 528 пск,офис,командировочные расходы, отель еда"},
            "sender": {"name": "Кирилл Мочалов"},
        },
    ]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"ПСК Ньютек": ["пск"], "Автохозяйство": ["автохозяйство"]},
        "projects": {"Офис": ["офис"], "Кран": ["кран"]},
        "budget_items": {"Топливо": ["топливо"], "Командировочные расходы": ["командировочные расходы"]},
        "responsibles": {},
        "counterparties": {},
    }

    records = create_cash_archive_records(messages, "-1", dictionaries)

    assert records[0].object_name == "Автохозяйство"
    assert records[0].project == "Кран"
    assert records[0].budget_item == "Топливо"
    assert records[0].purpose == "на кран"
    assert records[1].object_name == "ПСК Ньютек"
    assert records[1].project == "Офис"
    assert records[1].budget_item == "Командировочные расходы"
    assert records[1].purpose == "отель еда"


def test_cash_freeform_income_for_hidir() -> None:
    messages = [
        {
            "timestamp": 1781015264000,
            "body": {"mid": "mid.1", "text": "Приход\n+ 5 000 000 Хидир, км пр, работы"},
            "sender": {"name": "Кирилл"},
        }
    ]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"Хидир": ["хидир"]},
        "projects": {"КМ (ПР)": ["км пр"]},
        "budget_items": {"Работы": ["работы"]},
        "responsibles": {},
        "counterparties": {},
    }

    records = create_cash_archive_records(messages, "-1", dictionaries)

    assert records[0].operation_type == "Приход"
    assert records[0].object_name == "Хидир"
    assert records[0].project == "КМ (ПР)"
    assert records[0].budget_item == "Работы"
    assert records[0].amount == "+5000000"
    assert records[0].analysis_status == ""


def test_cash_freeform_project_aliases_and_new_objects() -> None:
    messages = [
        {
            "timestamp": 1781015264000,
            "body": {"mid": "mid.1", "text": "Расход\n-5170 Левон , км, пр, доставка, грузовичков (рекламация)"},
            "sender": {"name": "Кирилл"},
        },
        {
            "timestamp": 1781015264001,
            "body": {"mid": "mid.2", "text": "Расход\n- 2600 р аларм моторс , км монтаж , расходники , плашки"},
            "sender": {"name": "Кирилл"},
        },
        {
            "timestamp": 1781015264002,
            "body": {"mid": "mid.3", "text": "Расход\n- 9 000 Лидерстрой(металлострой), км монтаж, подрядчик, дом 2"},
            "sender": {"name": "Кирилл"},
        },
        {
            "timestamp": 1781015264003,
            "body": {"mid": "mid.4", "text": "Расход\n- 1 400 Ип Поляков, Ар монтаж, расходники, пилки"},
            "sender": {"name": "Кирилл"},
        },
        {
            "timestamp": 1781015264004,
            "body": {"mid": "mid.5", "text": "Расход\n- 460 Антарес, км монтаж, расходники, болты"},
            "sender": {"name": "Кирилл"},
        },
    ]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {
            "Левон": ["левон"],
            "Аларм Моторс": ["р аларм моторс"],
            "Лидерстрой (Металлострой)": ["лидерстрой(металлострой)"],
            "ИП Поляков": ["ип поляков"],
            "Антарес": ["антарес"],
        },
        "projects": {"КМ (ПР)": ["км пр"], "КМ (М)": ["км монтаж"], "АР": ["ар монтаж"]},
        "budget_items": {"Доставка": ["доставка"], "Расходники": ["расходники"], "Подрядчик": ["подрядчик"]},
        "responsibles": {},
        "counterparties": {},
    }

    records = create_cash_archive_records(messages, "-1", dictionaries)

    assert [(record.object_name, record.project, record.budget_item, record.purpose) for record in records] == [
        ("Левон", "КМ (ПР)", "Доставка", "грузовичков (рекламация)"),
        ("Аларм Моторс", "КМ (М)", "Расходники", "плашки"),
        ("Лидерстрой (Металлострой)", "КМ (М)", "Подрядчик", "дом 2"),
        ("ИП Поляков", "АР", "Расходники", "пилки"),
        ("Антарес", "КМ (М)", "Расходники", "болты"),
    ]
    assert all(record.analysis_status == "" for record in records)


def test_cash_freeform_shorthand_salary_travel_credit_and_commission() -> None:
    messages = [
        {
            "timestamp": 1781015264000,
            "body": {"mid": "mid.1", "text": "Расход\n- 116 500 пр, фот, зп январь\n- 353 500 пр.фот, зп февраль"},
            "sender": {"name": "Кирилл"},
        },
        {
            "timestamp": 1781015264001,
            "body": {"mid": "mid.2", "text": "Расход\n- 50 600 ТФЗ, кредит , КЮ"},
            "sender": {"name": "Кирилл"},
        },
        {
            "timestamp": 1781015264002,
            "body": {"mid": "mid.3", "text": "Расход\n- 14 306 Командировочные расходы мск"},
            "sender": {"name": "Кирилл"},
        },
        {
            "timestamp": 1781015264003,
            "body": {"mid": "mid.4", "text": "РАсход\n- 500 комиссия"},
            "sender": {"name": "Кирилл"},
        },
    ]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"Производство": ["пр"], "ТФЗ": ["тфз"], "ПСК Ньютек": ["пск"]},
        "projects": {"ФОТ": ["фот"], "Кредит": ["кредит"], "Офис": ["офис"], "Банк": ["банк"]},
        "budget_items": {
            "Зарплата": ["зарплата"],
            "Кредит Кирилл Юрьевич": ["кю"],
            "Командировочные расходы": ["командировочные расходы"],
            "Комиссия": ["комиссия"],
        },
        "responsibles": {},
        "counterparties": {},
    }

    records = create_cash_archive_records(messages, "-1", dictionaries)

    assert [(record.object_name, record.project, record.budget_item, record.purpose, record.amount) for record in records] == [
        ("Производство", "ФОТ", "Зарплата", "зп январь", "116500"),
        ("Производство", "ФОТ", "Зарплата", "зп февраль", "353500"),
        ("ТФЗ", "Кредит", "Кредит Кирилл Юрьевич", "", "50600"),
        ("ПСК Ньютек", "Офис", "Командировочные расходы", "мск", "14306"),
        ("ПСК Ньютек", "Банк", "Комиссия", "", "500"),
    ]
    assert all(record.analysis_status == "" for record in records)


def test_cash_ignores_discussion_about_wrong_income_sign() -> None:
    assert (
        parse_cash_message(
            "Виталий Гончаров это приход был\n\n"
            "В таблице стоит как расход\n\n"
            "Алексей Косичкин приход пишется со знаком +\n"
            "Приход\n"
            "-Левон, км монтаж,\n"
            "100 000"
        )
        is None
    )


def test_cash_remaining_rules_scrap_conversion_renessans_and_embedded_balance() -> None:
    messages = [
        {
            "timestamp": 1781015264000,
            "body": {"mid": "mid.1", "text": "Приход \nПр\nСдача металлома\n+ 22 000"},
            "sender": {"name": "Кирилл"},
        },
        {
            "timestamp": 1781015264001,
            "body": {"mid": "mid.2", "text": "Расход\n- 3 500 Усть луга, благоустройство, ремонт трамбовки"},
            "sender": {"name": "Кирилл"},
        },
        {
            "timestamp": 1781015264002,
            "body": {"mid": "mid.3", "text": "А где расход по этому приходу\nПочему не внес ?\nКонвертация мурсал\nОплата авто\nМак карго\n+ 880 000"},
            "sender": {"name": "Кирилл"},
        },
        {
            "timestamp": 1781015264003,
            "body": {"mid": "mid.4", "text": "Расход\n- 30 000 пск, ФОТ, Мироновна Ю, зп апрель\nОтаток 29 617"},
            "sender": {"name": "Кирилл"},
        },
        {
            "timestamp": 1781015264004,
            "body": {"mid": "mid.5", "text": "Расход\n- 5 000 ренессанс, подрядчик, юрист , ответ на претензию"},
            "sender": {"name": "Кирилл"},
        },
    ]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {
            "Производство": ["пр"],
            "Усть Луга": ["усть луга"],
            "Конвертация": ["конвертация"],
            "ПСК Ньютек": ["пск"],
            "Ренессанс": ["ренессанс"],
        },
        "projects": {
            "Реализация": ["реализация"],
            "Благоустройство": ["благоустройство"],
            "Конвертация": ["конвертация"],
            "ФОТ": ["фот"],
            "Офис": ["офис"],
        },
        "budget_items": {
            "Металлолом": ["металлолом"],
            "Ремонт ТО": ["ремонт то"],
            "Миронова": ["мироновна ю"],
            "Юридические услуги": ["юрист"],
        },
        "conversion_values": ["Конвертация"],
        "responsibles": {},
        "counterparties": {},
    }

    records = create_cash_archive_records(messages, "-1", dictionaries)

    assert [(record.operation_type, record.object_name, record.project, record.budget_item, record.purpose, record.amount) for record in records] == [
        ("Приход", "Производство", "Реализация", "Металлолом", "Сдача металлома", "+22000"),
        ("Расход", "Усть Луга", "Благоустройство", "Ремонт ТО", "", "3500"),
        ("Конвертация", "Конвертация", "Конвертация", "", "Конвертация мурсал, Оплата авто, Мак карго", "+880000"),
        ("Расход", "ПСК Ньютек", "ФОТ", "Миронова", "зп апрель", "30000"),
        ("Расход", "Ренессанс", "Офис", "Юридические услуги", "ответ на претензию", "5000"),
    ]


def test_cash_final_unresolved_rules_poliakov_new_dom_and_riverbots() -> None:
    messages = [
        {
            "timestamp": 1781015264000,
            "body": {"mid": "mid.1", "text": "Расход\n- 16 500 Поляков, Ар монтаж, подрядчик, фасонка"},
            "sender": {"name": "Кирилл"},
        },
        {
            "timestamp": 1781015264001,
            "body": {"mid": "mid.2", "text": "Расход\n- 20 000 Новый Дом, км монтаж, подрядчик, проект ппр"},
            "sender": {"name": "Кирилл"},
        },
        {
            "timestamp": 1781015264002,
            "body": {"mid": "mid.3", "text": "Расход\n\nОбъект: Риверботс\nПроект: Метизы\nСтатья: доставка\nНазначение: курьер\n- 3000 руб"},
            "sender": {"name": "Кирилл"},
        },
        {
            "timestamp": 1781015264003,
            "body": {"mid": "mid.4", "text": "Расход\n\nОбъект: Риверботс\nПроект: Метизы\nНазначение: крепеж\n- 1180 руб"},
            "sender": {"name": "Кирилл"},
        },
    ]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {
            "ИП Поляков": ["поляков"],
            "Новый Дом (инвест)": ["новый дом"],
            "Риверботс": ["риверботс"],
        },
        "projects": {"АР": ["ар монтаж"], "КМ (М)": ["км монтаж"]},
        "budget_items": {"Подрядчик": ["подрядчик"], "Доставка": ["доставка"], "Расходники": ["расходники"]},
        "responsibles": {},
        "counterparties": {},
    }

    records = create_cash_archive_records(messages, "-1", dictionaries)

    assert [(record.object_name, record.project, record.budget_item, record.purpose, record.amount) for record in records] == [
        ("ИП Поляков", "АР", "Подрядчик", "фасонка", "16500"),
        ("Новый Дом (инвест)", "КМ (М)", "Подрядчик", "проект ппр", "20000"),
        ("Риверботс", "КМ (М)", "Доставка", "курьер", "3000"),
        ("Риверботс", "КМ (М)", "Расходники", "крепеж", "1180"),
    ]
    assert all(record.analysis_status == "" for record in records)


def test_cash_records_use_short_date_standard_cash_type_and_responsible() -> None:
    messages = [
        {
            "timestamp": 1781015264000,
            "body": {
                "mid": "mid.1",
                "text": _ru(
                    r"\u0420\u0430\u0441\u0445\u043e\u0434\n\n"
                    r"\u041e\u0431\u044a\u0435\u043a\u0442: \u043f\u0441\u043a\n"
                    r"\u041f\u0440\u043e\u0435\u043a\u0442: \u0444\u043e\u0442\n"
                    r"\u0421\u0442\u0430\u0442\u044c\u044f: \u0420\u043e\u0434\u0438\u043d\n"
                    r"\u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435: \u0437\u043f \u041c\u0430\u0439\n"
                    r"- 60 000"
                ),
            },
            "sender": {"name": _ru(r"\u041a\u0438\u0440\u0438\u043b\u043b")},
        }
    ]
    dictionaries = {
        "unresolved_status": _ru(r"\u041d\u0443\u0436\u043d\u043e \u0440\u0430\u0437\u043e\u0431\u0440\u0430\u0442\u044c"),
        "objects": {_ru(r"\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"): [_ru(r"\u043f\u0441\u043a")]},
        "projects": {_ru(r"\u0424\u041e\u0422"): [_ru(r"\u0444\u043e\u0442")]},
        "budget_items": {_ru(r"\u0420\u043e\u0434\u0438\u043d \u041a."): [_ru(r"\u0440\u043e\u0434\u0438\u043d")]},
        "responsibles": {_ru(r"\u0420\u043e\u0434\u0438\u043d.\u041a"): [_ru(r"\u0440\u043e\u0434\u0438\u043d")]},
        "counterparties": {},
    }

    records = create_cash_archive_records(messages, "-1", dictionaries)

    assert records[0].max_date == "2026-06-09"
    assert records[0].payment_type == _ru(r"\u041d\u0430\u043b\u0438\u0447\u043d\u0430\u044f")
    assert records[0].responsible == _ru(r"\u0420\u043e\u0434\u0438\u043d.\u041a")


def test_parse_standalone_cash_conversion_without_income_or_expense_keyword() -> None:
    entries = parse_cash_message_entries(
        _ru(
            r"\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f \u041e\u041e\u041e \u041c\u0430\u0433\u043d\u0438\u0442\u043e \u043e\u043f\u043b\u0430\u0442\u0430 \u043d\u0430 \u041f\u0421\u041a\n"
            r"\u0432\u044b\u0434\u0430\u043d\u043e - 400 000 \u0440."
        )
    )

    assert entries == [
        {
            "operation_type": _ru(r"\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"),
            "object_name": _ru(r"\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"),
            "project": _ru(r"\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"),
            "budget_item": "",
            "purpose": _ru(r"\u041e\u041e\u041e \u041c\u0430\u0433\u043d\u0438\u0442\u043e \u043e\u043f\u043b\u0430\u0442\u0430 \u043d\u0430 \u041f\u0421\u041a"),
            "amount": "400000",
        }
    ]

def test_cash_unloading_metal_with_crane_is_equipment_budget() -> None:
    messages = [{
        "timestamp": 1782741600000,
        "body": {
            "mid": "mid.1",
            "text": _ru(
                r"\u0420\u0430\u0441\u0445\u043e\u0434\n\n"
                r"\u041e\u0431\u044a\u0435\u043a\u0442: \u043f\u0440\n"
                r"\u041f\u0440\u043e\u0435\u043a\u0442: \u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0435 \u0440\u0430\u0441\u0445\u043e\u0434\u044b\n"
                r"\u0421\u0442\u0430\u0442\u044c\u044f: \u0440\u0430\u0437\u0433\u0440\u0443\u0437\u043a\u0430 \u043c\u0435\u0442\u0430\u043b\u043b\u0430\n"
                r"\u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435: \u043a\u0440\u0430\u043d\n"
                r"- 13 000"
            ),
        },
        "sender": {"name": _ru(r"\u041a\u0438\u0440\u0438\u043b\u043b")},
    }]
    dictionaries = {
        "unresolved_status": _ru(r"\u041d\u0443\u0436\u043d\u043e \u0440\u0430\u0437\u043e\u0431\u0440\u0430\u0442\u044c"),
        "objects": {_ru(r"\u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u043e"): [_ru(r"\u043f\u0440")]},
        "projects": {_ru(r"\u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0435 \u0440\u0430\u0441\u0445\u043e\u0434\u044b"): [_ru(r"\u043f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0435 \u0440\u0430\u0441\u0445\u043e\u0434\u044b")]},
        "budget_items": {_ru(r"\u0422\u0435\u0445\u043d\u0438\u043a\u0430"): [_ru(r"\u0442\u0435\u0445\u043d\u0438\u043a\u0430")]},
        "responsibles": {_ru(r"\u0420\u043e\u0434\u0438\u043d.\u041a"): [_ru(r"\u0440\u043e\u0434\u0438\u043d")]},
        "counterparties": {},
    }

    records = create_cash_archive_records(messages, "-1", dictionaries)

    assert records[0].object_name == _ru(r"\u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u043e")
    assert records[0].project == _ru(r"\u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0435 \u0440\u0430\u0441\u0445\u043e\u0434\u044b")
    assert records[0].budget_item == _ru(r"\u0422\u0435\u0445\u043d\u0438\u043a\u0430")
    assert records[0].purpose == _ru(r"\u043a\u0440\u0430\u043d")
    assert records[0].responsible == _ru(r"\u0420\u043e\u0434\u0438\u043d.\u041a")


def test_cash_credit_for_lift_moves_auto_from_budget_to_object():
    messages = [{
        "timestamp": 1782212400000,
        "body": {"mid": "mid.1", "text": "Расход\n\nОбъект: пск\nПроект: кредит\nСтатья: Автохозяйство\nНазначение: кредит подъемник\n- 53 000"},
        "sender": {"name": "Кирилл Мочалов"},
    }]
    dictionaries = {
        "unresolved_status": "Нужно разобрать",
        "objects": {"ПСК Ньютек": ["пск"], "Автохозяйство": ["автохозяйство"]},
        "projects": {"Кредит": ["кредит"], "Подъемник": ["подъемник"]},
        "budget_items": {"Автохозяйство": ["автохозяйство"], "Кредит": ["кредит"]},
        "responsibles": {"Мочалов.К": ["кирилл мочалов"]},
        "counterparties": {},
    }
    records = create_cash_archive_records(messages, "-1", dictionaries)
    assert records[0].object_name == "Автохозяйство"
    assert records[0].project == "Подъемник"
    assert records[0].budget_item == "Кредит"

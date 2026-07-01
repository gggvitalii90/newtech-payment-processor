from payment_processor.dictionaries import normalize_key, normalize_value


def test_normalize_value_maps_alias_to_canonical() -> None:
    dictionaries = {"unresolved_status": "Нужно разобрать", "objects": {"ПСК Ньютек": ["пск", "офис"]}}

    result = normalize_value("objects", "ПСК.", dictionaries, required=True)

    assert result.value == "ПСК Ньютек"
    assert result.status == ""
    assert result.matched


def test_normalize_value_marks_required_unknown() -> None:
    dictionaries = {"unresolved_status": "Нужно разобрать", "objects": {}}

    result = normalize_value("objects", "Неизвестный объект", dictionaries, required=True)

    assert result.value == "Неизвестный объект"
    assert result.status == "Нужно разобрать"
    assert not result.matched


def test_normalize_key_ignores_case_punctuation_and_yo() -> None:
    assert normalize_key(" Счёт № 15. ") == "счет 15"

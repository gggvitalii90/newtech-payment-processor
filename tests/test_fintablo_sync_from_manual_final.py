from scripts.fintablo_sync_from_manual_final import ManualRow, _u, compact_key, resolve_ids


def test_resolve_ids_skips_category_from_wrong_group_but_keeps_direction_and_deal():
    row = ManualRow(
        5,
        _u(r"\u0418\u044e\u043b\u044c 2026"),
        {
            _u(r"\u0422\u0438\u043f \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0438"): _u(r"\u0420\u0430\u0441\u0445\u043e\u0434"),
            _u(r"\u0421\u0442\u0430\u0442\u044c\u044f \u0431\u044e\u0434\u0436\u0435\u0442\u0430"): _u(r"\u0420\u0430\u0431\u043e\u0442\u044b"),
            _u(r"\u041f\u0440\u043e\u0435\u043a\u0442"): _u(r"\u041e\u0444\u0438\u0441"),
            _u(r"\u041e\u0431\u044a\u0435\u043a\u0442"): _u(r"\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"),
        },
    )
    categories = {compact_key(_u(r"\u0420\u0430\u0431\u043e\u0442\u044b")): {"id": 11, "name": _u(r"\u0420\u0430\u0431\u043e\u0442\u044b"), "group": "income"}}
    directions = {compact_key(_u(r"\u041e\u0444\u0438\u0441")): {"id": 22, "name": _u(r"\u041e\u0444\u0438\u0441")}}
    deals = {
        compact_key(_u(r"\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a")): {
            "id": 33,
            "name": _u(r"\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"),
            "stages": [{"id": 44, "name": _u(r"\u041e\u0444\u0438\u0441")}],
        }
    }

    updates, notes = resolve_ids(row, categories, directions, deals)

    assert "categoryId" not in updates
    assert updates["directionId"] == 22
    assert updates["dealId"] == 44
    assert "wrong_category_group_skipped" in notes


def test_match_manual_for_tx_requires_same_operation_group():
    from scripts.fintablo_sync_from_manual_final import match_manual_for_tx

    row = ManualRow(
        10,
        _u(r"\u0418\u044e\u043b\u044c 2026"),
        {
            _u(r"\u0414\u0430\u0442\u0430"): "09.07.2026",
            _u(r"\u0421\u0443\u043c\u043c\u0430"): "150000",
            _u(r"\u0422\u0438\u043f \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0438"): _u(r"\u0420\u0430\u0441\u0445\u043e\u0434"),
            _u(r"\u0422\u0438\u043f \u043e\u043f\u043b\u0430\u0442\u044b"): _u(r"\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421"),
            _u(r"\u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u043f\u043b\u0430\u0442\u0435\u0436\u0430"): _u(r"\u0440\u0430\u0431\u043e\u0442\u044b"),
        },
    )
    tx = {"date": "09.07.2026", "value": 150000, "group": "income", "description": _u(r"\u0440\u0430\u0431\u043e\u0442\u044b")}

    matched, reason = match_manual_for_tx(tx, [row])

    assert matched is None
    assert reason == "no_date_amount"

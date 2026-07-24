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


def test_cash_reconciliation_matches_one_to_one_and_reports_missing():
    from scripts.fintablo_cash_manual_reconciliation import reconcile_cash

    cash_type = _u(r"\u041d\u0430\u043b\u0438\u0447\u043d\u0430\u044f")
    manual_rows = [
        ManualRow(2, _u(r"\u0418\u044e\u043b\u044c 2026"), {
            _u(r"\u0414\u0430\u0442\u0430"): "13.07.2026",
            _u(r"\u0422\u0438\u043f \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0438"): _u(r"\u0420\u0430\u0441\u0445\u043e\u0434"),
            _u(r"\u0422\u0438\u043f \u043e\u043f\u043b\u0430\u0442\u044b"): cash_type,
            _u(r"\u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u043f\u043b\u0430\u0442\u0435\u0436\u0430"): _u(r"\u043a\u0440\u0435\u0434\u0438\u0442"),
            _u(r"\u0421\u0443\u043c\u043c\u0430"): "33130",
        }),
        ManualRow(3, _u(r"\u0418\u044e\u043b\u044c 2026"), {
            _u(r"\u0414\u0430\u0442\u0430"): "13.07.2026",
            _u(r"\u0422\u0438\u043f \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0438"): _u(r"\u0420\u0430\u0441\u0445\u043e\u0434"),
            _u(r"\u0422\u0438\u043f \u043e\u043f\u043b\u0430\u0442\u044b"): cash_type,
            _u(r"\u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u043f\u043b\u0430\u0442\u0435\u0436\u0430"): _u(r"\u043d\u0435\u0442 \u0432 \u0444\u0438\u043d\u0442\u0430\u0431\u043b\u043e"),
            _u(r"\u0421\u0443\u043c\u043c\u0430"): "33130",
        }),
    ]
    txs = [
        {"id": 10, "date": "13.07.2026", "value": "33130", "group": "outcome", "moneybagId": 1, "description": _u(r"\u043a\u0440\u0435\u0434\u0438\u0442")}
    ]
    moneybags = {1: {"id": 1, "type": "nal", "name": cash_type}}

    result = reconcile_cash(manual_rows, txs, moneybags)

    assert result.summary["manual_cash_rows"] == 2
    assert result.summary["fintablo_cash_rows"] == 1
    assert result.summary["matched"] == 1
    assert result.summary["missing"] == 1
    assert result.missing[0]["manual_row"] == 3


def test_cash_reconciliation_treats_positive_conversion_as_income_cash_flow():
    from scripts.fintablo_cash_manual_reconciliation import reconcile_cash

    cash_type = _u(r"\u041d\u0430\u043b\u0438\u0447\u043d\u0430\u044f")
    row = ManualRow(4, _u(r"\u0410\u043f\u0440\u0435\u043b\u044c 2026"), {
        _u(r"\u0414\u0430\u0442\u0430"): "08.04.2026",
        _u(r"\u0422\u0438\u043f \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0438"): _u(r"\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"),
        _u(r"\u0422\u0438\u043f \u043e\u043f\u043b\u0430\u0442\u044b"): cash_type,
        _u(r"\u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u043f\u043b\u0430\u0442\u0435\u0436\u0430"): _u(r"\u043f\u0441\u043a \u043a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"),
        _u(r"\u0421\u0443\u043c\u043c\u0430"): "5000",
    })
    txs = [
        {"id": 11, "date": "08.04.2026", "value": "5000", "group": "income", "moneybagId": 1, "description": _u(r"\u043f\u0441\u043a \u043a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f")}
    ]
    moneybags = {1: {"id": 1, "type": "nal", "name": cash_type}}

    result = reconcile_cash([row], txs, moneybags)

    assert result.summary["matched"] == 1
    assert result.summary["missing"] == 0

from datetime import date
from pathlib import Path

from payment_processor.invoice_archive import InvoiceArchiveRecord
from payment_processor.models import PaymentRecord
from payment_processor.payment_history import (
    apply_mode_defaults,
    build_final_history,
    collect_payment_pdfs,
    dedupe_paths_by_sha256,
    validate_payment_records,
    unmatched_invoice_issues,
    write_payment_records_csv,
    reference_lists_from_dictionaries,
    dedupe_payment_records_by_identity,
)


def u(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")


def payment(name="one.pdf", payment_date="2026-04-02", counterparty='ООО "ТЕСТ"', invoice="15"):
    return PaymentRecord(name, payment_date, "Расход", "Безналичные без НДС", "б/н Альфа", counterparty, invoice, "", "", "", "", "Оплата", "", "100")


def invoice(counterparty='ООО "ТЕСТ"', number="15"):
    return InvoiceArchiveRecord("", "ПСК", "-1", "", "invoice.pdf", "pdf", "", "", "", counterparty, number, "", "ПСК Ньютек", "Офис", "Расходники", "Родин.К", "Материалы", "100", "Новый", "https://drive/invoice", "mid", "file", "ОК")


def test_collect_payment_pdfs_includes_psk_and_is_folders_inside_period(tmp_path: Path) -> None:
    for folder_name in ("2026.03.31", "2026.04.01", "2026.04.01 ИС", "2026.04.02"):
        folder = tmp_path / folder_name; folder.mkdir()
        (folder / f"{folder_name}.pdf").write_bytes(b"pdf")
    paths = collect_payment_pdfs(tmp_path, date(2026, 4, 1), date(2026, 4, 1))
    assert [path.parent.name for path in paths] == ["2026.04.01", "2026.04.01 ИС"]


def test_dedupe_paths_by_sha256_keeps_one_copy_and_reports_duplicate(tmp_path: Path) -> None:
    first = tmp_path / "a.pdf"; second = tmp_path / "b.pdf"; third = tmp_path / "c.pdf"
    first.write_bytes(b"same"); second.write_bytes(b"same"); third.write_bytes(b"different")
    unique, duplicates = dedupe_paths_by_sha256([first, second, third])
    assert unique == [first, third]
    assert duplicates == {first: [second]}


def test_build_final_history_keeps_unmatched_payment_and_enriches_match() -> None:
    matched = payment("matched.pdf")
    unmatched = payment("unmatched.pdf", counterparty='ООО "ДРУГОЙ"', invoice="99")
    matched.invoice_link = "https://drive/payment-matched"
    unmatched.invoice_link = "https://drive/payment-unmatched"
    cash = PaymentRecord("mid.cash", "2026-04-02", "Расход", "Наличные", "", "", "", "Производство", "ФОТ", "Зарплата", "Родин.К", "зп", "", "500")
    final, matched_count = build_final_history([matched, unmatched], [invoice()], [cash], [])
    assert len(final) == 3
    assert matched_count == 1
    assert next(row for row in final if row.name == "matched.pdf").object_name == "ПСК Ньютек"
    assert next(row for row in final if row.name == "unmatched.pdf").object_name == ""
    assert next(row for row in final if row.name == "matched.pdf").invoice_link == "https://drive/invoice"
    assert next(row for row in final if row.name == "unmatched.pdf").invoice_link == ""


def test_validate_payment_records_reports_missing_native_fields_but_not_invoice_number() -> None:
    row = payment()
    row.bank = ""; row.amount = ""; row.invoice_number = ""
    issues = validate_payment_records([row])
    assert [(issue.issue_type, issue.fields) for issue in issues] == [("missing_payment_fields", ("Банк", "Сумма"))]



def test_apply_mode_defaults_sets_only_investstroy_object_and_preserves_project() -> None:
    source = payment()
    source.object_name = "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
    source.project = "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"
    source.budget_item = "\u041f\u043e\u0434\u0440\u044f\u0434\u0447\u0438\u043a"

    result = apply_mode_defaults([source], "IS")

    assert result[0].object_name == "\u041f\u0421\u041a \u0418\u0421"
    assert result[0].project == "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"
    assert result[0].budget_item == "\u041f\u043e\u0434\u0440\u044f\u0434\u0447\u0438\u043a"
    assert source.object_name == "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"

def test_apply_mode_defaults_leaves_psk_records_unchanged() -> None:
    source = payment()
    source.object_name = "ПСК Ньютек"
    source.project = "Маркетинг"

    result = apply_mode_defaults([source], "ПСК")

    assert result[0].object_name == "ПСК Ньютек"
    assert result[0].project == "Маркетинг"

def test_unmatched_invoice_issues_reports_only_payment_without_match() -> None:
    issues = unmatched_invoice_issues(
        [payment("matched.pdf"), payment("unmatched.pdf", counterparty='ООО "ДРУГОЙ"', invoice="99")],
        [invoice()],
    )
    assert [(issue.source, issue.issue_type) for issue in issues] == [("unmatched.pdf", "unmatched_invoice")]


def test_write_payment_records_csv_uses_google_columns(tmp_path: Path) -> None:
    target = tmp_path / "payments.csv"
    write_payment_records_csv(target, [payment()])
    text = target.read_text(encoding="utf-8-sig")
    assert text.splitlines()[0].startswith("№,Дата,Тип операции")
    assert "one.pdf" in text

def test_reference_lists_from_dictionaries_uses_canonical_regulated_values() -> None:
    references = reference_lists_from_dictionaries({
        "objects": {"ПСК Ньютек": ["пск"]},
        "projects": {"Офис": ["обучение"]},
        "budget_items": {"Расходники": ["расходники"]},
        "responsibles": {"Родин.К": ["родин"]},
    })
    assert references == {
        "Объект": ["ПСК Ньютек"],
        "Проект": ["Офис"],
        "Статья бюджета": ["Расходники"],
        "Ответственный": ["Родин.К"],
    }

def test_dedupe_payment_records_by_identity_keeps_linked_copy_of_same_payment() -> None:
    first = payment("Платежное_поручение_№133.pdf", payment_date="2026-05-25", invoice="б/сч")
    second = payment("Платежное_поручение_№133_25.05.2026.pdf", payment_date="2026-05-25", invoice="б/сч")
    first.bank = second.bank = "б/н Альфа"
    first.counterparty = second.counterparty = "Казначейство России"
    first.purpose = second.purpose = "Стройка+"
    first.amount = second.amount = "17475"
    second.invoice_link = "https://drive/linked"
    result = dedupe_payment_records_by_identity([first, second])
    assert len(result) == 1
    assert result[0].invoice_link == "https://drive/linked"


def test_dedupe_payment_records_by_identity_keeps_different_operations_with_same_filename() -> None:
    first = payment("Платежное_поручение_№133.pdf", payment_date="2026-05-25", invoice="1")
    second = payment("Платежное_поручение_№133.pdf", payment_date="2026-05-25", invoice="2")
    second.amount = "999"
    assert len(dedupe_payment_records_by_identity([first, second])) == 2


def test_payment_history_state_path_is_separate_for_each_mode() -> None:
    from scripts.backfill_payment_history import _state_path_for_mode

    root = Path("staging")
    assert _state_path_for_mode(root, "\u041f\u0421\u041a") == root / "state_psk.json"
    assert _state_path_for_mode(root, "\u0418\u0421") == root / "state_is.json"


def test_build_final_history_matches_unique_invoice_by_amount_when_counterparty_is_bad() -> None:
    row = PaymentRecord(
        "payment.pdf", "2026-06-29", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a",
        '\u041e\u041e\u041e "\u0412\u0421"', "00065", "", "", "", "", "\u041d\u043e\u0432\u044b\u0439 \u0413\u043e\u0440\u043e\u0434", "", "143750",
    )
    source = InvoiceArchiveRecord(
        "2026-06-29 08:23:47", "\u041f\u0421\u041a", "-1", "", "invoice.pdf", "pdf", "\u0420\u0430\u0441\u0445\u043e\u0434",
        "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "", "\u0421\u0447. \u2116 40702810306000006280", "00065", "2026-06-24",
        "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f", "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f", "", "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a", "\u0441\u0442\u0440\u043e\u0439\u043c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b", "143750,00", "\u041e\u043f\u043b\u0430\u0447\u0435\u043d",
        "https://drive/invoice", "mid", "file", "\u041e\u041a",
    )

    final, matched = build_final_history([row], [source], [], [])

    assert matched == 1
    assert final[0].object_name == "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"
    assert final[0].project == "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"
    assert final[0].operation_type == "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"
    assert final[0].invoice_link == "https://drive/invoice"


def test_build_final_history_applies_common_unmatched_payment_fallbacks() -> None:
    loan = PaymentRecord("loan.pdf", "2026-06-29", "", "", "", "\u0418\u041f \u041c\u041e\u0427\u0410\u041b\u041e\u0412 \u041a. \u042e.", "1", "", "", "", "", "\u0412\u044b\u0434\u0430\u0447\u0430 \u0431\u0435\u0441\u043f\u0440\u043e\u0446\u0435\u043d\u0442\u043d\u043e\u0433\u043e \u0437\u0430\u0439\u043c\u0430", "", "100000")
    lease = PaymentRecord("lease.pdf", "2026-06-29", "", "", "", "\u041e\u041e\u041e \u0410\u041b\u042c\u0424\u0410\u041c\u041e\u0411\u0418\u041b\u042c", "LK", "\u0410\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e", "", "\u041b\u0438\u0437\u0438\u043d\u0433", "", "\u043b\u0438\u0437\u0438\u043d\u0433 \u043a\u0440\u0430\u043d\u0430", "", "170902,85")
    dues = PaymentRecord("dues.pdf", "2026-06-29", "", "", "", "\u0410\u0421 \"\u041f\u041e\u041c\u041e\u0429\u042c\"", "79810", "", "", "", "", "\u043e\u043f\u043b\u0430\u0442\u0430 \u0447\u043b\u0435\u043d\u0441\u043a\u0438\u0445 \u0432\u0437\u043d\u043e\u0441\u043e\u0432", "", "8000")

    final, _matched = build_final_history([loan, lease, dues], [], [], [])
    by_name = {record.name: record for record in final}

    assert by_name["loan.pdf"].object_name == "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"
    assert by_name["loan.pdf"].project == "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"
    assert by_name["loan.pdf"].operation_type == "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"
    assert by_name["loan.pdf"].responsible == "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a"
    assert by_name["lease.pdf"].project == "\u041a\u0440\u0430\u043d"
    assert by_name["lease.pdf"].responsible == "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a"
    assert by_name["dues.pdf"].object_name == "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
    assert by_name["dues.pdf"].project == "\u041e\u0444\u0438\u0441"
    assert by_name["dues.pdf"].budget_item == "\u0421\u0420\u041e"


def test_final_history_applies_29_june_classification_fallbacks() -> None:
    conversion = PaymentRecord(
        "atl.pdf", "2026-06-29", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a",
        "\u041e\u041e\u041e \"\u0410\u0422\u041b \u0421\u041f\u0415\u0426\"", "36", "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", "\u041e\u0444\u0438\u0441", "\u0422\u043e\u043f\u043b\u0438\u0432\u043e", "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e.", "\u0422\u0440\u0430\u043d\u0441\u043f\u043e\u0440\u0442\u043d\u044b\u0435 \u0443\u0441\u043b\u0443\u0433\u0438", "", "100000",
    )
    alfamobile = PaymentRecord(
        "alfa.pdf", "2026-06-29", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0431\u0435\u0437 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a",
        "\u041e\u041e\u041e \"\u0410\u041b\u042c\u0424\u0410\u041c\u041e\u0411\u0418\u041b\u042c\"", "LK1774763", "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", "\u041a\u0440\u0430\u043d", "\u041b\u0438\u0437\u0438\u043d\u0433", "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a", "\u043a\u0440\u0430\u043d\u0430 \u043f\u0435\u043d\u0438", "", "7100",
    )
    gaz = PaymentRecord(
        "gaz.pdf", "2026-06-29", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a",
        "\u041e\u041e\u041e \"\u0413\u0410\u0417\u041f\u0420\u041e\u041c\u0411\u0410\u041d\u041a \u0410\u0412\u0422\u041e\u041b\u0418\u0417\u0418\u041d\u0413\"", "\u0414\u041b-371021-25", "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", "\u0424\u041e\u0422", "\u041b\u0438\u0437\u0438\u043d\u0433", "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e.", "15-\u0439 \u041f\u043b\u0430\u0442\u0435\u0436 \u043f\u043e \u0434\u043e\u0433\u043e\u0432\u043e\u0440\u0443 \u043b\u0438\u0437\u0438\u043d\u0433\u0430", "", "104638,34",
    )

    final, _ = build_final_history([conversion, alfamobile, gaz], [], [], [])
    by_name = {record.name: record for record in final}

    assert by_name["atl.pdf"].operation_type == "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"
    assert by_name["atl.pdf"].object_name == "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"
    assert by_name["atl.pdf"].project == "\u041a\u043e\u043d\u0432\u0435\u0440\u0442\u0430\u0446\u0438\u044f"
    assert by_name["atl.pdf"].budget_item == ""
    assert by_name["alfa.pdf"].object_name == "\u0410\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e"
    assert by_name["alfa.pdf"].project == "\u041a\u0440\u0430\u043d"
    assert by_name["alfa.pdf"].budget_item == "\u041b\u0438\u0437\u0438\u043d\u0433"
    assert by_name["gaz.pdf"].project == "\u0424\u041e\u0422"
    assert by_name["gaz.pdf"].budget_item == "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a"
    assert by_name["gaz.pdf"].purpose == "\u043b\u0438\u0437\u0438\u043d\u0433 \u0447\u0430\u043d\u0433\u0430\u043d"


def test_final_history_applies_cash_reference_cleanup_for_nalichnaya() -> None:
    cash_office = PaymentRecord("mid.office", "2026-06-29", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u041d\u0430\u043b\u0438\u0447\u043d\u0430\u044f", "", "", "", "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", "\u043f\u0441\u043a \u043e\u0444\u0438\u0441", "\u041e\u0431\u0435\u0441\u043f\u0435\u0447\u0435\u043d\u0438\u0435 \u043e\u0444\u0438\u0441\u0430", "\u0420\u043e\u0434\u0438\u043d.\u041a", "\u043f\u043e\u0447\u0442\u0430", "", "1000")
    cash_advance = PaymentRecord("mid.advance", "2026-06-29", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u041d\u0430\u043b\u0438\u0447\u043d\u0430\u044f", "", "", "", "\u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u043e", "\u0424\u041e\u0422", "\u0430\u0432\u0430\u043d\u0441 (\u0421\u0443\u0445\u0440\u043e\u0431)", "\u0420\u043e\u0434\u0438\u043d.\u041a", "\u0437\u043f \u0418\u044e\u043b\u044c", "", "30000")
    cash_people = PaymentRecord("mid.people", "2026-06-29", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u041d\u0430\u043b\u0438\u0447\u043d\u0430\u044f", "", "", "", "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", "\u0424\u041e\u0422", "\u041a\u043e\u0441\u0438\u0447\u043a\u0438\u043d", "\u0420\u043e\u0434\u0438\u043d.\u041a", "\u0437\u043f \u041c\u0430\u0439", "", "50000")

    final, _ = build_final_history([], [], [cash_office, cash_advance, cash_people], [])
    by_name = {record.name: record for record in final}

    assert by_name["mid.office"].project == "\u041e\u0444\u0438\u0441"
    assert by_name["mid.advance"].budget_item == ""
    assert by_name["mid.advance"].purpose == "\u0430\u0432\u0430\u043d\u0441 (\u0421\u0443\u0445\u0440\u043e\u0431) \u0437\u043f \u0418\u044e\u043b\u044c"
    assert by_name["mid.people"].budget_item == "\u041a\u043e\u0441\u0438\u0447\u043a\u0438\u043d.\u0410"


def test_final_history_learns_april_cash_fot_cleanup_patterns() -> None:
    salary = PaymentRecord("mid.salary", "2026-04-10", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u041d\u0430\u043b\u0438\u0447\u043d\u0430\u044f", "", "", "", "\u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u043e", "\u0424\u041e\u0422", "\u0417\u0430\u0440\u043f\u043b\u0430\u0442\u0430", "\u0420\u043e\u0434\u0438\u043d.\u041a", "\u0437\u043f \u044f\u043d\u0432\u0430\u0440\u044c", "", "120000")
    person = PaymentRecord("mid.person", "2026-04-10", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u041d\u0430\u043b\u0438\u0447\u043d\u0430\u044f", "", "", "", "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", "\u0424\u041e\u0422", "\u041f\u043e\u0440\u043e\u0437\u043e\u0432", "\u0420\u043e\u0434\u0438\u043d.\u041a", "\u0437\u043f \u043c\u0430\u0440\u0442", "", "30000")
    moved = PaymentRecord("mid.moved", "2026-04-10", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u041d\u0430\u043b\u0438\u0447\u043d\u0430\u044f", "", "", "", "\u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u043e", "\u0424\u041e\u0422", "\u041f\u0435\u0434\u043e\u0440\u0435\u043d\u043a\u043e", "\u0420\u043e\u0434\u0438\u043d.\u041a", "\u0437\u043f \u0444\u0435\u0432\u0440\u0430\u043b\u044c \u043c\u0430\u0440\u0442", "", "106000")

    final, _ = build_final_history([], [], [salary, person, moved], [])
    by_name = {record.name: record for record in final}

    assert by_name["mid.salary"].budget_item == ""
    assert by_name["mid.person"].budget_item == "\u041f\u043e\u0440\u043e\u0437\u043e\u0432 \u041d."
    assert by_name["mid.person"].purpose == "\u0437\u043f \u043c\u0430\u0440\u0442, \u041f\u043e\u0440\u043e\u0437\u043e\u0432 \u041d."
    assert by_name["mid.moved"].budget_item == ""
    assert by_name["mid.moved"].purpose == "\u0437\u043f \u0444\u0435\u0432\u0440\u0430\u043b\u044c \u043c\u0430\u0440\u0442, \u041f\u0435\u0434\u043e\u0440\u0435\u043d\u043a\u043e"


def test_final_history_learns_april_tax_and_official_salary_cleanup() -> None:
    tax = PaymentRecord("tax.pdf", "2026-04-10", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0431\u0435\u0437 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a", "\u0418\u041f \u041c\u043e\u0447\u0430\u043b\u043e\u0432", "\u0431/\u0441\u0447", "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", "\u041d\u0430\u043b\u043e\u0433\u0438", "\u041d\u0430\u043b\u043e\u0433\u0438 \u041d\u0414\u0424\u041b", "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a", "\u041d\u0430\u043b\u043e\u0433\u0438 \u041d\u0414\u0424\u041b", "", "110009")
    salary = PaymentRecord("salary.pdf", "2026-04-10", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0431\u0435\u0437 \u041d\u0414\u0421", "\u0431/\u043d \u0418\u041f \u041c\u043e\u0447\u0430\u043b\u043e\u0432", "\u0418\u041f \u041c\u043e\u0447\u0430\u043b\u043e\u0432", "\u0431/\u0441\u0447", "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", "\u0424\u041e\u0422", "\u041e\u0444\u0438\u0446\u0438\u0430\u043b\u044c\u043d\u0430\u044f \u0417\u041f", "\u041c\u043e\u0447\u0430\u043b\u043e\u0432.\u041a", "\u041e\u0444\u0438\u0446\u0438\u0430\u043b\u044c\u043d\u0430\u044f \u0417\u041f", "", "129643")

    final, _ = build_final_history([tax, salary], [], [], [])
    by_name = {record.name: record for record in final}

    assert by_name["tax.pdf"].counterparty == ""
    assert by_name["tax.pdf"].invoice_number == ""
    assert by_name["tax.pdf"].purpose == "\u043d\u0430\u043b\u043e\u0433\u0438 - 110 009,00"
    assert by_name["salary.pdf"].bank == "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a"
    assert by_name["salary.pdf"].counterparty == ""
    assert by_name["salary.pdf"].invoice_number == ""
    assert by_name["salary.pdf"].purpose == "\u0437\u043f - 129 643,00"


def test_final_history_learns_april_unmatched_invoice_context_from_manual_patterns() -> None:
    alferuk = PaymentRecord("alferuk.pdf", "2026-04-10", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0431\u0435\u0437 \u041d\u0414\u0421", "\u0431/\u043d \u0418\u041f \u041c\u043e\u0447\u0430\u043b\u043e\u0432", "\u0410\u041b\u0424\u0415\u0420\u0423\u041a \u041d\u0418\u041a\u041e\u041b\u0410\u0419 \u0421\u0415\u0420\u0413\u0415\u0415\u0412\u0418\u0427", "28138226", "", "", "", "", "\u042d\u0441\u043a\u0438\u0437 \u043f\u043b\u0430\u043d\u0438\u0440\u043e\u0432\u043e\u043a", "", "10000")
    lebed = PaymentRecord("lebed.pdf", "2026-04-10", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a", "\u0418\u041f \u041b\u0415\u0411\u0415\u0414\u0415\u0412\u0410 \u0412. \u0415.", "74", "", "", "", "", "\u0422\u0440\u0430\u043d\u0441\u043f\u043e\u0440\u0442\u043d\u044b\u0435 \u0443\u0441\u043b\u0443\u0433\u0438", "", "19000")
    sam = PaymentRecord("sam.pdf", "2026-04-10", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a", "\u0421\u0410\u041c\u042b\u0413\u0418\u041d \u0410\u041b\u0415\u041a\u0421\u0410\u041d\u0414\u0420 \u0421\u0415\u0420\u0413\u0415\u0415\u0412\u0418\u0427", "19", "\u0412\u043b\u0430\u0434\u0420\u0443\u0441\u0445\u043e\u043b\u043e\u0434", "\u041a\u041c ( \u041f\u0420 )", "\u041e\u0431\u0435\u0441\u043f\u0435\u0447\u0435\u043d\u0438\u0435 \u043e\u0444\u0438\u0441\u0430", "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e.", "\u0422\u0440\u0430\u043d\u0441\u043f\u043e\u0440\u0442\u043d\u044b\u0435 \u0443\u0441\u043b\u0443\u0433\u0438", "", "19000")

    final, _ = build_final_history([alferuk, lebed, sam], [], [], [])
    by_name = {record.name: record for record in final}

    assert by_name["alferuk.pdf"].object_name == "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
    assert by_name["alferuk.pdf"].project == "\u041f\u0418\u0420"
    assert by_name["alferuk.pdf"].budget_item == "\u0423\u0447\u0430\u0441\u0442\u043e\u043a"
    assert by_name["alferuk.pdf"].responsible == "\u041a\u043e\u0441\u0438\u0447\u043a\u0438\u043d.\u0410"
    assert by_name["lebed.pdf"].object_name == "\u0422\u0443\u043d\u0433\u0443\u0441"
    assert by_name["lebed.pdf"].project == "\u041a\u041c ( \u041f\u0420 )"
    assert by_name["lebed.pdf"].budget_item == "\u0414\u043e\u0441\u0442\u0430\u0432\u043a\u0430"
    assert by_name["lebed.pdf"].purpose == "\u0448\u0430\u043b\u0430\u043d\u0434\u0430"
    assert by_name["sam.pdf"].budget_item == "\u0422\u0435\u0445\u043d\u0438\u043a\u0430"
    assert by_name["sam.pdf"].purpose == "\u0448\u0430\u043b\u0430\u043d\u0434\u0430"



def test_final_history_learns_april_13_transport_invoice_object_patterns() -> None:
    rows = [
        PaymentRecord("l61.pdf", "2026-04-13", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a", "\u0418\u041f \u041b\u0415\u0411\u0415\u0414\u0415\u0412\u0410 \u0412. \u0415.", "61", "", "", "", "", "\u0422\u0440\u0430\u043d\u0441\u043f\u043e\u0440\u0442\u043d\u044b\u0435 \u0443\u0441\u043b\u0443\u0433\u0438", "", "19000"),
        PaymentRecord("l68.pdf", "2026-04-13", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a", "\u0418\u041f \u041b\u0415\u0411\u0415\u0414\u0415\u0412\u0410 \u0412. \u0415.", "68", "\u0412\u043b\u0430\u0434\u0420\u0443\u0441\u0445\u043e\u043b\u043e\u0434", "\u041a\u041c ( \u041f\u0420 )", "\u0422\u0435\u0445\u043d\u0438\u043a\u0430", "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e.", "\u0422\u0440\u0430\u043d\u0441\u043f\u043e\u0440\u0442\u043d\u044b\u0435 \u0443\u0441\u043b\u0443\u0433\u0438", "", "19000"),
        PaymentRecord("l158.pdf", "2026-04-13", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a", "\u0418\u041f \u041b\u0415\u0411\u0415\u0414\u0415\u0412\u0410 \u0412. \u0415.", "158", "\u0410\u043b\u0430\u0440\u043c \u041c\u043e\u0442\u043e\u0440\u0441", "\u041a\u041c ( \u041f\u0420 )", "\u0422\u0435\u0445\u043d\u0438\u043a\u0430", "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e.", "\u0422\u0440\u0430\u043d\u0441\u043f\u043e\u0440\u0442\u043d\u044b\u0435 \u0443\u0441\u043b\u0443\u0433\u0438", "", "19000"),
    ]
    final, _ = build_final_history(rows, [], [], [])
    by_name = {record.name: record for record in final}
    assert by_name["l61.pdf"].object_name == "\u0420\u043e\u0431\u043e\u0434\u0440\u043e\u0438\u0434"
    assert by_name["l68.pdf"].object_name == "\u0422\u0443\u043d\u0433\u0443\u0441"
    assert by_name["l158.pdf"].object_name == "\u0410\u043b\u0430\u0440\u043c \u041c\u043e\u0442\u043e\u0440\u0441"
    for record in by_name.values():
        assert record.project == "\u041a\u041c ( \u041f\u0420 )"
        assert record.budget_item == "\u0414\u043e\u0441\u0442\u0430\u0432\u043a\u0430"
        assert record.responsible == "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e."
        assert record.purpose == "\u0448\u0430\u043b\u0430\u043d\u0434\u0430"


def test_final_history_learns_april_13_debt_and_office_fallbacks() -> None:
    etm = PaymentRecord("etm.pdf", "2026-04-13", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a", "\u0410\u041e \"\u0422\u0414 \"\u042d\u041b\u0415\u041a\u0422\u0420\u041e\u0422\u0415\u0425\u041c\u041e\u041d\u0422\u0410\u0416\"", "\u0431/\u0441\u0447", "", "", "", "", "\u041a\u043b\u0430\u043f\u0430\u043d", "", "24383,38")
    petrovich = PaymentRecord("petrovich.pdf", "2026-04-13", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a", "\u041e\u041e\u041e \"\u0421\u0422\u0414 \"\u041f\u0415\u0422\u0420\u041e\u0412\u0418\u0427\"", "\u0422\u042e\u042d00168834", "", "", "", "", "\u0421\u0442\u0440\u043e\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u0435 \u043c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u044b", "", "37828,48")
    netstore = PaymentRecord("netstore.pdf", "2026-04-13", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0431\u0435\u0437 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a", "\u041e\u041e\u041e \"\u041d\u042d\u0422\u0421\u0422\u041e\u0420\"", "3706/\u0423", "", "", "", "", "\u0417\u0430\u043f\u0440\u0430\u0432\u043a\u0430 \u043a\u0430\u0440\u0442\u0440\u0438\u0434\u0436\u0435\u0439", "", "3000")
    final, _ = build_final_history([etm, petrovich, netstore], [], [], [])
    by_name = {record.name: record for record in final}
    for key in ["etm.pdf", "petrovich.pdf"]:
        assert by_name[key].object_name == "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
        assert by_name[key].project == "\u0414\u043e\u043b\u0433"
        assert by_name[key].budget_item == "\u0412\u043e\u0437\u0432\u0440\u0430\u0442 \u0434\u043e\u043b\u0433\u0430"
        assert by_name[key].responsible == "\u0421\u043e\u043b\u043e\u0432\u0446\u043e\u0432 \u041d."
    assert by_name["etm.pdf"].purpose == "\u042d\u0422\u041c"
    assert by_name["netstore.pdf"].object_name == "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
    assert by_name["netstore.pdf"].project == "\u041e\u0444\u0438\u0441"
    assert by_name["netstore.pdf"].budget_item == "\u041e\u0431\u0435\u0441\u043f\u0435\u0447\u0435\u043d\u0438\u0435 \u043e\u0444\u0438\u0441\u0430"
    assert by_name["netstore.pdf"].responsible == "\u0420\u0430\u0437\u0434\u0440\u043e\u0433\u0438\u043d\u0430.\u0421"


def test_final_history_learns_april_13_material_and_auto_patterns() -> None:
    tool = PaymentRecord("tool.pdf", "2026-04-13", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a", "\u041e\u041e\u041e \"\u0412\u0421\u0415\u0418\u041d\u0421\u0422\u0420\u0423\u041c\u0415\u041d\u0422\u042b.\u0420\u0423\"", "2602-436107-51234", "\u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u043e", "", "\u0410\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e", "\u0421\u043e\u043b\u043e\u0432\u0446\u043e\u0432 \u041d.", "\u0421\u043c\u0435\u0441\u0438\u0442\u0435\u043b\u044c \u0434\u043b\u044f \u0440\u0430\u043a\u043e\u0432\u0438\u043d\u044b", "", "28106")
    autodoc = PaymentRecord("auto.pdf", "2026-04-13", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a", "\u041e\u041e\u041e \"\u0410\u0412\u0422\u041e\u0414\u041e\u041a-\u0421\u0415\u0422\u042c\"", "SP-70013", "", "", "", "", "\u0420\u0430\u0434\u0438\u0430\u0442\u043e\u0440", "", "12479")
    final, _ = build_final_history([tool, autodoc], [], [], [])
    by_name = {record.name: record for record in final}
    assert by_name["tool.pdf"].project == "\u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0435 \u0440\u0430\u0441\u0445\u043e\u0434\u044b"
    assert by_name["tool.pdf"].budget_item == "\u0420\u0430\u0441\u0445\u043e\u0434\u043d\u0438\u043a\u0438"
    assert by_name["autodoc.pdf" if False else "auto.pdf"].object_name == "\u0410\u0432\u0442\u043e\u0445\u043e\u0437\u044f\u0439\u0441\u0442\u0432\u043e"
    assert by_name["auto.pdf"].project == "\u041b\u0438\u0447\u043d\u044b\u0435 \u0430\u0432\u0442\u043e"
    assert by_name["auto.pdf"].budget_item == "\u0420\u0435\u043c\u043e\u043d\u0442/\u0422\u041e"
    assert by_name["auto.pdf"].responsible == "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e."



def test_final_history_learns_april_20_aktek_and_avtokran_patterns() -> None:
    aktek = PaymentRecord("aktek.pdf", "2026-04-20", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a", "\u041e\u041e\u041e \"\u0410\u041a\u0422\u0415\u041a\"", "\u0431/\u0441\u0447", "", "", "", "", "\u043e \u0440\u0430\u0441\u0442\u043e\u0440\u0436\u0435\u043d\u0438\u0438 \u0434\u043e\u0433\u043e\u0432\u043e\u0440\u0430", "", "1007777")
    crane = PaymentRecord("crane.pdf", "2026-04-20", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a", "", "1227", "", "", "", "", "\u0410\u041e \"\u0410\u0412\u0422\u041e\u041a\u0420\u0410\u041d \u0410\u0420\u0415\u041d\u0414\u0410\" \u0410\u0440\u0435\u043d\u0434\u0430 \u043f\u043e\u0434\u044a\u0435\u043c\u043d\u043e\u0433\u043e \u043e\u0431\u043e\u0440\u0443\u0434\u043e\u0432\u0430\u043d\u0438\u044f", "", "140300")
    final, _ = build_final_history([aktek, crane], [], [], [])
    by_name = {record.name: record for record in final}
    assert by_name["aktek.pdf"].invoice_number == "814"
    assert by_name["aktek.pdf"].object_name == "\u0410\u043a\u0442\u0435\u043a"
    assert by_name["aktek.pdf"].project == "\u0412\u043e\u0437\u0432\u0440\u0430\u0442"
    assert by_name["aktek.pdf"].budget_item == "\u0412\u043e\u0437\u0432\u0440\u0430\u0442 \u0434\u043e\u043b\u0433\u0430"
    assert by_name["aktek.pdf"].responsible == "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e."
    assert by_name["crane.pdf"].counterparty == "\u0410\u041e \"\u0410\u0432\u0442\u043e\u043a\u0440\u0430\u043d \u0410\u0440\u0435\u043d\u0434\u0430\""
    assert by_name["crane.pdf"].object_name == "\u041b\u0438\u0434\u0435\u0440\u0421\u0442\u0440\u043e\u0439  (\u041c\u0435\u0442\u0430\u043b\u043b\u043e\u0441\u0442\u0440\u043e\u0439)"
    assert by_name["crane.pdf"].project == "\u041a\u041c ( \u041c )"
    assert by_name["crane.pdf"].budget_item == "\u0422\u0435\u0445\u043d\u0438\u043a\u0430"
    assert by_name["crane.pdf"].responsible == "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e."
    assert by_name["crane.pdf"].purpose == "\u041f\u043e\u0434\u044c\u0435\u043c\u043d\u0438\u043a"


def test_final_history_learns_april_20_cash_fot_person_name_cleanup() -> None:
    rows = [
        PaymentRecord("mironova.cash", "2026-04-20", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u041d\u0430\u043b\u0438\u0447\u043d\u0430\u044f", "", "", "", "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", "\u0424\u041e\u0422", "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430", "\u0420\u043e\u0434\u0438\u043d.\u041a", "\u0437\u043f \u041c\u0430\u0440\u0442", "", "70000"),
        PaymentRecord("solovcov.cash", "2026-04-20", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u041d\u0430\u043b\u0438\u0447\u043d\u0430\u044f", "", "", "", "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", "\u0424\u041e\u0422", "\u0421\u043e\u043b\u043e\u0432\u0446\u043e\u0432", "\u0420\u043e\u0434\u0438\u043d.\u041a", "\u0437\u043f \u043c\u0430\u0440\u0442", "", "70000"),
        PaymentRecord("razd.cash", "2026-04-20", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u041d\u0430\u043b\u0438\u0447\u043d\u0430\u044f", "", "", "", "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a", "\u0424\u041e\u0422", "\u0420\u0430\u0437\u0434\u0440\u043e\u0433\u0438\u043d\u0430", "\u0420\u043e\u0434\u0438\u043d.\u041a", "\u0437\u043f \u043c\u0430\u0440\u0442", "", "36500"),
    ]
    final, _ = build_final_history([], [], rows, [])
    by_name = {record.name: record for record in final}
    assert by_name["mironova.cash"].budget_item == "\u041c\u0438\u0440\u043e\u043d\u043e\u0432\u0430 \u042e."
    assert by_name["solovcov.cash"].budget_item == "\u0421\u043e\u043b\u043e\u0432\u0446\u043e\u0432 \u041d."
    assert by_name["razd.cash"].budget_item == "\u0420\u0430\u0437\u0434\u0440\u043e\u0433\u0438\u043d\u0430.\u0421"



def test_final_history_learns_april_27_office_service_patterns_and_skips_not_to_enter_cash() -> None:
    cdek = PaymentRecord("cdek.pdf", "2026-04-27", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a", "\u041e\u041e\u041e \"\u0421\u0414\u042d\u041a-\u0413\u041b\u041e\u0411\u0410\u041b\"", "\u0413\u041b-01963662", "\u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u043e", "\u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0435 \u0440\u0430\u0441\u0445\u043e\u0434\u044b", "\u041a\u0443\u0440\u044c\u0435\u0440\u0441\u043a\u0430\u044f \u0441\u043b\u0443\u0436\u0431\u0430", "\u0420\u0430\u0437\u0434\u0440\u043e\u0433\u0438\u043d\u0430.\u0421", "\u0423\u0441\u043b\u0443\u0433\u0438 \u0434\u043e\u0441\u0442\u0430\u0432\u043a\u0438", "", "1738,50")
    hh = PaymentRecord("hh.pdf", "2026-04-27", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u0411\u0435\u0437\u043d\u0430\u043b\u0438\u0447\u043d\u044b\u0435 \u0441 \u041d\u0414\u0421", "\u0431/\u043d \u0421\u0431\u0435\u0440\u0431\u0430\u043d\u043a", "\u041e\u041e\u041e \"\u0425\u042d\u0414\u0425\u0410\u041d\u0422\u0415\u0420\"", "9401004/115", "\u0418\u041f \u041f\u043e\u043b\u044f\u043a\u043e\u0432", "\u041a\u041c ( \u041c )", "\u041f\u043e\u0434\u0440\u044f\u0434\u0447\u0438\u043a", "\u0420\u043e\u0434\u0438\u043d.\u041a", "\u0440\u0430\u0431\u043e\u0442\u044b \u0441 \u0443\u0441\u043b\u0443\u0433\u0430\u043c\u0438 HeadHunter", "", "8931")
    skip = PaymentRecord("skip.cash", "2026-04-27", "\u0420\u0430\u0441\u0445\u043e\u0434", "\u041d\u0430\u043b\u0438\u0447\u043d\u0430\u044f", "", "", "", "\u041f\u0440\u043e\u0438\u0437\u0432\u043e\u0434\u0441\u0442\u0432\u043e", "\u0424\u041e\u0422", "", "\u0420\u043e\u0434\u0438\u043d.\u041a", "\u0430\u0432\u0430\u043d\u0441 \u043f\u043e\u043a\u0430 \u043d\u0435 \u0437\u0430\u043d\u043e\u0441\u0438", "", "500000")
    final, _ = build_final_history([cdek, hh], [], [skip], [])
    by_name = {record.name: record for record in final}
    assert "skip.cash" not in by_name
    assert by_name["cdek.pdf"].object_name == "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
    assert by_name["cdek.pdf"].project == "\u041e\u0444\u0438\u0441"
    assert by_name["cdek.pdf"].budget_item == "\u041a\u0443\u0440\u044c\u0435\u0440\u0441\u043a\u0430\u044f \u0441\u043b\u0443\u0436\u0431\u0430"
    assert by_name["hh.pdf"].object_name == "\u041f\u0421\u041a \u041d\u044c\u044e\u0442\u0435\u043a"
    assert by_name["hh.pdf"].project == "\u041d\u0430\u0439\u043c"
    assert by_name["hh.pdf"].budget_item == "HR"
    assert by_name["hh.pdf"].responsible == "\u0420\u0430\u0437\u0434\u0440\u043e\u0433\u0438\u043d\u0430.\u0421"

def test_final_history_learns_may_25_manual_patterns() -> None:
    lawyer = PaymentRecord("lawyer.pdf", "2026-05-25", "Расход", "Безналичные без НДС", "б/н Альфа", "СЕВЕРО-ЗАПАДНЫЙ БАНК ПАО СБЕРБАНК г Санкт-", "16", "", "", "", "", "Оплата по СОГЛАШЕНИЮ №43п об оказании юридической помощи", "", "11300")
    court = PaymentRecord("court.pdf", "2026-05-25", "Расход", "Безналичные без НДС", "б/н Альфа", "ОКЦ № 7 ГУ Банка России по ЦФО//УФК по Тульской", "б/сч", "", "", "", "", "Стройка+", "", "17475")
    restart = PaymentRecord("restart.pdf", "2026-05-25", "Расход", "Безналичные с НДС", "б/н Альфа", "ООО \"ТД \"РЕСТАРТ\"", "ТД-2329", "ПСК Ньютек", "КМ ( ПР )", "Материалы", "Соловцов Н.", "Грунт-Эмаль", "", "311075")
    mainfix = PaymentRecord("mainfix.pdf", "2026-05-25", "Расход", "Безналичные с НДС", "б/н Альфа", "ООО \"КОМПАНИЯ ГЛАВКРЕП\"", "6367", "ИП Егунян", "КМ ( ПР )", "Материалы", "Соловцов Н.", "Болт", "", "75863,27")
    metall = PaymentRecord("metall.pdf", "2026-05-25", "Расход", "Безналичные с НДС", "б/н Альфа", "АО \"МЕТАЛЛОТОРГ\"", "СП15392/1", "", "", "", "", "Труба", "", "1340236")
    autodoc = PaymentRecord("autodoc.pdf", "2026-05-25", "Расход", "Безналичные с НДС", "б/н Альфа", "ООО \"АВТОДОК-СЕТЬ\"", "SP-70013", "Автохозяйство", "Личные авто", "Ремонт/ТО", "Миронова Ю.", "Свеча зажигания", "", "14452")
    pedorenko = PaymentRecord("pedorenko.cash", "2026-05-25", "Расход", "Наличная", "", "", "", "Производство", "ФОТ", "Педоренко", "Родин.К", "зп апрель", "", "61000")

    final, _ = build_final_history([lawyer, court, restart, mainfix, metall, autodoc], [], [pedorenko], [])
    by_name = {record.name: record for record in final}

    assert by_name["lawyer.pdf"].counterparty == "Адвокат Евдокимов Алексей Анатольевич"
    assert by_name["lawyer.pdf"].object_name == "ПСК Ньютек"
    assert by_name["lawyer.pdf"].project == "Инвестиции"
    assert by_name["lawyer.pdf"].budget_item == "Участок"
    assert by_name["lawyer.pdf"].responsible == "Раздрогина.С"
    assert by_name["lawyer.pdf"].purpose == "адвокат"
    assert by_name["court.pdf"].counterparty == "Казначейство России (ФНС России)"
    assert by_name["court.pdf"].purpose == "суд"
    assert by_name["restart.pdf"].object_name == "Техносоюз"
    assert by_name["mainfix.pdf"].object_name == "Техносоюз"
    assert by_name["metall.pdf"].object_name == "ИП Егунян"
    assert by_name["metall.pdf"].project == "КМ ( ПР )"
    assert by_name["metall.pdf"].budget_item == "Материалы"
    assert by_name["metall.pdf"].responsible == "Соловцов Н."
    assert by_name["autodoc.pdf"].operation_type == "Конвертация"
    assert by_name["autodoc.pdf"].object_name == "Конвертация"
    assert by_name["autodoc.pdf"].project == "Конвертация"
    assert by_name["autodoc.pdf"].budget_item == ""
    assert by_name["pedorenko.cash"].object_name == "ПСК Ньютек"
    assert by_name["pedorenko.cash"].budget_item == "Педоренко"
    assert by_name["pedorenko.cash"].purpose == "зп апрель"

def test_final_history_learns_april_30_manual_patterns() -> None:
    mak = PaymentRecord("mak.pdf", "2026-04-30", "Расход", "Безналичные с НДС", "б/н Сбербанк", "ООО \"МАК КАРГО\"", "1", "Конвертация", "Конвертация", "", "Мочалов.К", "Ч/о", "", "400000")
    apm = PaymentRecord("apm.pdf", "2026-04-30", "Расход", "Безналичные с НДС", "б/н Сбербанк", "ООО \"АПМ\"", "ЦБ-235", "Конвертация", "Конвертация", "", "Мочалов.К", "Деталь", "", "58576,80")
    glav = PaymentRecord("glav.pdf", "2026-04-30", "Расход", "Безналичные с НДС", "б/н Сбербанк", "ООО \"КОМПАНИЯ ГЛАВКРЕП\"", "5492", "ВладРусхолод", "КМ ( ПР )", "Расходники", "Соловцов Н.", "Шайба", "", "2388,72")
    trip = PaymentRecord("trip.cash", "2026-04-30", "Расход", "Наличная", "", "", "", "ПСК Ньютек", "Офис", "командировочные расходы", "Косичкин.А", "мск", "", "14306")
    zhilin = PaymentRecord("zhilin.cash", "2026-04-30", "Расход", "Наличная", "", "", "", "ПСК Ньютек", "ФОТ", "Бригада Ж", "Родин.К", "зп апрель", "", "17100")
    work = PaymentRecord("work.cash", "2026-04-30", "Расход", "Наличная", "", "", "", "Антарес", "КМ ( М )", "Разнорабочие", "Родин.К", "дом 2", "", "22500")
    leader = PaymentRecord("leader.cash", "2026-04-30", "Расход", "Наличная", "", "", "", "ЛидерСтрой  (Металлострой)", "Металлострой", "АР", "Мочалов.К", "работа, агенское вознаграждение (Саша Лидерстрой)", "", "700000")
    suhod = PaymentRecord("suhod.cash", "2026-04-30", "Расход", "Наличная", "", "", "", "ПСК Ньютек", "ФОТ", "Суходолин С (МОП)", "Мочалов.К", "аванс зп апрель", "", "30000")

    final, _ = build_final_history([mak, apm, glav], [], [trip, zhilin, work, leader, suhod], [])
    by_name = {record.name: record for record in final}

    assert by_name["mak.pdf"].operation_type == "Конвертация"
    assert by_name["mak.pdf"].responsible == "Родин.К"
    assert by_name["mak.pdf"].purpose == "Мурсал авто"
    assert by_name["apm.pdf"].operation_type == "Конвертация"
    assert by_name["apm.pdf"].purpose == "Евгений Металлопрокат"
    assert by_name["glav.pdf"].purpose == "Метизы"
    assert by_name["trip.cash"].budget_item == "Командировочные"
    assert by_name["trip.cash"].purpose == "Командировочные расходы мск"
    assert by_name["zhilin.cash"].budget_item == "Жилин А."
    assert by_name["zhilin.cash"].purpose == "Бригада Ж  зп апрель"
    assert by_name["work.cash"].purpose == "разнорабочие  дом 2"
    assert by_name["leader.cash"].project == "АР"
    assert by_name["leader.cash"].budget_item == "Агенское вознагрождение"
    assert by_name["leader.cash"].purpose == "Металлострой  работа  агентское"
    assert by_name["suhod.cash"].budget_item == "Суходолин С."

def test_final_history_learns_may_04_manual_patterns() -> None:
    megafon = PaymentRecord("megafon.pdf", "2026-05-04", "Расход", "Безналичные с НДС", "б/н Альфа", "ПАО \"МЕГАФОН\"", "133167607987", "", "", "", "", "Предоплата за услуги связи", "", "16000")
    alfalizing = PaymentRecord("alfa.pdf", "2026-05-04", "Расход", "Безналичные с НДС", "б/н Альфа", "ООО \"АЛЬФАМОБИЛЬ\"", "LK1703724", "Автохозяйство", "Кран", "Лизинг", "Мочалов.К", "лизинг крана", "", "170902,85")
    gaz = PaymentRecord("gaz.pdf", "2026-05-04", "Расход", "Безналичные с НДС", "б/н Альфа", "ООО \"ГАЗПРОМБАНК АВТОЛИЗИНГ\"", "ДЛ-371021-25", "ПСК Ньютек", "ФОТ", "Мочалов.К", "Миронова Ю.", "лизинг чанган", "", "96141,25")
    autoinvest204 = PaymentRecord("auto204.pdf", "2026-05-04", "Расход", "Безналичные с НДС", "б/н Альфа", "ООО \"АВТОИНВЕСТ\"", "204", "ПСК Ньютек", "Офис", "Топливо", "Миронова Ю.", "Услуга по вывозу мусора по Договору №07/Ком-2024 от 13.03.2024г", "", "9600")
    autoinvest218 = PaymentRecord("auto218.pdf", "2026-05-04", "Расход", "Безналичные с НДС", "б/н Альфа", "ООО \"АВТОИНВЕСТ\"", "218", "Производство", "", "Техника", "Миронова Ю.", "Аренда Контейнер 6м3 (за апрель 2026г)", "", "4500")
    yandex = PaymentRecord("yandex.pdf", "2026-05-04", "Расход", "Безналичные без НДС", "б/н Альфа", "ООО \"ЯНДЕКС 360 ДЛЯ БИЗНЕСА\"", "ЛС-5001487617-1", "ПСК Ньютек", "Маркетинг", "АТС", "Гончаров В.", "бизнеса\"", "", "8970")
    prof = PaymentRecord("prof.pdf", "2026-05-04", "Конвертация", "Безналичные с НДС", "б/н Альфа", "ООО \"ПРОФСТРОЙ\"", "12", "Конвертация", "Конвертация", "", "Раздрогина.С", "Изготовление металлоконструкции", "", "500000")
    mak = PaymentRecord("mak-may.pdf", "2026-05-04", "Конвертация", "Безналичные с НДС", "б/н Альфа", "ООО \"МАК КАРГО\"", "1", "Конвертация", "Конвертация", "", "Мочалов.К", "О/о", "", "700000")
    ms = PaymentRecord("ms.pdf", "2026-05-04", "Конвертация", "Безналичные с НДС", "б/н Альфа", "ООО \"МЕТАЛЛСЕРВИС-МОСКВА\"", "1309864", "Конвертация", "Конвертация", "", "Соловцов Н.", "Арматура", "", "1021040")
    furniture = PaymentRecord("furniture.pdf", "2026-05-04", "Конвертация", "Безналичные с НДС", "б/н Альфа", "ООО \"ДОМАШНИЙ ИНТЕРЬЕР\"", "793BJ16947/2", "Конвертация", "Конвертация", "", "Миронова Ю.", "Хоз", "", "160569")

    final, _ = build_final_history([megafon, alfalizing, gaz, autoinvest204, autoinvest218, yandex, prof, mak, ms, furniture], [], [], [])
    by_name = {record.name: record for record in final}

    assert by_name["megafon.pdf"].object_name == "ПСК Ньютек"
    assert by_name["megafon.pdf"].project == "Офис"
    assert by_name["megafon.pdf"].budget_item == "АТС"
    assert by_name["megafon.pdf"].responsible == "Гончаров В."
    assert by_name["alfa.pdf"].responsible == "Родин.К"
    assert by_name["alfa.pdf"].purpose.startswith("Лизинговый платеж Ивановец")
    assert by_name["gaz.pdf"].responsible == "Родин.К"
    assert by_name["gaz.pdf"].purpose == "лизинг чанган ЗП Май"
    assert by_name["auto204.pdf"].object_name == "Производство"
    assert by_name["auto204.pdf"].project == "Производственные расходы"
    assert by_name["auto218.pdf"].project == "Производственные расходы"
    assert by_name["yandex.pdf"].budget_item == "Яндекс бизнес"
    assert by_name["prof.pdf"].responsible == "Косичкин.А"
    assert by_name["mak-may.pdf"].purpose == "Мурсал авто"
    assert by_name["ms.pdf"].purpose == "Арматура Лизан"
    assert by_name["furniture.pdf"].purpose == "Жилин мебель"


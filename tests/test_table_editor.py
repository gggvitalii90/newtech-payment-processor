from payment_processor.models import COLUMNS
from payment_processor.table_editor import COPY_EXCLUDED_COLUMNS, rows_to_tsv


def test_rows_to_tsv_excludes_name_for_transfer() -> None:
    row = [
        "file.pdf",
        "2026-06-09",
        "Расход",
        "Безналичные с НДС",
        "б/н Альфа",
        "ООО Ромашка",
        "15",
        "ПСК Ньютек",
        "Офис",
        "Топливо",
        "Родин.К",
        "Оплата\nпо счету",
        "",
        "1000",
    ]

    copied = rows_to_tsv([row], excluded_columns=COPY_EXCLUDED_COLUMNS)

    assert copied.split("\t")[0] == "2026-06-09"
    assert "file.pdf" not in copied
    assert "Оплата по счету" in copied
    assert len(copied.split("\t")) == len(COLUMNS) - 1


def test_rows_to_tsv_can_include_headers() -> None:
    copied = rows_to_tsv([["file.pdf", "2026-06-09"]], excluded_columns={"Name"}, include_headers=True)

    assert copied.splitlines()[0].startswith("Дата\t")
    assert copied.splitlines()[1].startswith("2026-06-09")

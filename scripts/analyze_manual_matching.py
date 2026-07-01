from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compare_manual_final import (  # noqa: E402
    DEFAULT_MANUAL_SHEETS,
    DEFAULT_MANUAL_SPREADSHEET_ID,
    FINAL_SHEET,
    cents,
    dmy,
    file_id,
    links_equal,
    load_final_rows,
    load_manual_rows,
    soft,
)
from payment_processor.env import load_env  # noqa: E402
from payment_processor.google_api import build_sheets_service, get_credentials, load_google_settings  # noqa: E402


FIELD_INDEXES = [
    (2, "\u0422\u0438\u043f \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u0438"),
    (3, "\u0422\u0438\u043f \u043e\u043f\u043b\u0430\u0442\u044b"),
    (4, "\u0411\u0430\u043d\u043a"),
    (5, "\u041a\u043e\u043d\u0442\u0440\u0430\u0433\u0435\u043d\u0442"),
    (6, "\u041d\u043e\u043c\u0435\u0440 \u0441\u0447\u0435\u0442\u0430"),
    (7, "\u041e\u0431\u044a\u0435\u043a\u0442"),
    (8, "\u041f\u0440\u043e\u0435\u043a\u0442"),
    (9, "\u0421\u0442\u0430\u0442\u044c\u044f \u0431\u044e\u0434\u0436\u0435\u0442\u0430"),
    (10, "\u041e\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0439"),
    (11, "\u041d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435"),
    (12, "\u0421\u0441\u044b\u043b\u043a\u0430"),
]


def _parse_date(value: str) -> date:
    return datetime.strptime(dmy(value), "%d.%m.%Y").date()


def _norm(value: str) -> str:
    return soft(value).replace("С‘", "Рµ")


def _tokens(value: str) -> set[str]:
    return {token for token in re.split(r"\W+", _norm(value)) if len(token) >= 3}


def _token_score(left: str, right: str, weight: int) -> int:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0
    overlap = len(left_tokens & right_tokens)
    total = max(len(left_tokens), len(right_tokens))
    return round(weight * overlap / total)


def _same_text(left: str, right: str) -> bool:
    return _norm(left) == _norm(right)


def row_amount(row: dict) -> str:
    values = row["values"] + [""] * 15
    return cents(values[13])


def row_date(row: dict) -> str:
    values = row["values"] + [""] * 15
    return dmy(values[1])


def row_summary(row: dict) -> str:
    values = row["values"] + [""] * 15
    return " | ".join(str(values[index]).strip() for index in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13])


def diff_fields(manual: dict, final: dict) -> list[str]:
    left_values = manual["values"] + [""] * 15
    right_values = final["values"] + [""] * 15
    fields = []
    for index, name in FIELD_INDEXES:
        left = str(left_values[index] or "").strip()
        right = str(right_values[index] or "").strip()
        if index == 12:
            if links_equal(left, right):
                continue
        elif left == right or _same_text(left, right):
            continue
        fields.append(name)
    return fields


def score_pair(manual: dict, final: dict) -> int:
    left = manual["values"] + [""] * 15
    right = final["values"] + [""] * 15
    score = 0
    if row_date(manual) == row_date(final):
        score += 100
    if row_amount(manual) == row_amount(final) and row_amount(manual):
        score += 120
    if _same_text(left[2], right[2]):
        score += 12
    if _same_text(left[3], right[3]):
        score += 12
    if _same_text(left[4], right[4]):
        score += 8
    if _same_text(left[5], right[5]):
        score += 30
    else:
        score += _token_score(left[5], right[5], 22)
    if _same_text(left[6], right[6]) and str(left[6]).strip():
        score += 25
    elif (
        str(left[6]).strip()
        and str(right[6]).strip()
        and (_norm(left[6]) in _norm(right[6]) or _norm(right[6]) in _norm(left[6]))
    ):
        score += 12
    for index in [7, 8, 9, 10]:
        if _same_text(left[index], right[index]):
            score += 10
    score += _token_score(left[11], right[11], 18)
    if links_equal(left[12], right[12]) and file_id(left[12]) not in {"", "-"}:
        score += 20
    return score


def match_date(manual_rows: list[dict], final_rows: list[dict], target_date: str) -> list[dict]:
    manual = [row for row in manual_rows if row_date(row) == target_date]
    final = [row for row in final_rows if row_date(row) == target_date]
    candidates = []
    for manual_index, manual_row in enumerate(manual):
        for final_index, final_row in enumerate(final):
            score = score_pair(manual_row, final_row)
            if row_amount(manual_row) != row_amount(final_row):
                score -= 80
            candidates.append((score, manual_index, final_index))
    candidates.sort(reverse=True)

    matched_manual = set()
    matched_final = set()
    result = []
    for score, manual_index, final_index in candidates:
        if manual_index in matched_manual or final_index in matched_final:
            continue
        if score < 170:
            continue
        manual_row = manual[manual_index]
        final_row = final[final_index]
        matched_manual.add(manual_index)
        matched_final.add(final_index)
        diffs = diff_fields(manual_row, final_row)
        result.append({
            "date": target_date,
            "status": "matched_with_diffs" if diffs else "matched_exact",
            "score": score,
            "amount": row_amount(manual_row),
            "manual_sheet": manual_row["sheet"],
            "manual_row": manual_row["row"],
            "final_row": final_row["row"],
            "diff_fields": ", ".join(diffs),
            "manual": row_summary(manual_row),
            "final": row_summary(final_row),
        })

    for index, manual_row in enumerate(manual):
        if index in matched_manual:
            continue
        result.append({
            "date": target_date,
            "status": "manual_unmatched",
            "score": "",
            "amount": row_amount(manual_row),
            "manual_sheet": manual_row["sheet"],
            "manual_row": manual_row["row"],
            "final_row": "",
            "diff_fields": "",
            "manual": row_summary(manual_row),
            "final": "",
        })
    for index, final_row in enumerate(final):
        if index in matched_final:
            continue
        result.append({
            "date": target_date,
            "status": "final_unmatched",
            "score": "",
            "amount": row_amount(final_row),
            "manual_sheet": "",
            "manual_row": "",
            "final_row": final_row["row"],
            "diff_fields": "",
            "manual": "",
            "final": row_summary(final_row),
        })
    return sorted(result, key=lambda row: (row["date"], str(row["amount"]), str(row["manual_row"]), str(row["final_row"])))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["date", "status", "score", "amount", "manual_sheet", "manual_row", "final_row", "diff_fields", "manual", "final"]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manual-spreadsheet-id", default=DEFAULT_MANUAL_SPREADSHEET_ID)
    parser.add_argument("--manual-sheet", action="append", dest="manual_sheets")
    parser.add_argument("--date", action="append", dest="dates")
    parser.add_argument("--start", default="2026-04-10")
    parser.add_argument("--end", default="2026-06-30")
    parser.add_argument("--output", default="reports/manual_matching_by_date.csv")
    args = parser.parse_args()

    settings = load_google_settings(load_env())
    sheets = build_sheets_service(get_credentials(settings))
    manual_rows = load_manual_rows(sheets, args.manual_spreadsheet_id, args.manual_sheets or DEFAULT_MANUAL_SHEETS)
    final_rows = load_final_rows(sheets, settings.archive_spreadsheet_id, {"04", "05", "06"})
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    manual_rows = [row for row in manual_rows if start <= _parse_date(row_date(row)) <= end]
    final_rows = [row for row in final_rows if start <= _parse_date(row_date(row)) <= end]
    dates = args.dates or sorted({row_date(row) for row in manual_rows} | {row_date(row) for row in final_rows}, key=_parse_date)

    rows = []
    for target_date in dates:
        rows.extend(match_date(manual_rows, final_rows, target_date))
    write_csv(Path(args.output), rows)

    print(f"manual_rows={len(manual_rows)} final_rows={len(final_rows)} report={args.output}")
    print("statuses", dict(Counter(row["status"] for row in rows)))
    print("dates", len(dates))
    print("sheet", FINAL_SHEET)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


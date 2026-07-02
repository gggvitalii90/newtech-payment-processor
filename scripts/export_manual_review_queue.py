from __future__ import annotations

import argparse
import csv
from pathlib import Path


def u(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


SUMMARY_COLUMNS = [
    u(0x0414,0x0430,0x0442,0x0430),
    u(0x0422,0x0438,0x043f,0x0020,0x043e,0x043f,0x0435,0x0440,0x0430,0x0446,0x0438,0x0438),
    u(0x0422,0x0438,0x043f,0x0020,0x043e,0x043f,0x043b,0x0430,0x0442,0x044b),
    u(0x0411,0x0430,0x043d,0x043a),
    u(0x041a,0x043e,0x043d,0x0442,0x0440,0x0430,0x0433,0x0435,0x043d,0x0442),
    u(0x041d,0x043e,0x043c,0x0435,0x0440,0x0020,0x0441,0x0447,0x0435,0x0442,0x0430),
    u(0x041e,0x0431,0x044a,0x0435,0x043a,0x0442),
    u(0x041f,0x0440,0x043e,0x0435,0x043a,0x0442),
    u(0x0421,0x0442,0x0430,0x0442,0x044c,0x044f,0x0020,0x0431,0x044e,0x0434,0x0436,0x0435,0x0442,0x0430),
    u(0x041e,0x0442,0x0432,0x0435,0x0442,0x0441,0x0442,0x0432,0x0435,0x043d,0x043d,0x044b,0x0439),
    u(0x041d,0x0430,0x0437,0x043d,0x0430,0x0447,0x0435,0x043d,0x0438,0x0435,0x0020,0x043f,0x043b,0x0430,0x0442,0x0435,0x0436,0x0430),
    u(0x0421,0x0443,0x043c,0x043c,0x0430),
]
LINK_FIELD = u(0x0421,0x0441,0x044b,0x043b,0x043a,0x0430)
EXPECTED_FIELD = u(0x043a,0x0430,0x043a,0x0020,0x0434,0x043e,0x043b,0x0436,0x043d,0x043e,0x0020,0x0431,0x044b,0x0442,0x044c)
COMMENT_FIELD = u(0x043a,0x043e,0x043c,0x043c,0x0435,0x043d,0x0442,0x0430,0x0440,0x0438,0x0439)


def split_row(value: str) -> list[str]:
    values = (value or "").split("|")
    return values + [""] * (len(SUMMARY_COLUMNS) - len(values))


def should_include(row: dict[str, str]) -> bool:
    status = row.get("status", "")
    if status in {"manual_unmatched", "final_unmatched"}:
        return True
    fields = {item.strip() for item in row.get("diff_fields", "").split(",") if item.strip()}
    fields.discard(LINK_FIELD)
    return bool(fields) and int(row.get("score") or 0) < 260


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--output", default="reports/manual_review_queue.csv")
    args = parser.parse_args()

    source = Path(args.source)
    rows = list(csv.DictReader(source.open(encoding="utf-8-sig")))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    headers = ["date", "status", "score", "amount", "diff_fields", "manual_row", "final_row"]
    headers += ["manual_" + name for name in SUMMARY_COLUMNS]
    headers += ["final_" + name for name in SUMMARY_COLUMNS]
    headers += [EXPECTED_FIELD, COMMENT_FIELD]

    with output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        count = 0
        for row in rows:
            if not should_include(row):
                continue
            manual = split_row(row.get("manual", ""))
            final = split_row(row.get("final", ""))
            out = {key: row.get(key, "") for key in ["date", "status", "score", "amount", "diff_fields", "manual_row", "final_row"]}
            out.update({"manual_" + name: manual[index] for index, name in enumerate(SUMMARY_COLUMNS)})
            out.update({"final_" + name: final[index] for index, name in enumerate(SUMMARY_COLUMNS)})
            out[EXPECTED_FIELD] = ""
            out[COMMENT_FIELD] = ""
            writer.writerow(out)
            count += 1
    print(f"review_rows={count} report={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

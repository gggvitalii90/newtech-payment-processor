from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payment_processor.dictionaries import load_dictionaries
from payment_processor.fintablo_client import FinTabloClient, FinTabloError, load_fintablo_settings
from payment_processor.fintablo_references import DEFAULT_DEAL_STAGES, fetch_reference_sync_plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Google reference dictionary with FinTablo references")
    parser.add_argument("--apply", action="store_true", help="Reserved for the next phase; currently not supported")
    parser.add_argument("--output", default="reports/fintablo_reference_sync_plan.json")
    args = parser.parse_args()

    if args.apply:
        print("--apply is not implemented yet. Run without --apply for safe dry-run.", file=sys.stderr)
        return 2

    try:
        dictionaries = load_dictionaries(prefer_google=True)
        client = FinTabloClient(load_fintablo_settings())
        plan = fetch_reference_sync_plan(client, dictionaries)
    except FinTabloError as exc:
        print(f"FinTablo error: {exc}", file=sys.stderr)
        return 1

    payload = {
        "mode": "dry-run",
        "default_deal_stages": DEFAULT_DEAL_STAGES,
        "counts": plan.counts(),
        "missing_categories": plan.missing_categories,
        "missing_deals": plan.missing_deals,
        "missing_stages": plan.missing_stages,
        "extra_categories": plan.extra_categories,
        "extra_deals": plan.extra_deals,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"mode": "dry-run", "counts": plan.counts(), "output": str(output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from payment_processor.env import load_env
from payment_processor.google_api import build_drive_service, get_credentials, load_google_settings

MONTHS = {
    1: "\u044f\u043d\u0432\u0430\u0440\u044c", 2: "\u0444\u0435\u0432\u0440\u0430\u043b\u044c", 3: "\u043c\u0430\u0440\u0442", 4: "\u0430\u043f\u0440\u0435\u043b\u044c",
    5: "\u043c\u0430\u0439", 6: "\u0438\u044e\u043d\u044c", 7: "\u0438\u044e\u043b\u044c", 8: "\u0430\u0432\u0433\u0443\u0441\u0442",
    9: "\u0441\u0435\u043d\u0442\u044f\u0431\u0440\u044c", 10: "\u043e\u043a\u0442\u044f\u0431\u0440\u044c", 11: "\u043d\u043e\u044f\u0431\u0440\u044c", 12: "\u0434\u0435\u043a\u0430\u0431\u0440\u044c",
}
VARIANTS = {
    "\u044f\u043d\u0432\u0430\u0440\u044c": 1, "\u044f\u043d\u0432\u0430\u0440\u044f": 1, "\u0444\u0435\u0432\u0440\u0430\u043b\u044c": 2, "\u0444\u0435\u0432\u0440\u0430\u043b\u044f": 2,
    "\u043c\u0430\u0440\u0442": 3, "\u043c\u0430\u0440\u0442\u0430": 3, "\u0430\u043f\u0440\u0435\u043b\u044c": 4, "\u0430\u043f\u0440\u0435\u043b\u044f": 4, "\u043c\u0430\u0439": 5, "\u043c\u0430\u044f": 5,
    "\u0438\u044e\u043d\u044c": 6, "\u0438\u044e\u043d\u044f": 6, "\u0438\u044e\u043b\u044c": 7, "\u0438\u044e\u043b\u044f": 7, "\u0430\u0432\u0433\u0443\u0441\u0442": 8, "\u0430\u0432\u0433\u0443\u0441\u0442\u0430": 8,
    "\u0441\u0435\u043d\u0442\u044f\u0431\u0440\u044c": 9, "\u0441\u0435\u043d\u0442\u044f\u0431\u0440\u044f": 9, "\u0441\u0435\u043d\u044f\u0431\u0440\u044c": 9,
    "\u043e\u043a\u0442\u044f\u0431\u0440\u044c": 10, "\u043e\u043a\u0442\u044f\u0431\u0440\u044f": 10, "\u043d\u043e\u044f\u0431\u0440\u044c": 11, "\u043d\u043e\u044f\u0431\u0440\u044f": 11, "\u043d\u043e\u0431\u044f\u0440\u044c": 11,
    "\u0434\u0435\u043a\u0430\u0431\u0440\u044c": 12, "\u0434\u0435\u043a\u0430\u0431\u0440\u044f": 12,
}
FOLDER_MIME = "application/vnd.google-apps.folder"


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default="reports/drive_archive_tree_before_2026-06-24.json")
    parser.add_argument("--current-year", type=int, default=2026)
    parser.add_argument("--current-month", type=int, default=6)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def month_of(name: str) -> int | None:
    text = name.casefold().replace("_", " ").strip()
    number_match = re.fullmatch(r"0?([1-9]|1[0-2])", text)
    numbers = [int(number_match.group(1))] if number_match else []
    words = [month for word, month in VARIANTS.items() if word in text]
    values = set(numbers + words)
    return next(iter(values)) if len(values) == 1 else None


def canonical_month(month: int) -> str:
    return f"{month:02d} {MONTHS[month]}"


def build_plan(rows: list[dict], current_year: int, current_month: int) -> dict:
    by_id = {row["id"]: row for row in rows}
    children = defaultdict(list)
    for row in rows:
        for parent in row.get("parents", []):
            children[parent].append(row)

    def object_for(row):
        current = row
        while current.get("depth", 0) > 1:
            parents = current.get("parents", [])
            if not parents or parents[0] not in by_id:
                return None
            current = by_id[parents[0]]
        return current if current.get("depth") == 1 else None

    def year_for(row):
        current = row
        while True:
            for parent in current.get("parents", []):
                candidate = by_id.get(parent)
                if candidate and re.fullmatch(r"20\d{2}", candidate["name"].strip()):
                    return int(candidate["name"]), candidate
            parents = current.get("parents", [])
            if not parents or parents[0] not in by_id:
                return None, None
            current = by_id[parents[0]]

    year_groups = defaultdict(list)
    for row in rows:
        if row["mimeType"] != FOLDER_MIME or not re.fullmatch(r"20\d{2}", row["name"].strip()):
            continue
        obj = object_for(row)
        if obj and row.get("parents", [None])[0] == obj["id"]:
            year_groups[(obj["id"], int(row["name"]))].append(row)
    year_folders = {
        key: max(folders, key=lambda folder: len(children.get(folder["id"], [])))
        for key, folders in year_groups.items()
    }

    groups = defaultdict(list)
    unknown = []
    for row in rows:
        if row["mimeType"] != FOLDER_MIME:
            continue
        month = month_of(row["name"])
        if not month:
            continue
        obj = object_for(row)
        if not obj:
            unknown.append({"reason": "no_object", "folder": row})
            continue
        year, year_folder = year_for(row)
        explicit = re.search(r"20\d{2}", row["name"])
        if explicit:
            year = int(explicit.group())
        elif year is None:
            modified_year = int(row["modifiedTime"][:4]) if re.match(r"20\d{2}", row.get("modifiedTime", "")) else None
            year = modified_year
        if not year:
            unknown.append({"reason": "no_year", "folder": row})
            continue
        groups[(obj["id"], year, month)].append(row)

    operations = []
    creates = []
    for key, folders in sorted(year_groups.items(), key=lambda item: item[0]):
        primary = year_folders[key]
        for duplicate in folders:
            if duplicate["id"] == primary["id"]:
                continue
            for child in children.get(duplicate["id"], []):
                operations.append({"action": "move", "id": child["id"], "name": child["name"], "from": duplicate["id"], "to": primary["id"]})
            operations.append({"action": "trash_if_empty", "id": duplicate["id"], "name": duplicate["name"], "object": by_id[key[0]]["name"]})

    for (object_id, year, month), folders in sorted(groups.items(), key=lambda item: item[0]):
        obj = by_id[object_id]
        if year == current_year and month == current_month:
            for folder in folders:
                for child in children.get(folder["id"], []):
                    operations.append({"action": "move", "id": child["id"], "name": child["name"], "from": folder["id"], "to": object_id})
                operations.append({"action": "trash_if_empty", "id": folder["id"], "name": folder["name"], "object": obj["name"]})
            continue

        target_year = year_folders.get((object_id, year))
        if target_year is None:
            create = {"object_id": object_id, "object_name": obj["name"], "year": year}
            if create not in creates:
                creates.append(create)
            target_parent = f"CREATE:{object_id}:{year}"
        else:
            target_parent = target_year["id"]

        canonical = canonical_month(month)
        primary = max(
            folders,
            key=lambda folder: (
                folder["name"] == canonical,
                target_parent in folder.get("parents", []),
                len(children.get(folder["id"], [])),
            ),
        )
        if primary["name"] != canonical:
            operations.append({"action": "rename", "id": primary["id"], "from_name": primary["name"], "to_name": canonical})
        current_parent = primary.get("parents", [""])[0]
        if current_parent != target_parent:
            operations.append({"action": "move", "id": primary["id"], "name": canonical, "from": current_parent, "to": target_parent})
        for duplicate in folders:
            if duplicate["id"] == primary["id"]:
                continue
            for child in children.get(duplicate["id"], []):
                operations.append({"action": "move", "id": child["id"], "name": child["name"], "from": duplicate["id"], "to": primary["id"]})
            operations.append({"action": "trash_if_empty", "id": duplicate["id"], "name": duplicate["name"], "object": obj["name"]})

    return {"creates": creates, "operations": operations, "unknown": unknown}


def execute_plan(plan: dict) -> dict:
    settings = load_google_settings(load_env())
    drive = build_drive_service(get_credentials(settings))
    created = {}
    log = []
    for item in plan["creates"]:
        key = f"CREATE:{item['object_id']}:{item['year']}"
        result = drive.files().create(
            body={"name": str(item["year"]), "mimeType": FOLDER_MIME, "parents": [item["object_id"]]},
            fields="id,name,parents",
        ).execute()
        created[key] = result["id"]
        log.append({"action": "create_year", **item, "result": result})

    result_path = Path("reports/drive_archive_organize_result.json")
    for operation_index, op in enumerate(plan["operations"]):
        try:
            _execute_operation(drive, op, created, log)
        except Exception as exc:
            log.append({**op, "status": "error", "error": str(exc)})
        if operation_index % 10 == 0:
            result_path.write_text(json.dumps({"created": created, "log": log}, ensure_ascii=False, indent=2), encoding="utf-8")
    result_path.write_text(json.dumps({"created": created, "log": log}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"created": created, "log": log}


def _execute_operation(drive, op: dict, created: dict, log: list) -> None:
        if op["action"] == "rename":
            before = drive.files().get(fileId=op["id"], fields="id,name,parents,trashed").execute()
            result = drive.files().update(fileId=op["id"], body={"name": op["to_name"]}, fields="id,name,parents,trashed").execute()
            log.append({**op, "before": before, "result": result})
        elif op["action"] == "move":
            target = created.get(op["to"], op["to"])
            before = drive.files().get(fileId=op["id"], fields="id,name,parents,trashed").execute()
            current_parents = before.get("parents", [])
            if op["from"] not in current_parents:
                log.append({**op, "status": "skipped_parent_changed", "before": before})
                return
            result = drive.files().update(
                fileId=op["id"], addParents=target, removeParents=op["from"], fields="id,name,parents,trashed"
            ).execute()
            log.append({**op, "to_resolved": target, "before": before, "result": result})
        elif op["action"] == "trash_if_empty":
            response = drive.files().list(
                q=f"'{op['id']}' in parents and trashed=false", fields="files(id)", pageSize=2
            ).execute()
            if response.get("files"):
                log.append({**op, "status": "kept_not_empty"})
                return
            before = drive.files().get(fileId=op["id"], fields="id,name,parents,trashed").execute()
            result = drive.files().update(fileId=op["id"], body={"trashed": True}, fields="id,name,parents,trashed").execute()
            log.append({**op, "before": before, "result": result})


def main():
    args = parse_args()
    rows = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    plan = build_plan(rows, args.current_year, args.current_month)
    Path("reports/drive_archive_organize_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "creates": len(plan["creates"]),
        "operations": len(plan["operations"]),
        "renames": sum(op["action"] == "rename" for op in plan["operations"]),
        "moves": sum(op["action"] == "move" for op in plan["operations"]),
        "trash_checks": sum(op["action"] == "trash_if_empty" for op in plan["operations"]),
        "unknown": len(plan["unknown"]),
        "apply": args.apply,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.apply:
        result = execute_plan(plan)
        Path("reports/drive_archive_organize_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"executed": len(result["log"]), "created": len(result["created"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()

import json
import os
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = Path(r"C:\Users\Vitaliy\OneDrive\work\new_tech\_ПП")
DEFAULT_CONFIG_PATH = APP_DIR / "config.json"
DEFAULT_RULES_PATH = APP_DIR / "rules.json"


DEFAULT_CONFIG = {
    "root_folder": str(DEFAULT_ROOT),
    "output_file": str(DEFAULT_ROOT / "result.xlsx"),
    "reference_file": r"C:\Users\Vitaliy\OneDrive\downloads_one_drive\Справочник.xlsx",
    "date_folder_format": "%Y.%m.%d",
    "modes": {
        "ПСК": {"sheet_name": "ПСК", "folder_suffix": ""},
        "ИС": {"sheet_name": "ИС", "folder_suffix": " ИС"},
    },
}


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default.copy()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return _deep_merge(default, data)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    config = load_json(path, DEFAULT_CONFIG)
    env_overrides = {
        "root_folder": os.getenv("NEWTECH_ROOT_FOLDER"),
        "output_file": os.getenv("NEWTECH_OUTPUT_FILE"),
        "reference_file": os.getenv("NEWTECH_REFERENCE_FILE"),
    }
    for key, value in env_overrides.items():
        if value:
            config[key] = value
    return config


def load_rules(path: Path = DEFAULT_RULES_PATH) -> dict:
    return load_json(path, {"account_banks": {}, "classification_rules": []})


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result

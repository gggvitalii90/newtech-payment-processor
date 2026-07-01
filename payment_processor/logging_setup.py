from __future__ import annotations

import logging
from pathlib import Path

from .env import APP_DIR


DEFAULT_LOG_PATH = APP_DIR / "logs" / "payment_processor.log"


def configure_logging(log_path: Path = DEFAULT_LOG_PATH) -> Path:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == log_path for handler in root.handlers):
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    return log_path

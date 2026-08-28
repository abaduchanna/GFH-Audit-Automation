"""Runtime paths. Everything the app writes lives in one portable directory
next to the repository/checkout (or next to the frozen executable)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _choose_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_DIR: Path = _choose_app_dir()
DB_PATH: Path = APP_DIR / "inventory_audit.sqlite3"
IMAGE_DIR: Path = APP_DIR / "whatsapp_images"
EXPORT_DIR: Path = APP_DIR / "exports"
DOWNLOAD_DIR: Path = APP_DIR / "portal_downloads"
OCR_DEBUG_DIR: Path = APP_DIR / "ocr_debug"
CONFIG_PATH: Path = APP_DIR / "gfh_audit_config.json"
STORE_CONFIG_PATH: Path = APP_DIR / "store_master_list.csv"
WHATSAPP_PROFILE_DIR: Path = APP_DIR / "whatsapp_web_profile"
LOG_DIR: Path = APP_DIR / "logs"


def ensure_runtime_dirs() -> None:
    for path in (APP_DIR, IMAGE_DIR, EXPORT_DIR, DOWNLOAD_DIR, OCR_DEBUG_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)

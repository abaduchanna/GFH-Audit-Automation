"""Central configuration + credential storage.

Credentials for the Timesheet portal and the BRS count-sheet portal are kept
in a JSON file inside APP_DIR with restricted permissions (0600 on POSIX).
Secrets are stored base64-obfuscated so they are not sitting in the file as
readable plaintext, and the file itself is excluded from any git tree.
"""
from __future__ import annotations

import base64
import json
import os
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Optional

from .paths import CONFIG_PATH

CONFIG_VERSION = 2


def _obfuscate(value: str) -> str:
    if not value:
        return ""
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _deobfuscate(value: str) -> str:
    if not value:
        return ""
    try:
        return base64.b64decode(value.encode("ascii")).decode("utf-8")
    except Exception:
        return value  # tolerate legacy plain-text values


@dataclass
class PortalCredentials:
    email: str = ""
    password: str = ""

    def is_configured(self) -> bool:
        return bool(self.email.strip() and self.password.strip())


@dataclass
class ReminderSettings:
    """Reminder cadence for the district audit state machine."""

    reminder_interval_minutes: int = 5          # gap between reminder 1..3
    max_reminders: int = 3
    audit_timeout_minutes: int = 15             # "complete the audit within 15 minutes"
    final_notice_after_last_reminder: bool = True


@dataclass
class EngineSettings:
    poll_interval_seconds: int = 10             # WhatsApp Web message poll cadence
    group_load_wait_seconds: int = 25           # max wait for a group conversation to open
    session_wait_seconds: int = 180             # max wait for WhatsApp Web QR login
    send_retry_attempts: int = 3
    send_retry_delay_seconds: int = 5
    scraper_login_wait_seconds: int = 20
    headless_scrapers: bool = False             # keep False: portals often need visible nav
    ocr_min_confidence: int = 30                # tesseract word confidence floor
    ocr_language: str = "eng"


@dataclass
class DistrictScheduleEntry:
    district: str
    whatsapp_group: str = ""
    start_time: str = ""        # "HH:MM" 24h local time; empty == manual start only
    enabled: bool = True


@dataclass
class AppConfig:
    version: int = CONFIG_VERSION
    timesheet: PortalCredentials = field(default_factory=PortalCredentials)
    brs: PortalCredentials = field(default_factory=PortalCredentials)
    reminders: ReminderSettings = field(default_factory=ReminderSettings)
    engine: EngineSettings = field(default_factory=EngineSettings)
    schedule: Dict[str, DistrictScheduleEntry] = field(default_factory=dict)
    tesseract_path: str = ""
    ghostscript_path: str = ""
    whatsapp_browser: str = "edge"               # "chrome" | "edge"
    # Edge attach mode (VidaPay Transfer Bot pattern): a real Edge window is
    # launched with --remote-debugging-port and Selenium attaches to it.
    edge_attach: bool = True
    edge_debug_port: int = 9226
    edge_profile_dir: str = r"C:\GFH_Edge_Automation_Profile"

    # -- serialisation ------------------------------------------------------
    def to_dict(self) -> dict:
        data = asdict(self)
        data["timesheet"]["password"] = _obfuscate(self.timesheet.password)
        data["brs"]["password"] = _obfuscate(self.brs.password)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        cfg = cls()

        def _portal(key: str) -> PortalCredentials:
            raw = (data or {}).get(key, {}) or {}
            return PortalCredentials(
                email=str(raw.get("email", "") or ""),
                password=_deobfuscate(str(raw.get("password", "") or "")),
            )

        cfg.timesheet = _portal("timesheet")
        cfg.brs = _portal("brs")

        rem = (data or {}).get("reminders", {}) or {}
        cfg.reminders = ReminderSettings(
            reminder_interval_minutes=int(rem.get("reminder_interval_minutes", 5) or 5),
            max_reminders=int(rem.get("max_reminders", 3) or 3),
            audit_timeout_minutes=int(rem.get("audit_timeout_minutes", 15) or 15),
            final_notice_after_last_reminder=bool(rem.get("final_notice_after_last_reminder", True)),
        )

        eng = (data or {}).get("engine", {}) or {}
        cfg.engine = EngineSettings(
            poll_interval_seconds=int(eng.get("poll_interval_seconds", 10) or 10),
            group_load_wait_seconds=int(eng.get("group_load_wait_seconds", 25) or 25),
            session_wait_seconds=int(eng.get("session_wait_seconds", 180) or 180),
            send_retry_attempts=int(eng.get("send_retry_attempts", 3) or 3),
            send_retry_delay_seconds=int(eng.get("send_retry_delay_seconds", 5) or 5),
            scraper_login_wait_seconds=int(eng.get("scraper_login_wait_seconds", 20) or 20),
            headless_scrapers=bool(eng.get("headless_scrapers", False)),
            ocr_min_confidence=int(eng.get("ocr_min_confidence", 30) or 30),
            ocr_language=str(eng.get("ocr_language", "eng") or "eng"),
        )

        cfg.schedule = {}
        for district, raw in ((data or {}).get("schedule", {}) or {}).items():
            cfg.schedule[district] = DistrictScheduleEntry(
                district=str(raw.get("district", district) or district),
                whatsapp_group=str(raw.get("whatsapp_group", "") or ""),
                start_time=str(raw.get("start_time", "") or ""),
                enabled=bool(raw.get("enabled", True)),
            )

        cfg.tesseract_path = str((data or {}).get("tesseract_path", "") or "")
        cfg.ghostscript_path = str((data or {}).get("ghostscript_path", "") or "")
        cfg.whatsapp_browser = str((data or {}).get("whatsapp_browser", "edge") or "edge")
        cfg.edge_attach = bool((data or {}).get("edge_attach", True))
        try:
            cfg.edge_debug_port = int((data or {}).get("edge_debug_port", 9226) or 9226)
        except (TypeError, ValueError):
            cfg.edge_debug_port = 9226
        cfg.edge_profile_dir = str((data or {}).get("edge_profile_dir", "") or r"C:\GFH_Edge_Automation_Profile")
        return cfg


class ConfigStore:
    """Thread-safe load/save of :class:`AppConfig` to CONFIG_PATH."""

    def __init__(self, path: Path = CONFIG_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> AppConfig:
        with self._lock:
            if not self.path.exists():
                return AppConfig()
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                return AppConfig.from_dict(data)
            except Exception:
                return AppConfig()

    def save(self, cfg: AppConfig) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            self._restrict_permissions()

    def _restrict_permissions(self) -> None:
        try:
            if os.name == "posix":
                os.chmod(self.path, 0o600)
        except Exception:
            pass

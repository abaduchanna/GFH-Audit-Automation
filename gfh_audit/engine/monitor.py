"""Continuous WhatsApp Web monitor — watches active district groups, OCRs new
images and auto-clears matching pending variances in real time."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..database import VarianceDatabase
from ..models import VarianceRow
from ..ocr.engine import OcrEngine, match_imei_against_variances
from ..paths import IMAGE_DIR
from ..whatsapp.whatsapp_web import WhatsAppMessage, WhatsAppWeb

logger = logging.getLogger("gfh.audit.engine.monitor")


@dataclass
class MonitorEvent:
    kind: str          # "ocr_match" | "ocr_miss" | "text_message" | "error"
    district: str
    group: str
    detail: str = ""
    cleared_keys: List[str] = field(default_factory=list)


class WhatsAppMonitor:
    """Polls each active district group for new messages and processes images."""

    def __init__(
        self,
        whatsapp: WhatsAppWeb,
        db: VarianceDatabase,
        ocr: OcrEngine,
        poll_interval: int = 10,
        on_event: Optional[Callable[[MonitorEvent], None]] = None,
    ):
        self.whatsapp = whatsapp
        self.db = db
        self.ocr = ocr
        self.poll_interval = max(3, poll_interval)
        self.on_event = on_event
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._group_lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="gfh-monitor", daemon=True)
        self._thread.start()
        logger.info("WhatsApp monitor loop started (interval=%ss)", self.poll_interval)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=15)
        logger.info("WhatsApp monitor loop stopped")

    # -- loop ---------------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                logger.error("Monitor poll error: %s", exc)
                self._emit(MonitorEvent(kind="error", district="", group="", detail=str(exc)))
            self._stop_event.wait(self.poll_interval)

    def _poll_once(self) -> None:
        active = self._active_groups()
        if not active:
            return
        targets = self._select_target_groups(dict(active))
        for district, group in targets:
            if self._stop_event.is_set():
                return
            if not self.whatsapp.open_group(group, wait_seconds=15):
                logger.debug("Could not open group %s - skipping this cycle", group)
                continue
            messages = self.whatsapp.fetch_new_messages(group)
            for message in messages:
                self._process_message(district, group, message)

    def _select_target_groups(self, active: Dict[str, str]) -> List[tuple]:
        """VidaPay pattern: read the WhatsApp unread badges first and only
        open groups that actually have new messages. Falls back to scanning
        every active group when the badge check is unavailable."""
        try:
            unread = self.whatsapp.check_group_notifications()
        except Exception as exc:
            logger.debug("Badge check unavailable: %s", exc)
            unread = None
        if unread is None:
            return list(active.items())
        if not unread:
            return []
        unread_set = {name.strip().lower() for name in unread}
        targets = [
            (district, group)
            for district, group in active.items()
            if group.strip().lower() in unread_set
        ]
        if targets:
            logger.info("Unread badges in: %s", [g for _, g in targets])
        return targets

    # -- group registry ---------------------------------------------------------------
    def _active_groups(self) -> List[tuple]:
        with self._group_lock:
            return list(self._active_groups_map.items())

    _active_groups_map: Dict[str, str] = {}

    def set_active_groups(self, groups: Dict[str, str]) -> None:
        """district -> whatsapp group name."""
        with self._group_lock:
            WhatsAppMonitor._active_groups_map = dict(groups)
        logger.info("Monitor active groups: %s", list(groups.keys()))

    # -- message processing ---------------------------------------------------------------
    def _process_message(self, district: str, group: str, message: WhatsAppMessage) -> None:
        if message.has_image:
            self._process_image_message(district, group, message)
        elif message.text.strip():
            self._emit(MonitorEvent(kind="text_message", district=district, group=group,
                                    detail=f"{message.sender}: {message.text[:80]}"))

    def _process_image_message(self, district: str, group: str, message: WhatsAppMessage) -> None:
        sender = message.sender or "unknown"

        # --- duplicate-scan guard: never OCR the same image/message twice ---
        if message.message_id and self.db.is_message_processed(message.message_id):
            logger.debug("Skipping already-processed message %s", message.message_id)
            return

        logger.info("Image message in %s from %s — running OCR", group, sender)
        try:
            data = self.whatsapp.download_message_image(message)
            if not data:
                self._emit(MonitorEvent(kind="ocr_miss", district=district, group=group,
                                        detail=f"Could not download image from {sender}"))
                return  # not marked processed - retried while still visible

            # keep a copy for audit purposes
            stamp = time.strftime("%Y%m%d_%H%M%S")
            safe_group = "".join(c if c.isalnum() else "_" for c in group)[:40]
            saved_path = IMAGE_DIR / f"incoming_{safe_group}_{stamp}.png"
            try:
                saved_path.write_bytes(data)
            except Exception:
                saved_path = None

            if not self.ocr.available:
                self._emit(MonitorEvent(kind="ocr_miss", district=district, group=group,
                                        detail="OCR unavailable (Tesseract missing) — image saved but not processed"))
                return

            result = self.ocr.extract_from_bytes(data, f"{group}_{sender}_{stamp}")
            if not result.ok:
                self._emit(MonitorEvent(kind="ocr_miss", district=district, group=group,
                                        detail=f"OCR error: {result.error}"))
                return

            # processed successfully (match or clean miss) - record it so the
            # same image is never downloaded/OCR'd again (survives restarts)
            self.db.mark_message_processed(message.message_id, district, group,
                                           result.imeis)

            pending = self.db.rows(include_cleared=False, district=district)
            if not pending:
                pending = self.db.rows(include_cleared=False)

            cleared_any = False
            matched_desc: List[str] = []
            for imei in dict.fromkeys(result.imeis):  # dedupe, order preserved
                if not imei:
                    continue
                matches = match_imei_against_variances(imei, pending)
                for row in matches:
                    keys = self.db.clear_by_imei(row.imei, district=district or "", via="ocr")
                    if keys:
                        cleared_any = True
                        matched_desc.append(row.imei)
                        pending = [p for p in pending if p.key not in keys]
                        self._emit(MonitorEvent(
                            kind="ocr_match", district=district, group=group,
                            detail=(f"IMEI {imei} from {sender} cleared variance "
                                    f"{row.store} / {row.product}"),
                            cleared_keys=keys,
                        ))
                        self.db.log_event(district, "ocr_cleared",
                                          f"imei={imei} keys={keys}")

            if not cleared_any:
                found = ", ".join(result.imeis[:6]) if result.imeis else "no 15-digit IMEIs"
                self._emit(MonitorEvent(
                    kind="ocr_miss", district=district, group=group,
                    detail=f"OCR from {sender}: {found} — no pending variance match",
                ))
        except Exception as exc:
            logger.exception("Image processing failed in %s", group)
            self._emit(MonitorEvent(kind="error", district=district, group=group,
                                    detail=f"Image processing failed: {exc}"))

    def _emit(self, event: MonitorEvent) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass

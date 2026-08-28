"""District scheduler — queues districts and fires each workflow when its
configured Audit Start Time is reached (or immediately for 'Start now')."""
from __future__ import annotations

import datetime as dt
import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..config import DistrictScheduleEntry
from ..textutils import parse_start_time

logger = logging.getLogger("gfh.audit.engine.scheduler")


@dataclass
class QueuedDistrict:
    district: str
    group_name: str
    start_at: Optional[dt.datetime] = None
    fired: bool = False
    skipped: bool = False

    def label(self) -> str:
        when = self.start_at.strftime("%Y-%m-%d %H:%M") if self.start_at else "immediate"
        return f"{self.district} @ {when}"


class DistrictScheduler:
    """Watches the queue on a background thread and fires districts on time."""

    def __init__(self, on_fire: Callable[[QueuedDistrict], None], tick_seconds: float = 5.0):
        self.on_fire = on_fire
        self.tick_seconds = tick_seconds
        self.queue: List[QueuedDistrict] = []
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- queue management -----------------------------------------------------
    def build_queue(self, entries: List[DistrictScheduleEntry], now: Optional[dt.datetime] = None) -> List[QueuedDistrict]:
        """Create the queue from schedule entries; times today in the past stay
        queued for 'immediate fire' to avoid silently skipping districts."""
        now = now or dt.datetime.now()
        queued: List[QueuedDistrict] = []
        for entry in entries:
            if not entry.enabled:
                logger.info("District %s disabled in schedule — skipping", entry.district)
                continue
            start_time = parse_start_time(entry.start_time)
            start_at: Optional[dt.datetime] = None
            if start_time is not None:
                start_at = now.replace(hour=start_time.hour, minute=start_time.minute, second=0, microsecond=0)
            queued.append(
                QueuedDistrict(district=entry.district, group_name=entry.whatsapp_group, start_at=start_at)
            )
        with self._lock:
            self.queue = queued
        logger.info("Scheduler queue built: %s", [q.label() for q in queued])
        return queued

    def queue_snapshot(self) -> List[QueuedDistrict]:
        with self._lock:
            return list(self.queue)

    def fire_all_now(self) -> None:
        """Manual 'start everything immediately' (UI Start without schedule)."""
        with self._lock:
            for item in self.queue:
                if not item.fired:
                    item.start_at = None
        logger.info("Scheduler: all pending districts set to immediate fire")

    def fire_district_now(self, district: str) -> bool:
        with self._lock:
            for item in self.queue:
                if item.district == district and not item.fired:
                    item.start_at = None
                    return True
        return False

    # -- loop -------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="gfh-scheduler", daemon=True)
        self._thread.start()
        logger.info("Scheduler loop started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Scheduler loop stopped")

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:
                logger.error("Scheduler tick error: %s", exc)
            self._stop_event.wait(self.tick_seconds)

    def _tick(self) -> None:
        now = dt.datetime.now()
        with self._lock:
            for item in self.queue:
                if item.fired or item.skipped:
                    continue
                if item.start_at is None or now >= item.start_at:
                    item.fired = True
                    logger.info("Scheduler firing district: %s", item.label())
                    try:
                        self.on_fire(item)
                    except Exception as exc:
                        logger.error("District fire callback failed for %s: %s", item.district, exc)

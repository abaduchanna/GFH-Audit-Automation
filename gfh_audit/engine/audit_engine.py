"""AuditEngine — the orchestrator.

Flow (per district, fired by the scheduler):
  1. Scrape Timesheet portal + BRS count sheet (with credentials from config)
  2. Load / cross-reference both reports and compute variances
  3. Send kickoff text tagging active reps ("Audit is open... 15 minutes")
  4. Post variance breakdown image + @phone mention line
  5. Reminders 1..3 every N minutes, then Final Notice — driven by the FSM
  6. Auto-complete when every variance is cleared (OCR / manual)

A continuous WhatsApp monitor watches all active groups for photo replies
and clears matched IMEIs in real time. Everything runs until the UI Stop
button is pressed or all districts reach a terminal state."""
from __future__ import annotations

import logging
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..config import AppConfig
from ..database import VarianceDatabase
from ..models import VarianceRow
from ..ocr.engine import OcrEngine
from ..paths import DOWNLOAD_DIR, EXPORT_DIR, WHATSAPP_PROFILE_DIR
from ..renderer import ImageRenderer
from ..scrapers.brs_portal import BRSCountSheetScraper
from ..scrapers.base import PortalScraperError
from ..scrapers.timesheet_portal import TimesheetPortalScraper
from ..whatsapp.driver_manager import DriverManager
from ..whatsapp.mentions import MentionResolver
from ..whatsapp.whatsapp_web import WhatsAppWeb
from .monitor import MonitorEvent, WhatsAppMonitor
from .scheduler import DistrictScheduler, QueuedDistrict
from .state_machine import DistrictAuditFSM, DistrictAuditState, DistrictState

logger = logging.getLogger("gfh.audit.engine")


class AuditEngine:
    def __init__(self, db: VarianceDatabase, config: AppConfig, log_callback: Optional[Callable[[str], None]] = None):
        self.db = db
        self.config = config
        self.log_callback = log_callback
        self.stop_event = threading.Event()
        self._engine_thread: Optional[threading.Thread] = None
        self.dm: Optional[DriverManager] = None
        self.whatsapp: Optional[WhatsAppWeb] = None
        self.ocr: Optional[OcrEngine] = None
        self.monitor: Optional[WhatsAppMonitor] = None
        self.scheduler: Optional[DistrictScheduler] = None
        self.fsms: Dict[str, DistrictAuditFSM] = {}
        self.variances_by_district: Dict[str, List[VarianceRow]] = {}
        self._district_threads: List[threading.Thread] = []
        self._active_lock = threading.RLock()

    # -- logging -----------------------------------------------------------------
    def log(self, message: str) -> None:
        logger.info(message)
        if self.log_callback:
            try:
                self.log_callback(message)
            except Exception:
                pass

    # -- lifecycle -----------------------------------------------------------------
    def start(self, manual_start_all: bool = False) -> bool:
        if self._engine_thread and self._engine_thread.is_alive():
            self.log("Engine already running")
            return False
        self.stop_event.clear()
        self._engine_thread = threading.Thread(
            target=self._run, args=(manual_start_all,), name="gfh-engine", daemon=True
        )
        self._engine_thread.start()
        return True

    def stop(self) -> None:
        self.log("Stop requested — shutting down engine loops...")
        self.stop_event.set()
        if self.scheduler:
            try:
                self.scheduler.stop()
            except Exception:
                pass
        if self.monitor:
            try:
                self.monitor.stop()
            except Exception:
                pass
        # browser is closed but profile cookies persist → no QR rescan next time
        if self.dm:
            try:
                self.dm.quit()
            except Exception:
                pass
        self.log("Engine stopped")

    def is_running(self) -> bool:
        return bool(self._engine_thread and self._engine_thread.is_alive())

    # -- main run -----------------------------------------------------------------
    def _run(self, manual_start_all: bool) -> None:
        try:
            self.log("=== GFH Audit Automation engine starting ===")
            self._initialise_components()

            entries = [e for e in self.config.schedule.values() if e.enabled]
            if not entries:
                self.log("No enabled districts in the schedule — nothing to do")
                return

            self.scheduler.build_queue(entries)
            if manual_start_all:
                self.scheduler.fire_all_now()
            self.scheduler.start()

            self._monitor_loop_until_done()
        except Exception as exc:
            logger.error("Engine run failed: %s\n%s", exc, traceback.format_exc())
            self.log(f"ENGINE ERROR: {exc}")
        finally:
            self.log("=== Engine run finished ===")

    def _initialise_components(self) -> None:
        self.log("Initialising browser (persistent WhatsApp Web profile)...")
        self.dm = DriverManager(
            profile_dir=WHATSAPP_PROFILE_DIR,
            browser=self.config.whatsapp_browser,
            headless=False,  # WhatsApp Web QR/first login needs a visible window
        )
        if not self.dm.initialize():
            raise RuntimeError("Browser driver could not be initialised")
        self.whatsapp = WhatsAppWeb(self.dm, status_callback=self.log)

        if not self.whatsapp.open_session(wait_seconds=self.config.engine.session_wait_seconds):
            raise RuntimeError(
                "WhatsApp Web session could not be established within the timeout — "
                "scan the QR code in the opened browser window and try again"
            )

        self.ocr = OcrEngine(
            tesseract_path=self.config.tesseract_path,
            ghostscript_path=self.config.ghostscript_path,
            language=self.config.engine.ocr_language,
        )
        if not self.ocr.available:
            self.log("WARNING: Tesseract OCR not available — image clearing disabled")
        if not self.ocr.ghostscript_path:
            self.log("WARNING: Ghostscript not available — PDF images cannot be OCR'd")

        self.monitor = WhatsAppMonitor(
            whatsapp=self.whatsapp,
            db=self.db,
            ocr=self.ocr,
            poll_interval=self.config.engine.poll_interval_seconds,
            on_event=self._on_monitor_event,
        )

        self.scheduler = DistrictScheduler(on_fire=self._on_district_fired)

    def _monitor_loop_until_done(self) -> None:
        self.monitor.start()
        reminder_interval = self.config.reminders.reminder_interval_minutes
        max_reminders = self.config.reminders.max_reminders

        while not self.stop_event.is_set():
            # progress updates + early completion
            for district, fsm in list(self.fsms.items()):
                if fsm.state.is_active:
                    pending = self.db.rows(include_cleared=False, district=district)
                    total = self.variances_by_district.get(district, [])
                    cleared = len(total) - len(pending)
                    fsm.register_progress(len(total), cleared)
                    self.db.update_district_run(
                        district, total_variances=len(total), cleared_variances=cleared
                    )
                    fsm.maybe_complete_from_progress()
                if fsm.state.is_active and fsm.reminder_due():
                    fsm.fire_reminder()

            if self.fsms and all(f.state.is_terminal for f in self.fsms.values()):
                self.log("All districts reached a terminal state — audit complete")
                break
            if not self.scheduler.queue_snapshot() and not self.fsms:
                # nothing queued and nothing running
                break
            self.stop_event.wait(5)

        time.sleep(2)
        self.monitor.stop()
        self.scheduler.stop()
        self.dm.quit()

    # -- district workflow ------------------------------------------------------------
    def _on_district_fired(self, queued: QueuedDistrict) -> None:
        thread = threading.Thread(
            target=self._run_district_workflow, args=(queued,), daemon=True,
            name=f"gfh-district-{queued.district}",
        )
        self._district_threads.append(thread)
        thread.start()

    def _run_district_workflow(self, queued: QueuedDistrict) -> None:
        district = queued.district
        group = queued.group_name or f"GFH TELECOM {district.upper()}"
        self.log(f"--- District workflow starting: {district} (group: {group}) ---")
        self.db.set_district_state(district, DistrictState.PENDING.value)
        self.db.log_event(district, "workflow_started", group)

        try:
            # 1) portal extraction -----------------------------------------------------
            timesheet_records, count_sheet_records = self._extract_portal_data(district)

            # 2) cross-reference both reports into the audit engine -------------------
            variances = self._compute_variances(district, timesheet_records, count_sheet_records)
            self.variances_by_district[district] = variances
            self.db.update_district_run(district, total_variances=len(variances))
            self.log(f"{district}: {len(variances)} variance(s) computed")

            # 3) state machine wiring --------------------------------------------------
            fsm = self._make_fsm(district, group, variances)
            with self._active_lock:
                self.fsms[district] = fsm
                self.monitor.set_active_groups(
                    {d: f.group_name or f"GFH TELECOM {d.upper()}" for d, f in self.fsms.items() if f.state.is_active}
                )

            fsm.start()
        except PortalScraperError as exc:
            self.log(f"PORTAL ERROR [{district}]: {exc}")
            self.db.set_district_state(district, DistrictState.FAILED.value)
            self.db.log_event(district, "portal_error", str(exc))
            fsm = self._make_fsm(district, group, [])
            fsm.fail(str(exc))
            with self._active_lock:
                self.fsms[district] = fsm
        except Exception as exc:
            logger.exception("District workflow failed: %s", district)
            self.log(f"DISTRICT ERROR [{district}]: {exc}")
            self.db.set_district_state(district, DistrictState.FAILED.value)
            self.db.log_event(district, "workflow_error", str(exc))
            fsm = self._make_fsm(district, group, [])
            fsm.fail(str(exc))
            with self._active_lock:
                self.fsms[district] = fsm

    # -- portal extraction ---------------------------------------------------------------
    def _extract_portal_data(self, district: str) -> tuple:
        ts_cfg = self.config.timesheet
        brs_cfg = self.config.brs

        timesheet_records: List[dict] = []
        count_sheet_records: List[dict] = []

        if ts_cfg.is_configured():
            self.log(f"[{district}] Logging into Timesheet portal...")
            try:
                scraper = TimesheetPortalScraper(
                    self.dm, ts_cfg.email, ts_cfg.password,
                    login_wait=self.config.engine.scraper_login_wait_seconds,
                )
                result = scraper.extract_for_district(district, DOWNLOAD_DIR)
                timesheet_records = result["records"]
                if result.get("file_path"):
                    self.db.save_portal_report(district, "timesheet", result["file_path"], "download")
                self.log(f"[{district}] Timesheet rows extracted: {len(timesheet_records)}")
            except PortalScraperError as exc:
                self.log(f"[{district}] Timesheet portal failed: {exc} — continuing with manual data")
        else:
            self.log(f"[{district}] Timesheet credentials not configured — skipping portal scrape")

        if brs_cfg.is_configured():
            self.log(f"[{district}] Logging into BRS count sheet portal...")
            try:
                scraper = BRSCountSheetScraper(
                    self.dm, brs_cfg.email, brs_cfg.password,
                    login_wait=self.config.engine.scraper_login_wait_seconds,
                )
                result = scraper.extract_for_district(district, DOWNLOAD_DIR)
                count_sheet_records = result["records"]
                if result.get("file_path"):
                    self.db.save_portal_report(district, "count_sheet", result["file_path"], "download")
                self.log(f"[{district}] Count sheet rows extracted: {len(count_sheet_records)}")
            except PortalScraperError as exc:
                self.log(f"[{district}] BRS portal failed: {exc} — continuing with manual data")
        else:
            self.log(f"[{district}] BRS credentials not configured — skipping portal scrape")

        return timesheet_records, count_sheet_records

    # -- variance computation ----------------------------------------------------------------
    def _compute_variances(self, district: str, timesheet_records: List[dict], count_sheet_records: List[dict]) -> List[VarianceRow]:
        from ..pipeline import extract_variances

        master_records = self._store_master_records_for(district)

        inventory_records = count_sheet_records
        if count_sheet_records:
            # reuse the DB rows for stores not present in the downloaded report
            variances, _summary = extract_variances(
                inventory_records, timesheet_records, master_records,
                source_file=f"portal:{district}",
            )
        else:
            # fall back to the last manually loaded workbook stored in the DB
            variances = [
                r for r in self.db.rows(include_cleared=False, district=district)
            ]
            self.log(f"[{district}] Using {len(variances)} stored variance row(s) (no fresh count sheet)")

        # keep previously cleared state when keys match
        existing = {r.key: r for r in self.db.rows(include_cleared=True, district=district)}
        for row in variances:
            prior = existing.get(row.key)
            if prior and prior.cleared:
                row.cleared = True
                row.cleared_at = prior.cleared_at
                row.cleared_via = prior.cleared_via
        self.db.upsert_rows(variances)
        self.db.log_event(district, "variances_computed", f"count={len(variances)}")
        return variances

    def _store_master_records_for(self, district: str) -> List[dict]:
        records = self.db.store_master_records_all() if hasattr(self.db, "store_master_records_all") else []
        if not records:
            from ..paths import STORE_CONFIG_PATH
            import csv

            if STORE_CONFIG_PATH.exists():
                with STORE_CONFIG_PATH.open("r", newline="", encoding="utf-8-sig") as f:
                    records = [dict(r) for r in csv.DictReader(f)]
        return records

    # -- FSM construction ----------------------------------------------------------------------
    def _make_fsm(self, district: str, group: str, variances: List[VarianceRow]) -> DistrictAuditFSM:
        resolver = MentionResolver.from_employees(self.db.employees())

        def send_kickoff(fsm_state) -> bool:
            rep_names = sorted({(v.rep_name or v.created_by or "").strip() for v in variances} - {""})
            tags, missing = resolver.mentions_for_rows(rep_names)
            timeout = self.config.reminders.audit_timeout_minutes
            lines = [f"Audit is open. Please complete the audit within {timeout} minutes."]
            tag_line = resolver.tag_line(tags, "")
            if tag_line:
                lines.insert(0, tag_line.strip())
            if missing:
                self.log(f"[{district}] No employee phone match for: {', '.join(missing[:5])}")
            ok = self.whatsapp.send_text_with_mentions(group, "\n".join(lines))
            if ok:
                self.db.update_district_run(district, kickoff_sent_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                self.db.log_event(district, "kickoff_sent", group)
            return ok

        def post_variance(fsm_state) -> bool:
            pending = [v for v in variances if not v.cleared]
            if not pending:
                self.log(f"[{district}] No pending variances — nothing to post")
                return True
            renderer = ImageRenderer()
            image = renderer.render_rows(district, pending, mode="district")
            tags, _missing = resolver.mentions_for_rows(
                sorted({(v.rep_name or v.created_by or "").strip() for v in pending} - {""})
            )
            caption_lines = ["Inventory variance — please clear the items below."]
            if tags:
                caption_lines.append(" ".join(tags))
            ok = self.whatsapp.send_image(group, image, caption="\n".join(caption_lines))
            if ok:
                self.db.mark_sent(pending, group, district, str(image), "district")
                self.db.update_district_run(
                    district, variance_posted_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
                self.db.log_event(district, "variance_posted", f"rows={len(pending)} image={image.name}")
            return ok

        def send_reminder(fsm_state, reminder_number: int) -> bool:
            pending = self.db.rows(include_cleared=False, district=district)
            if not pending:
                return True
            tags, _missing = resolver.mentions_for_rows(
                sorted({(v.rep_name or v.created_by or "").strip() for v in pending} - {""})
            )
            remaining = f"{len(pending)} variance(s) still pending"
            body_lines = [
                f"Reminder {reminder_number}/{self.config.reminders.max_reminders}: {remaining}.",
                "Please send photo proof of resolution.",
            ]
            if tags:
                body_lines.insert(0, " ".join(tags))
            ok = self.whatsapp.send_text_with_mentions(group, "\n".join(body_lines))
            if ok:
                self.db.update_district_run(
                    district,
                    reminders_sent=reminder_number,
                    last_reminder_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    last_message=f"reminder {reminder_number}",
                )
                self.db.log_event(district, f"reminder_{reminder_number}_sent", f"pending={len(pending)}")
            return ok

        def send_final_notice(fsm_state) -> bool:
            pending = self.db.rows(include_cleared=False, district=district)
            tags, _missing = resolver.mentions_for_rows(
                sorted({(v.rep_name or v.created_by or "").strip() for v in pending} - {""})
            )
            body_lines = [
                "Final notice: this audit round is being finalized.",
                "Unresolved variances will be escalated to management.",
            ]
            if tags:
                body_lines.insert(0, " ".join(tags))
            ok = self.whatsapp.send_text_with_mentions(group, "\n".join(body_lines))
            if ok:
                self.db.update_district_run(
                    district,
                    final_notice_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    last_message="final notice",
                )
                self.db.log_event(district, "final_notice_sent", "")
            return ok

        def on_completed(fsm_state) -> None:
            self.db.update_district_run(
                district,
                completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                state=DistrictState.COMPLETED.value,
            )
            self.db.log_event(district, "audit_completed", fsm_state.summary())
            self.log(f"[{district}] Audit COMPLETED — {fsm_state.summary()}")

        return DistrictAuditFSM(
            state=DistrictAuditState(
                district=district,
                group_name=group,
                state=DistrictState.PENDING,
                total_variances=len(variances),
                started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
            on_send_kickoff=send_kickoff,
            on_post_variance=post_variance,
            on_send_reminder=send_reminder,
            on_send_final_notice=send_final_notice,
            on_completed=on_completed,
            reminder_interval_minutes=self.config.reminders.reminder_interval_minutes,
            max_reminders=self.config.reminders.max_reminders,
        )

    # -- monitor events ---------------------------------------------------------------------------
    def _on_monitor_event(self, event: MonitorEvent) -> None:
        if event.kind == "ocr_match":
            self.log(f"✅ [{event.district}] {event.detail}")
            district = event.district
            fsm = self.fsms.get(district)
            if fsm and fsm.state.is_active:
                total = self.variances_by_district.get(district, [])
                pending = self.db.rows(include_cleared=False, district=district)
                cleared = len(total) - len(pending)
                fsm.register_progress(len(total), cleared)
                fsm.maybe_complete_from_progress()
                if fsm.state.is_active:
                    self._post_clear_confirmation(event)
        elif event.kind == "ocr_miss":
            self.log(f"🔍 [{event.district or 'general'}] {event.detail}")
        elif event.kind == "error":
            self.log(f"⚠️ Monitor error: {event.detail}")

    def _post_clear_confirmation(self, event: MonitorEvent) -> None:
        """Real-time confirmation in the group when an IMEI clears."""
        try:
            fsm = self.fsms.get(event.district)
            if not fsm:
                return
            total = fsm.state.total_variances
            cleared = fsm.state.cleared_variances
            message = (
                f"✅ IMEI cleared by photo confirmation ({cleared}/{total} resolved). "
                "Thank you!"
            )
            self.whatsapp.send_text(event.group, message)
            self.db.log_event(event.district, "clear_confirmation_sent", event.detail)
        except Exception as exc:
            logger.error("Clear confirmation failed: %s", exc)

    # -- introspection for the GUI ---------------------------------------------------------------
    def district_states(self) -> Dict[str, str]:
        return {d: f.state.state.value for d, f in self.fsms.items()}

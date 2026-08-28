"""District audit state machine.

PENDING → KICKOFF_SENT → VARIANCE_POSTED → REMINDER_1 → REMINDER_2
        → REMINDER_3 → FINAL_NOTICE → COMPLETED
Any state can transition to COMPLETED when every variance is cleared, or to
FAILED on an unrecoverable error."""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from ..models import VarianceRow
from ..textutils import now_text

logger = logging.getLogger("gfh.audit.engine.fsm")


class DistrictState(str, Enum):
    PENDING = "pending"
    KICKOFF_SENT = "kickoff_sent"
    VARIANCE_POSTED = "variance_posted"
    REMINDER_1 = "reminder_1"
    REMINDER_2 = "reminder_2"
    REMINDER_3 = "reminder_3"
    FINAL_NOTICE = "final_notice"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


REMINDER_SEQUENCE = {
    DistrictState.VARIANCE_POSTED: DistrictState.REMINDER_1,
    DistrictState.REMINDER_1: DistrictState.REMINDER_2,
    DistrictState.REMINDER_2: DistrictState.REMINDER_3,
}


@dataclass
class DistrictAuditState:
    district: str
    group_name: str
    state: DistrictState = DistrictState.PENDING
    reminders_sent: int = 0
    total_variances: int = 0
    cleared_variances: int = 0
    started_at: str = ""
    last_transition_at: str = ""
    next_action_at: Optional[dt.datetime] = None
    error: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.state in {DistrictState.COMPLETED, DistrictState.FAILED, DistrictState.SKIPPED}

    @property
    def is_active(self) -> bool:
        return not self.is_terminal

    def summary(self) -> str:
        cleared = f"{self.cleared_variances}/{self.total_variances} cleared"
        return f"{self.district}: {self.state.value} ({cleared})"


class DistrictAuditFSM:
    """Per-district finite state machine with reminder scheduling."""

    def __init__(
        self,
        state: DistrictAuditState,
        on_send_kickoff: Callable[["DistrictAuditState"], bool],
        on_post_variance: Callable[["DistrictAuditState"], bool],
        on_send_reminder: Callable[["DistrictAuditState", int], bool],
        on_send_final_notice: Callable[["DistrictAuditState"], bool],
        on_completed: Optional[Callable[["DistrictAuditState"], None]] = None,
        reminder_interval_minutes: int = 5,
        max_reminders: int = 3,
    ):
        self.state = state
        self.on_send_kickoff = on_send_kickoff
        self.on_post_variance = on_post_variance
        self.on_send_reminder = on_send_reminder
        self.on_send_final_notice = on_send_final_notice
        self.on_completed = on_completed
        self.reminder_interval_minutes = max(1, reminder_interval_minutes)
        self.max_reminders = max(1, max_reminders)

    # -- transitions ------------------------------------------------------------
    def start(self) -> bool:
        if self.state.state is not DistrictState.PENDING:
            return False
        self.state.started_at = now_text()
        if not self.on_send_kickoff(self.state):
            return False
        self._transition(DistrictState.KICKOFF_SENT)
        # variance post happens right after the kickoff in the same run cycle
        if self.on_post_variance(self.state):
            self._transition(DistrictState.VARIANCE_POSTED)
            self._schedule_next_reminder()
        return True

    def reminder_due(self, now: Optional[dt.datetime] = None) -> bool:
        if self.state.state not in REMINDER_SEQUENCE or self.state.next_action_at is None:
            return False
        now = now or dt.datetime.now()
        return now >= self.state.next_action_at

    def fire_reminder(self) -> bool:
        """Send the next reminder / final notice when due. Returns True if fired."""
        if not self.reminder_due():
            return False
        next_state = REMINDER_SEQUENCE.get(self.state.state)
        if next_state is None:
            return False
        if self.state.cleared_variances >= self.state.total_variances > 0:
            self.complete("all variances cleared")
            return False

        self.state.reminders_sent += 1
        if not self.on_send_reminder(self.state, self.state.reminders_sent):
            # retry later on the next engine tick
            self._schedule_next_reminder()
            return False
        self._transition(next_state)
        if self.state.reminders_sent >= self.max_reminders:
            if self.on_send_final_notice(self.state):
                self._transition(DistrictState.FINAL_NOTICE)
                # final notice sent: audit round finalises
                self.complete("final notice sent after 3 reminders")
            else:
                self._schedule_next_reminder()
        else:
            self._schedule_next_reminder()
        return True

    def register_progress(self, total_variances: int, cleared_variances: int) -> None:
        self.state.total_variances = total_variances
        self.state.cleared_variances = cleared_variances

    def maybe_complete_from_progress(self) -> bool:
        if self.state.total_variances > 0 and self.state.cleared_variances >= self.state.total_variances:
            return self.complete("all variances cleared via OCR/manual entries")
        return False

    def complete(self, reason: str = "") -> bool:
        if self.state.is_terminal:
            return False
        self.state.error = ""
        self._transition(DistrictState.COMPLETED, note=reason)
        if self.on_completed:
            try:
                self.on_completed(self.state)
            except Exception:
                pass
        return True

    def fail(self, error: str) -> None:
        self.state.error = error
        self._transition(DistrictState.FAILED, note=error)

    # -- internals -----------------------------------------------------------------
    def _schedule_next_reminder(self) -> None:
        self.state.next_action_at = dt.datetime.now() + dt.timedelta(
            minutes=self.reminder_interval_minutes
        )

    def _transition(self, new_state: DistrictState, note: str = "") -> None:
        old = self.state.state
        self.state.state = new_state
        self.state.last_transition_at = now_text()
        if new_state is DistrictState.COMPLETED:
            self.state.next_action_at = None
        logger.info("FSM %s: %s -> %s %s", self.state.district, old.value, new_state.value, note)

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gfh_audit.engine.state_machine import DistrictAuditFSM, DistrictAuditState, DistrictState


def make_fsm(reminder_interval_minutes=5, max_reminders=3, kickoff_ok=True,
             variance_ok=True, reminder_ok=True, final_ok=True):
    state = DistrictAuditState(district="Houston", group_name="GFH TELECOM HOUSTON")
    calls = {"kickoff": 0, "variance": 0, "reminders": [], "final": 0, "completed": 0}

    fsm = DistrictAuditFSM(
        state=state,
        on_send_kickoff=lambda s: (calls.__setitem__("kickoff", calls["kickoff"] + 1) or kickoff_ok),
        on_post_variance=lambda s: (calls.__setitem__("variance", calls["variance"] + 1) or variance_ok),
        on_send_reminder=lambda s, n: (calls["reminders"].append(n) or reminder_ok),
        on_send_final_notice=lambda s: (calls.__setitem__("final", calls["final"] + 1) or final_ok),
        on_completed=lambda s: calls.__setitem__("completed", calls["completed"] + 1),
        reminder_interval_minutes=reminder_interval_minutes,
        max_reminders=max_reminders,
    )
    return fsm, calls


class TestStateMachine(unittest.TestCase):
    def test_happy_flow_kickoff_then_variance(self):
        fsm, calls = make_fsm()
        self.assertTrue(fsm.start())
        self.assertEqual(fsm.state.state, DistrictState.VARIANCE_POSTED)
        self.assertEqual(calls["kickoff"], 1)
        self.assertEqual(calls["variance"], 1)
        self.assertIsNotNone(fsm.state.next_action_at)

    def test_reminders_fire_after_interval(self):
        fsm, calls = make_fsm(reminder_interval_minutes=5)
        fsm.start()
        # not due yet
        self.assertFalse(fsm.reminder_due(now=datetime.now() + timedelta(minutes=1)))
        # due
        self.assertTrue(fsm.reminder_due(now=datetime.now() + timedelta(minutes=6)))
        fsm.state.next_action_at = datetime.now() - timedelta(seconds=1)
        self.assertTrue(fsm.fire_reminder())
        self.assertEqual(fsm.state.state, DistrictState.REMINDER_1)
        fsm.state.next_action_at = datetime.now() - timedelta(seconds=1)
        fsm.fire_reminder()
        self.assertEqual(fsm.state.state, DistrictState.REMINDER_2)
        fsm.state.next_action_at = datetime.now() - timedelta(seconds=1)
        # 3rd reminder: max reached → final notice → finalise audit round
        fsm.fire_reminder()
        self.assertEqual(fsm.state.state, DistrictState.COMPLETED)
        self.assertEqual(len(calls["reminders"]), 3)
        self.assertEqual(calls["final"], 1)
        self.assertEqual(calls["completed"], 1)

    def test_max_reminders_two(self):
        fsm, calls = make_fsm(max_reminders=2)
        fsm.start()
        for _ in range(2):
            fsm.state.next_action_at = datetime.now() - timedelta(seconds=1)
            fsm.fire_reminder()
        self.assertEqual(fsm.state.state, DistrictState.COMPLETED)
        self.assertEqual(len(calls["reminders"]), 2)
        self.assertEqual(calls["final"], 1)

    def test_early_completion_on_all_cleared(self):
        fsm, calls = make_fsm()
        fsm.start()
        fsm.register_progress(10, 10)
        self.assertTrue(fsm.maybe_complete_from_progress())
        self.assertEqual(fsm.state.state, DistrictState.COMPLETED)

    def test_progress_does_not_complete_partial(self):
        fsm, calls = make_fsm()
        fsm.start()
        fsm.register_progress(10, 3)
        self.assertFalse(fsm.maybe_complete_from_progress())
        self.assertEqual(fsm.state.state, DistrictState.VARIANCE_POSTED)

    def test_fail_path(self):
        fsm, calls = make_fsm(kickoff_ok=False)
        self.assertFalse(fsm.start())
        self.assertEqual(fsm.state.state, DistrictState.PENDING)


if __name__ == "__main__":
    unittest.main()

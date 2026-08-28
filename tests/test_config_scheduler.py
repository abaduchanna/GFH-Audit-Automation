import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gfh_audit.config import AppConfig, ConfigStore, DistrictScheduleEntry
from gfh_audit.engine.scheduler import DistrictScheduler
from gfh_audit.textutils import parse_start_time


class TestConfigStore(unittest.TestCase):
    def test_roundtrip_obfuscates_password(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp) / "cfg.json")
            cfg = AppConfig()
            cfg.timesheet.email = "user@gfh.com"
            cfg.timesheet.password = "secret123"
            cfg.brs.email = "brs@gfh.com"
            cfg.brs.password = "hunter2"
            cfg.schedule["Houston"] = DistrictScheduleEntry(
                district="Houston", whatsapp_group="GFH TELECOM HOUSTON",
                start_time="09:30", enabled=True,
            )
            store.save(cfg)

            # password obfuscated on disk
            import json

            raw = json.loads((Path(tmp) / "cfg.json").read_text())
            self.assertNotIn("secret123", json.dumps(raw))

            loaded = store.load()
            self.assertEqual(loaded.timesheet.password, "secret123")
            self.assertEqual(loaded.brs.password, "hunter2")
            self.assertEqual(loaded.schedule["Houston"].start_time, "09:30")


class TestScheduler(unittest.TestCase):
    def test_build_queue_respects_schedule(self):
        from datetime import datetime

        fired = []
        scheduler = DistrictScheduler(on_fire=fired.append)
        entries = [
            DistrictScheduleEntry(district="Houston", whatsapp_group="G1", start_time="23:59", enabled=True),
            DistrictScheduleEntry(district="Arizona", whatsapp_group="G2", start_time="", enabled=True),
            DistrictScheduleEntry(district="Disabled", whatsapp_group="", start_time="08:00", enabled=False),
        ]
        queued = scheduler.build_queue(entries, now=datetime(2025, 1, 1, 10, 0))
        self.assertEqual(len(queued), 2)  # disabled excluded
        self.assertIsNone(queued[1].start_at)  # empty start == immediate

    def test_fire_all_now(self):
        from datetime import datetime, timedelta

        fired = []
        scheduler = DistrictScheduler(on_fire=fired.append)
        entries = [DistrictScheduleEntry(district="Houston", start_time=(datetime.now() + timedelta(hours=2)).strftime("%H:%M"))]
        scheduler.build_queue(entries)
        scheduler.fire_all_now()
        scheduler._tick()  # immediate tick — should fire now
        self.assertEqual(len(fired), 1)


class TestTimeParsing(unittest.TestCase):
    def test_schedule_formats(self):
        from datetime import time

        self.assertEqual(parse_start_time("9:30"), time(9, 30))
        self.assertEqual(parse_start_time("18:45"), time(18, 45))
        self.assertIsNone(parse_start_time("25:00"))


if __name__ == "__main__":
    unittest.main()

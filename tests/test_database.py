import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gfh_audit.database import VarianceDatabase
from gfh_audit.models import InventoryStatusRow, VarianceRow
from gfh_audit.textutils import variance_key


class TestDatabase(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = TemporaryDirectory()
        self.db = VarianceDatabase(Path(self._tmp.name) / "test.sqlite3")

    def tearDown(self):
        self.db = None
        self._tmp.cleanup()

    def _row(self, store="A", imei="356938035194802", district="Houston", cleared=False):
        return VarianceRow(
            key=variance_key(store, imei, "iPhone 15", "Deficit", "rep", "01/01/2024"),
            district=district, store=store, product="iPhone 15", imei=imei,
            status="Deficit", created_by="rep", rep_name="rep",
            created_date="01/01/2024", source_file="test",
        )

    def test_upsert_and_read(self):
        row = self._row()
        self.db.upsert_rows([row])
        rows = self.db.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].imei, "356938035194802")

    def test_upsert_preserves_cleared_state(self):
        row = self._row()
        self.db.upsert_rows([row])
        self.db.set_cleared(row.key, True, via="ocr")
        # re-upsert same key (recount) must keep cleared
        self.db.upsert_rows([self._row()])
        rows = self.db.rows(include_cleared=True)
        self.assertTrue(rows[0].cleared)
        self.assertEqual(rows[0].cleared_via, "ocr")

    def test_clear_by_imei(self):
        row = self._row()
        self.db.upsert_rows([row])
        keys = self.db.clear_by_imei("356938035194802", district="Houston", via="ocr")
        self.assertEqual(keys, [row.key])
        self.assertEqual(self.db.rows(), [])

    def test_clear_by_imei_partial(self):
        row = self._row(imei="356938035194802")
        self.db.upsert_rows([row])
        # OCR misread of last 12 digits match window
        keys = self.db.clear_by_imei("938035194802")
        self.assertEqual(keys, [row.key])

    def test_clear_by_imei_respects_district(self):
        row = self._row(district="Houston")
        self.db.upsert_rows([row])
        keys = self.db.clear_by_imei("356938035194802", district="Arizona")
        self.assertEqual(keys, [])

    def test_employee_phone_lookup(self):
        self.db.save_employee("Abad Channa", "2815551234", "Houston")
        self.db.save_employee("Zed Cheap", "7135559999")
        self.assertEqual(self.db.find_employee_phone("abad channa"), "2815551234")
        self.assertEqual(self.db.find_employee_phone("Channa, Abad"), "2815551234")
        self.assertEqual(self.db.find_employee_phone("Unknown Person"), "")
        employees = self.db.employees()
        self.assertEqual(len(employees), 2)

    def test_district_runs(self):
        self.db.set_district_state("Houston", "pending")
        self.db.update_district_run("Houston", total_variances=5, cleared_variances=2)
        run = self.db.district_run("Houston")
        self.assertEqual(run["total_variances"], 5)
        n = self.db.increment_reminders("Houston")
        self.assertEqual(n, 1)
        n = self.db.increment_reminders("Houston")
        self.assertEqual(n, 2)

    def test_whatsapp_groups(self):
        self.db.save_whatsapp_group("Houston", "GFH TELECOM HOUSTON")
        self.assertEqual(self.db.find_whatsapp_group("Houston"), "GFH TELECOM HOUSTON")
        self.db.delete_whatsapp_group("Houston")
        self.assertEqual(self.db.find_whatsapp_group("Houston"), "")

    def test_inventory_status_rows(self):
        self.db.upsert_inventory_status_rows([
            InventoryStatusRow(key="k1", district="Houston", store="A", status="Completed"),
            InventoryStatusRow(key="k2", district="Houston", store="B", status="Pending"),
        ])
        rows = self.db.inventory_status_rows(district="Houston")
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(1 for r in rows if r.status == "Completed"), 1)

    def test_portal_reports_and_events(self):
        self.db.save_portal_report("Houston", "timesheet", "/tmp/x.xlsx", "download")
        self.assertEqual(self.db.latest_portal_report("Houston", "timesheet"), "/tmp/x.xlsx")
        self.db.log_event("Houston", "kickoff_sent", "detail")
        self.assertGreaterEqual(len(self.db.recent_events()), 1)


if __name__ == "__main__":
    unittest.main()

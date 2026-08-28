"""Tests for the processed-image registry (duplicate IMEI scan prevention)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gfh_audit.database import VarianceDatabase


class ProcessedImageRegistryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = VarianceDatabase(Path(self._tmp.name) / "test.sqlite3")

    def tearDown(self):
        self._tmp.cleanup()

    def test_unseen_message_is_not_processed(self):
        self.assertFalse(self.db.is_message_processed("msg-100"))

    def test_mark_then_check(self):
        self.db.mark_message_processed(
            "msg-1", district="Dallas", group_name="GFH Dallas", imeis=["356938035643809"]
        )
        self.assertTrue(self.db.is_message_processed("msg-1"))
        self.assertFalse(self.db.is_message_processed("msg-2"))

    def test_empty_message_id_is_never_processed(self):
        self.db.mark_message_processed("", district="X")
        self.assertFalse(self.db.is_message_processed(""))

    def test_imeis_survive_roundtrip(self):
        imeis = ["490154203237518", "356938035643809"]
        self.db.mark_message_processed("msg-3", district="Plano", group_name="G", imeis=imeis)
        # verify through direct query on the registry table
        with self.db.connect() as con:
            row = con.execute(
                "SELECT imeis, district FROM processed_images WHERE message_id = ?",
                ("msg-3",),
            ).fetchone()
        self.assertEqual(row["district"], "Plano")
        self.assertEqual(row["imeis"].split(","), imeis)

    def test_prune_keeps_newest(self):
        for i in range(20):
            self.db.mark_message_processed(f"m{i}", district="D")
        self.db.prune_processed_images(keep=10)
        with self.db.connect() as con:
            count = con.execute("SELECT COUNT(*) c FROM processed_images").fetchone()["c"]
        self.assertEqual(count, 10)
        # newest entries survive
        self.assertTrue(self.db.is_message_processed("m19"))
        self.assertFalse(self.db.is_message_processed("m0"))


if __name__ == "__main__":
    unittest.main()

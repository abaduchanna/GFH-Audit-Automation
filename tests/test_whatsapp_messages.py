"""Regression tests for the pending inventory count WhatsApp reminder.

Per pending store the line addresses the PERSON responsible whenever the
row has one (the Salesperson column — which also carries the timesheet-
matched employee for stores absent from the count file):

    1. Rep has a phone on file (Employees tab / sales reps): tag it —
           @<phone>, please complete the inventory count ASAP.
    2. Rep known but no phone anywhere: address the person by name —
           <Rep Name>, please complete the inventory count ASAP.
    3. No rep on the row: fall back to the store name —
           <Store Name>, please complete the inventory count ASAP.

These tests extract ``pending_inventory_count_message`` (plus its
dependencies ``safe_text``, ``normalize_phone``, ``whatsapp_mention`` and
``InventoryStatusRow``) from the standalone ``GFH_Inventory_Audit_Automation.py``
script via the AST, so no GUI / selenium imports are needed.
"""
import ast
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "GFH_Inventory_Audit_Automation.py"

FORBIDDEN_TEXTS = (
    "Count not completed",
    "Employee at store",
    "no timesheet entry",
    "⚠️",
    "—",
)


def _extract_sources():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))
    wanted_funcs = ("safe_text", "normalize_phone", "whatsapp_mention")
    sources: dict = {"func": {}, "row": None, "pending": None}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted_funcs:
            sources["func"][node.name] = ast.unparse(node)
        if isinstance(node, ast.ClassDef) and node.name == "InventoryStatusRow":
            sources["row"] = ast.unparse(node)
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if (
                    isinstance(child, ast.FunctionDef)
                    and child.name == "pending_inventory_count_message"
                ):
                    sources["pending"] = ast.unparse(child)
    missing = [f for f in wanted_funcs if f not in sources["func"]]
    if missing or not sources["row"] or not sources["pending"]:
        raise RuntimeError(f"Could not extract required definitions: missing {missing}")
    return sources


SOURCES = _extract_sources()


class FakeDB:
    """Employees-tab aware phone resolver stub."""

    def __init__(self, phones: dict):
        self.phones = phones
        self.asked: list = []

    def resolve_phone_for_rep(self, rep_name, created_by=""):
        self.asked.append(rep_name)
        return self.phones.get(rep_name, "")


class NoLookupDB:
    """Sentinel — must never be touched when a row carries no rep name."""

    def resolve_phone_for_rep(self, *a, **k):
        raise AssertionError("resolve_phone_for_rep must not be called without a rep name")


def _make_namespace():
    ns = {"dataclass": dataclass, "List": List}
    for name in ("safe_text", "normalize_phone", "whatsapp_mention"):
        exec(SOURCES["func"][name], ns)
    exec(SOURCES["row"], ns)
    exec(SOURCES["pending"], ns)
    return ns


class TestPendingInventoryCountMessage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ns = _make_namespace()
        cls.InventoryStatusRow = ns["InventoryStatusRow"]
        # staticmethod: prevent Python from binding the plain function as a
        # method when accessed through self (which would shift the args).
        cls.fn = staticmethod(ns["pending_inventory_count_message"])

    def _row(self, store, status="Pending", rep=""):
        return self.InventoryStatusRow(
            key=f"{store}-{status}", district="Arizona", store=store,
            status=status, rep_name=rep,
        )

    def _call(self, rows, db):
        return self.fn(SimpleNamespace(db=db), rows)

    # ── case 3: no rep on the row ─────────────────────────────────────────

    def test_user_reported_example_no_rep(self):
        """Exact original scenario: two pending stores, no rep names."""
        rows = [self._row("Kings highway store"), self._row("Hollywood Store")]
        db = NoLookupDB()
        message = self._call(rows, db)
        self.assertEqual(
            message,
            "Kings highway store, please complete the inventory count ASAP.\n\n"
            "Hollywood Store, please complete the inventory count ASAP.",
        )

    def test_no_verbose_block(self):
        rows = [self._row("Kings highway store"), self._row("Hollywood Store")]
        message = self._call(rows, NoLookupDB())
        for forbidden in FORBIDDEN_TEXTS:
            self.assertNotIn(forbidden, message)

    def test_completed_stores_skipped(self):
        rows = [
            self._row("Done Store", status="Completed"),
            self._row("Pending Store", status="Pending"),
        ]
        message = self._call(rows, NoLookupDB())
        self.assertEqual(message, "Pending Store, please complete the inventory count ASAP.")

    def test_duplicate_store_deduped(self):
        rows = [self._row("Kings highway store"), self._row("Kings highway store")]
        message = self._call(rows, NoLookupDB())
        self.assertEqual(message, "Kings highway store, please complete the inventory count ASAP.")

    def test_all_completed_returns_empty(self):
        rows = [self._row("Done Store", status="Completed")]
        self.assertEqual(self._call(rows, NoLookupDB()), "")

    def test_empty_rows_returns_empty(self):
        self.assertEqual(self._call([], NoLookupDB()), "")

    # ── case 1: rep with a phone on file → tag the number ─────────────────

    def test_rep_with_phone_is_tagged(self):
        rows = [self._row("N 19th Store", rep="Adithya Mosam")]
        db = FakeDB({"Adithya Mosam": "+1 404 555 1234"})
        message = self._call(rows, db)
        self.assertEqual(
            message,
            "@+14045551234, please complete the inventory count ASAP.",
        )
        self.assertEqual(db.asked, ["Adithya Mosam"])

    def test_tagged_phone_drops_store_name(self):
        """The tag replaces the store reference, per spec."""
        rows = [self._row("South Central Store", rep="Dua E Batool")]
        db = FakeDB({"Dua E Batool": "404-555-9876"})
        message = self._call(rows, db)
        self.assertEqual(message, "@4045559876, please complete the inventory count ASAP.")
        self.assertNotIn("South Central", message)

    def test_phone_lookup_uses_employees_tab_cascade(self):
        """resolve_phone_for_rep (Employees tab + sales reps) is the lookup —
        not just the sales-reps table."""
        rows = [self._row("Phoenix Store", rep="Abhay Surya Sanjay Kumar")]
        db = FakeDB({"Abhay Surya Sanjay Kumar": "+1 602 555 0101"})
        message = self._call(rows, db)
        self.assertTrue(message.startswith("@+16025550101,"))

    # ── case 2: rep known but no phone → person's name instead of store ───

    def test_rep_without_phone_named_instead_of_store(self):
        rows = [self._row("N 19th Store", rep="Adithya Mosam")]
        message = self._call(rows, FakeDB({}))
        self.assertEqual(
            message,
            "Adithya Mosam, please complete the inventory count ASAP.",
        )
        self.assertNotIn("N 19th", message)

    def test_screenshot_scenario_both_reps_no_phones(self):
        """The exact screenshot case: two pending stores, both with
        salesperson names, no phones on file → both lines use the person."""
        rows = [
            self._row("N 19th Store", rep="Adithya Mosam"),
            self._row("South Central Store", rep="Dua E Batool"),
        ]
        message = self._call(rows, FakeDB({}))
        self.assertEqual(
            message,
            "Adithya Mosam, please complete the inventory count ASAP.\n\n"
            "Dua E Batool, please complete the inventory count ASAP.",
        )

    # ── mixed rows keep per-store order ───────────────────────────────────

    def test_mixed_rows_full_cascade(self):
        rows = [
            self._row("Tagged Store", rep="Rep One"),
            self._row("Named Store", rep="Rep Two"),
            self._row("Plain Store"),
        ]
        db = FakeDB({"Rep One": "+1 404 555 0001"})
        message = self._call(rows, db)
        self.assertEqual(
            message,
            "@+14045550001, please complete the inventory count ASAP.\n\n"
            "Rep Two, please complete the inventory count ASAP.\n\n"
            "Plain Store, please complete the inventory count ASAP.",
        )


if __name__ == "__main__":
    unittest.main()

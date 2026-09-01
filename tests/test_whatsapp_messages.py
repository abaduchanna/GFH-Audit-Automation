"""Regression tests for the pending inventory count WhatsApp reminder.

The message used to be a verbose block:

    ⚠️ {Store} — Count not completed
    Employee at store: (no timesheet entry for this store)

Per task spec it must now be one plain line per pending store:

    {Store}, please complete the inventory count ASAP.

These tests extract ``pending_inventory_count_message`` (plus its
dependencies ``safe_text`` and ``InventoryStatusRow``) from the standalone
``GFH_Inventory_Audit_Timesheet.py`` script via the AST, so no GUI /
selenium imports are needed.
"""
import ast
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "GFH_Inventory_Audit_Timesheet.py"

FORBIDDEN_TEXTS = (
    "Count not completed",
    "Employee at store",
    "no timesheet entry",
    "⚠️",
    "—",
)


def _extract_sources():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))
    safe_text_src = None
    status_row_src = None
    pending_msg_src = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "safe_text":
            safe_text_src = ast.unparse(node)
        if isinstance(node, ast.ClassDef) and node.name == "InventoryStatusRow":
            status_row_src = ast.unparse(node)
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if (
                    isinstance(child, ast.FunctionDef)
                    and child.name == "pending_inventory_count_message"
                ):
                    pending_msg_src = ast.unparse(child)
    if not (safe_text_src and status_row_src and pending_msg_src):
        raise RuntimeError("Could not extract required definitions from script")
    return safe_text_src, status_row_src, pending_msg_src


SAFE_TEXT_SRC, STATUS_ROW_SRC, PENDING_MSG_SRC = _extract_sources()


def _make_namespace():
    ns = {"dataclass": dataclass, "List": List}
    exec(SAFE_TEXT_SRC, ns)
    exec(STATUS_ROW_SRC, ns)
    exec(PENDING_MSG_SRC, ns)
    return ns


class TestPendingInventoryCountMessage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ns = _make_namespace()
        cls.InventoryStatusRow = ns["InventoryStatusRow"]
        # staticmethod: prevent Python from binding the plain function as a
        # method when accessed through self (which would shift the args).
        cls.fn = staticmethod(ns["pending_inventory_count_message"])
        cls.db_never_called = object()  # sentinel; function must not touch self.db

    def _row(self, store, status="Pending", rep=""):
        return self.InventoryStatusRow(
            key=f"{store}-{status}", district="Arizona", store=store,
            status=status, rep_name=rep,
        )

    def test_user_reported_example(self):
        """Exact user scenario: two pending stores, no timesheet entries."""
        rows = [
            self._row("Kings highway store"),
            self._row("Hollywood Store"),
        ]
        message = self.fn(self.db_never_called, rows)
        self.assertEqual(
            message,
            "Kings highway store, please complete the inventory count ASAP.\n\n"
            "Hollywood Store, please complete the inventory count ASAP.",
        )

    def test_no_verbose_block(self):
        rows = [self._row("Kings highway store"), self._row("Hollywood Store")]
        message = self.fn(self.db_never_called, rows)
        for forbidden in FORBIDDEN_TEXTS:
            self.assertNotIn(forbidden, message)

    def test_completed_stores_skipped(self):
        rows = [
            self._row("Done Store", status="Completed"),
            self._row("Pending Store", status="Pending"),
        ]
        message = self.fn(self.db_never_called, rows)
        self.assertEqual(message, "Pending Store, please complete the inventory count ASAP.")

    def test_duplicate_store_deduped(self):
        rows = [
            self._row("Kings highway store"),
            self._row("Kings highway store"),
        ]
        message = self.fn(self.db_never_called, rows)
        self.assertEqual(message, "Kings highway store, please complete the inventory count ASAP.")

    def test_all_completed_returns_empty(self):
        rows = [self._row("Done Store", status="Completed")]
        self.assertEqual(self.fn(self.db_never_called, rows), "")

    def test_empty_rows_returns_empty(self):
        self.assertEqual(self.fn(self.db_never_called, []), "")

    def test_no_mention_even_with_rep(self):
        """The new format must not include WhatsApp @mention lines."""
        rows = [self._row("Kings highway store", rep="Abad Channa")]
        message = self.fn(self.db_never_called, rows)
        self.assertEqual(message, "Kings highway store, please complete the inventory count ASAP.")
        self.assertNotIn("@", message)


if __name__ == "__main__":
    unittest.main()

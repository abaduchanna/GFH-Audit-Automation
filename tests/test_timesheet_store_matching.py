"""Regression tests: pending stores must get their employee from the timesheet.

User-reported bug: when a store's count is pending, the Rep Name in the
Inventory Audit Status shows blank even though the timesheet clearly has an
employee clocked in at that store.

Root causes fixed:
1. build_store_maps only transferred timesheet employees for stores that
   appear in the count file — pending stores that exist only in the master
   store list never got an employee.
2. Slight store-name differences between the timesheet export and the
   count file ("Kings Highway #1204" vs "Kings Highway Store 1204") broke
   the exact-match lookup.

These tests extract the light module-level helpers from the standalone
script via the AST (no GUI / selenium imports needed).
"""
import ast
import datetime as dt
import hashlib
import re
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "GFH_Inventory_Audit_Timesheet.py"

NEEDED_FUNCS = [
    "safe_text",
    "normalize_header",
    "normalize_store",
    "display_store",
    "normalize_district",
    "excel_serial_to_datetime",
    "numeric_excel_date",
    "find_column",
    "build_ts_store_to_employee",
    "store_name_tokens",
    "match_store_employee",
    "build_store_maps",
    "filter_latest_inventory_records",
    "build_inventory_status_rows",
]


def _extract_sources():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in NEEDED_FUNCS:
            found[node.name] = ast.unparse(node)
        if isinstance(node, ast.ClassDef) and node.name == "InventoryStatusRow":
            found["InventoryStatusRow"] = ast.unparse(node)
    missing = set(NEEDED_FUNCS) - set(found) | (
        {"InventoryStatusRow"} if "InventoryStatusRow" not in found else set()
    )
    if missing:
        raise RuntimeError(f"Could not extract from script: {sorted(missing)}")
    return found


def _make_namespace():
    ns = {
        "re": re, "dt": dt, "hashlib": hashlib, "dataclass": dataclass,
        "Dict": Dict, "List": List, "Optional": Optional, "Tuple": Tuple,
        "Iterable": Iterable, "Any": Any,
    }
    for name, src in _extract_sources().items():
        exec(src, ns)
    return ns


def _count_row(store, created_by, imei="111111111111111", created="45870.5"):
    return {
        "Store": store, "District": "Arizona - D1", "Created By": created_by,
        "Created Date/Time": created, "Serial #": imei, "Status": "Matched",
        "Product": "Phone",
    }


class TestTimesheetStoreMatching(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ns = _make_namespace()
        for name in NEEDED_FUNCS + ["InventoryStatusRow"]:
            setattr(cls, name, staticmethod(ns[name]) if callable(ns[name]) else ns[name])

    # ── match_store_employee unit tests ───────────────────────────────────
    def test_exact_lookup(self):
        ts = {"kings highway": "John Doe"}
        self.assertEqual(self.match_store_employee("kings highway", ts), "John Doe")

    def test_fuzzy_subset_both_directions(self):
        ts = {"kings highway store 1204": "Jane Roe"}
        self.assertEqual(self.match_store_employee("kings highway", ts), "Jane Roe")
        ts2 = {"kings highway": "Jane Roe"}
        self.assertEqual(self.match_store_employee("kings highway store 1204", ts2), "Jane Roe")

    def test_hash_and_symbols_ignored(self):
        ts = {"kings highway 1204": "Jane Roe"}
        self.assertEqual(self.match_store_employee("kings highway #1204", ts), "Jane Roe")

    def test_conflicting_store_numbers_do_not_match(self):
        ts = {"store 1204": "Wrong Guy"}
        self.assertEqual(self.match_store_employee("store 1205", ts), "")

    def test_best_overlap_wins(self):
        ts = {"kings highway": "A", "kings highway store": "B"}
        self.assertEqual(self.match_store_employee("kings highway store 5", ts), "B")

    def test_empty_inputs(self):
        self.assertEqual(self.match_store_employee("", {"x": "y"}), "")
        self.assertEqual(self.match_store_employee("kings highway", {}), "")

    # ── build_ts_store_to_employee ────────────────────────────────────────
    def test_latest_clockin_wins(self):
        rows = [
            {"Store": "Kings Highway", "Employee": "Early Emp", "Clock In": "45870.29"},
            {"Store": "Kings Highway", "Employee": "Late Emp", "Clock In": "45870.50"},
        ]
        self.assertEqual(
            self.build_ts_store_to_employee(rows)["kings highway"], "Late Emp")

    def test_total_rows_skipped(self):
        rows = [
            {"Store": "Kings Highway", "Employee": "TOTAL", "Clock In": "45870.29"},
            {"Store": "Kings Highway", "Employee": "Real Emp", "Clock In": "45870.50"},
        ]
        self.assertEqual(
            self.build_ts_store_to_employee(rows)["kings highway"], "Real Emp")

    def test_missing_clockin_still_kept(self):
        rows = [{"Store": "Kings Highway", "Employee": "Scheduled Emp", "Clock In": ""}]
        self.assertEqual(
            self.build_ts_store_to_employee(rows)["kings highway"], "Scheduled Emp")

    # ── end-to-end: build_inventory_status_rows ───────────────────────────
    def test_user_scenario_pending_store_gets_timesheet_employee(self):
        """Count file has only Store A; Kings Highway is pending (master list
        only) but the timesheet has John Doe clocked in there."""
        inv = [_count_row("Houston Store A", "alice")]
        master = [{"Store": "Kings Highway", "District": "Arizona - D1"}]
        ts = [{"Store": "Kings Highway", "Employee": "John Doe", "Clock In": "45870.33"}]
        rows, _summary = self.build_inventory_status_rows(inv, ts, master_store_records=master)
        by_store = {r.store: r for r in rows}
        self.assertIn("Kings Highway", by_store)
        row = by_store["Kings Highway"]
        self.assertEqual(row.status, "Pending")
        self.assertEqual(row.rep_name, "John Doe")

    def test_fuzzy_variant_matches_end_to_end(self):
        """Timesheet spells the store slightly differently than the count file."""
        inv = [_count_row("Kings Highway", "bob")]
        master = [{"Store": "Kings Highway", "District": "Arizona - D1"}]
        ts = [{"Store": "Kings Highway Store #1204", "Employee": "Jane Roe", "Clock In": "45870.33"}]
        rows, _summary = self.build_inventory_status_rows(inv, ts, master_store_records=master)
        row = next(r for r in rows if r.store == "Kings Highway")
        self.assertEqual(row.status, "Completed")
        self.assertEqual(row.rep_name, "Jane Roe")

    def test_exact_match_completed_store(self):
        inv = [_count_row("Houston Store A", "alice")]
        ts = [{"Store": "Houston Store A", "Employee": "Alice A", "Clock In": "45870.33"}]
        rows, _summary = self.build_inventory_status_rows(inv, ts)
        row = next(r for r in rows if r.store == "Houston Store A")
        self.assertEqual(row.status, "Completed")
        self.assertEqual(row.rep_name, "Alice A")

    def test_no_timesheet_no_crash_blank_rep(self):
        inv = [_count_row("Houston Store A", "alice")]
        master = [{"Store": "Kings Highway", "District": "Arizona - D1"}]
        rows, _summary = self.build_inventory_status_rows(inv, [], master_store_records=master)
        by_store = {r.store: r for r in rows}
        self.assertEqual(by_store["Kings Highway"].rep_name, "")

    def test_different_stores_not_confused(self):
        master = [{"Store": "Store 1205", "District": "Arizona - D1"}]
        ts = [{"Store": "Store 1204", "Employee": "Wrong Guy", "Clock In": "45870.33"}]
        rows, _summary = self.build_inventory_status_rows([], ts, master_store_records=master)
        row = next(r for r in rows if r.store == "Store 1205")
        self.assertEqual(row.rep_name, "")

    # ── build_store_maps transfers employees with fuzzy fallback ─────────
    def test_build_store_maps_fuzzy_transfer(self):
        inv = [_count_row("Kings Highway", "bob")]
        ts = [{"Store": "Kings Highway Store", "Employee": "Jane Roe", "Clock In": "45870.33"}]
        _d, _disp, rep_by_store = self.build_store_maps(inv, ts)
        self.assertEqual(rep_by_store.get("kings highway"), "Jane Roe")


if __name__ == "__main__":
    unittest.main()

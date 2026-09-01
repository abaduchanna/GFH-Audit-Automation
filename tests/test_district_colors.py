"""Regression test: Atlanta rows must be colored in the Inventory Audit Status image.

User-reported issue: Atlanta rows rendered with the default gray fallback
because "Atlanta" was missing from the district_colors map in
_render_status_rows, while every other district had a row color.

Fix: Atlanta uses the GFH Telecom brand magenta sampled from the user's
reference artwork: RGB (189, 49, 120). Also normalize_district now maps
case variants ("atlanta"/"ATLANTA") to the canonical "Atlanta" key.
"""
import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "GFH_Inventory_Audit_Timesheet.py"

EXPECTED_DISTRICT_COLORS = {
    "Arizona": (196, 138, 230),
    "Atlanta": (189, 49, 120),
    "Colorado East": (96, 194, 236),
    "Colorado West": (78, 186, 228),
    "Houston": (244, 128, 128),
    "Louisiana": (247, 199, 34),
    "Tennessee": (74, 145, 62),
}


def _extract_district_colors():
    """Find the district_colors dict literal inside _render_status_rows."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for child in node.body:
            if not isinstance(child, ast.FunctionDef):
                continue
            if child.name != "_render_status_rows":
                continue
            for stmt in ast.walk(child):
                if (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and stmt.targets[0].id == "district_colors"
                ):
                    return ast.literal_eval(stmt.value)
    raise RuntimeError("district_colors assignment not found in _render_status_rows")


def _extract_normalize_district():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ("safe_text", "normalize_district"):
            yield node


class TestAtlantaDistrictColor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.district_colors = _extract_district_colors()
        ns = {"re": __import__("re")}
        for node in _extract_normalize_district():
            exec(ast.unparse(node), ns)
        # staticmethod: prevent instance-method binding from shifting args
        cls.normalize_district = staticmethod(ns["normalize_district"])

    def test_atlanta_present_with_reference_color(self):
        self.assertEqual(
            self.district_colors.get("Atlanta"), (189, 49, 120),
            "Atlanta must use the GFH Telecom magenta from the reference artwork",
        )

    def test_all_existing_districts_kept(self):
        for district, color in EXPECTED_DISTRICT_COLORS.items():
            self.assertEqual(
                self.district_colors.get(district), color,
                f"{district} row color must not change",
            )

    def test_atlanta_resolves_via_normalize_district(self):
        """district_colors lookups go through normalize_district — the map key
        must be reachable from real-world spellings."""
        for spelling in ("Atlanta", "atlanta", "ATLANTA", " Atlanta "):
            normalized = self.normalize_district(spelling)
            self.assertIn(
                normalized, self.district_colors,
                f"{spelling!r} normalizes to {normalized!r} which has no row color",
            )

    def test_row_fill_lookup_hits_color(self):
        """Replicates the renderer: district_colors.get(normalize_district(row.district))."""
        row_fill = self.district_colors.get(self.normalize_district("Atlanta"), (245, 245, 245))
        self.assertEqual(row_fill, (189, 49, 120))


if __name__ == "__main__":
    unittest.main()

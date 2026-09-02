"""Verge-style band texture + brand naming regression tests (Automation).

Static (AST/string) checks - no tkinter/display needed, CI-safe:

* theme_manager ships draw_band_texture and stores settings under
  GFH-Telecom (prefix in caps).
* header_manager paints header + footer bands and keeps the lazy-tkinter
  import that prevents a frozen-exe NameError.
* The modular GUI instantiates ThemeManager with app_name="VidaPay-GFH".
"""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

THEME_MANAGER = ROOT / "theme_manager.py"
HEADER_MANAGER = ROOT / "header_manager.py"
APP_FILE = ROOT / "gfh_audit" / "gui" / "app.py"


def _source(path):
    assert path.exists(), f"missing expected source file: {path}"
    return path.read_text(encoding="utf-8")


def test_theme_manager_has_band_texture_painter():
    tree = ast.parse(_source(THEME_MANAGER))
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "draw_band_texture" in names
    src = _source(THEME_MANAGER)
    assert "create_oval" in src
    assert "_HEADER_CIRCLES" in src and "_FOOTER_CIRCLES" in src
    assert 'tag_lower("band_texture")' in src


def test_config_dir_uses_caps_prefix():
    src = _source(THEME_MANAGER)
    assert '"GFH-Telecom"' in src
    assert '"gfh-telecom"' not in src


def test_header_manager_paints_header_and_footer_bands():
    src = _source(HEADER_MANAGER)
    assert "draw_band_texture" in src
    assert 'painter(canvas, "header")' in src
    assert 'painter(canvas, "footer")' in src
    assert "self.texture_canvas.lift(self.title_label)" in src
    assert "self.footer_canvas.lift(self.copyright_label)" in src


def test_header_manager_keeps_lazy_tkinter_import_in_add_copyright():
    src = _source(HEADER_MANAGER)
    assert re.search(
        r"def add_copyright\(.*?\)\s*:\s*\n(?:.*\n)*?\s*import tkinter as tk", src
    ), "add_copyright must import tkinter lazily before using tk"


def test_gui_uses_vidapay_gfh_app_name():
    src = _source(APP_FILE)
    assert 'app_name="VidaPay-GFH"' in src
    assert "vidapay-gfh" not in src

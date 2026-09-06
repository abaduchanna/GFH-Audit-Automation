"""The bot must never un-maximize the WhatsApp Desktop window when focusing it.

Regression test for: "why whatsapp window layout changed when bot focus it to
send text" — _force_focus_whatsapp() used to call ShowWindow(hwnd, SW_RESTORE)
unconditionally. On Windows, SW_RESTORE applied to a MAXIMIZED window
un-maximizes it, so WhatsApp visibly re-flowed its whole layout on every send.

The fix gates the restore behind IsIconic(hwnd): only a MINIMIZED window may
be restored; a maximized window is left maximized (SetForegroundWindow works
either way).
"""
import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
APP_FILES = [
    REPO / "GFH_Inventory_Audit_Timesheet.py",
    REPO / "GFH_Inventory_Audit.py",
]


def _find_force_focus(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "WhatsAppSender":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "_force_focus_whatsapp":
                    return item
    return None


def _calls(node: ast.AST, name: str):
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            if (isinstance(fn, ast.Attribute) and fn.attr == name) or (
                isinstance(fn, ast.Name) and fn.id == name
            ):
                out.append(sub)
    return out


def _contains_call(node: ast.AST, name: str) -> bool:
    return bool(_calls(node, name))


@pytest.mark.parametrize("app", APP_FILES, ids=lambda p: p.name)
def test_showwindow_restore_only_when_minimized(app):
    tree = ast.parse(app.read_text(encoding="utf-8"))
    fn = _find_force_focus(tree)
    assert fn is not None, f"_force_focus_whatsapp not found in {app.name}"

    # 1) An IsIconic gate must exist.
    iconic_ifs = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.If) and _contains_call(n.test, "IsIconic")
    ]
    assert iconic_ifs, "ShowWindow(SW_RESTORE) must be gated behind IsIconic(hwnd)"

    # 2) EVERY ShowWindow call must live inside the IsIconic-gated branch —
    #    an unconditional SW_RESTORE un-maximizes WhatsApp on every send.
    gated_ids = set()
    for iff in iconic_ifs:
        for sub in ast.walk(iff):
            gated_ids.add(id(sub))
    show_calls = _calls(fn, "ShowWindow")
    assert show_calls, "ShowWindow(SW_RESTORE) should still exist for minimized windows"
    for call in show_calls:
        assert id(call) in gated_ids, (
            "unconditional ShowWindow(SW_RESTORE) found — it un-maximizes a "
            "maximized WhatsApp window and re-flows its layout"
        )

    # 3) The window must still be focused.
    assert _calls(fn, "SetForegroundWindow"), "SetForegroundWindow must still be called"

"""Regression tests: B2B sign-in workflow after the access code step.

The post-credentials sign-in flow was cloned from the VidaPay extractor
(handle_ibm_verify_and_setup → finish_setup_steps →
complete_remaining_setup_next_flow) into this repo's B2BSoftScraper login
machine. Before the port the login stalled after credentials: no Trust
Device radio selection (#trustRadio), no #setupNextBtn walk, no Security
Upgrade handling, and the body-text state checks silently died on a
missing By import.

These tests pin the port in the shipped source (AST-level — no live
portal, no Selenium needed).
"""
import ast
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "GFH_Inventory_Audit_Automation.py")

HELPERS = (
    "_b2b_click_button",
    "_b2b_has_any_setup_next_button",
    "_b2b_select_trust_radio",
    "_b2b_wait_for_states",
    "_b2b_find_later_buttons",
    "_b2b_clear_later_alerts",
    "_b2b_complete_setup_next_flow",
)


def _read():
    with open(SCRIPT, encoding="utf-8") as f:
        return f.read()


def _function_src(src, name):
    m = re.search(rf"\ndef {name}\(.*?(?=\ndef |\nclass )", src, re.S)
    assert m, f"function {name} not found"
    return m.group(0)


def _branch_src(fn_src, marker):
    """Source from `marker` up to the next sibling `if state ==` check."""
    idx = fn_src.find(marker)
    assert idx >= 0, f"branch marker not found: {marker}"
    nxt = re.search(r"\n        if state ==", fn_src[idx + 10:])
    end = idx + 10 + nxt.start() if nxt else len(fn_src)
    return fn_src[idx:end]


def test_monolith_parses():
    ast.parse(_read())


def test_all_ported_helpers_exist():
    src = _read()
    for name in HELPERS:
        assert f"\ndef {name}(" in src, f"missing ported helper: {name}"


def test_trust_radio_js_force_check():
    body = _function_src(_read(), "_b2b_select_trust_radio")
    for fragment in (
        "querySelector('#trustRadio')",
        "removeAttribute('disabled')",
        "radio.checked = true",
        "new Event('input', { bubbles: true })",
        "new Event('change', { bubbles: true })",
    ):
        assert fragment in body, f"trust radio JS missing: {fragment}"


def test_setup_next_clicks_use_setupnextbtn_id():
    body = _function_src(_read(), "_b2b_complete_setup_next_flow")
    # Trust Device Next and Setup Next both click the #setupNextBtn control
    assert body.count('button_id="setupNextBtn"') >= 2, \
        "Trust Device and Setup Next steps must click #setupNextBtn"


def test_security_upgrade_never_clicks_next():
    fn_src = _function_src(_read(), "_b2b_complete_setup_next_flow")
    branch = _branch_src(fn_src, "if state == _B2B_STATE_SECURITY_UPG:")
    assert "NOT clicking Next" in branch, \
        "Security Upgrade branch must document why Next is not clicked"
    assert "_b2b_click_button" not in branch, \
        "Security Upgrade page must never get a Next click (error=io without selection)"
    assert "_b2b_wait_for_states" in branch, \
        "Security Upgrade branch must wait for the page to advance"


def test_page_state_detects_setup_next_and_security_upgrade():
    src = _read()
    fn_src = _function_src(src, "_b2b_get_page_state")
    assert '"twofactorsetup" in url' in fn_src
    assert "_b2b_has_any_setup_next_button" in fn_src
    # order matters: Ready To Go must win over the generic setup-next DOM check
    assert fn_src.find('"readytogo" in url') < fn_src.find("_b2b_has_any_setup_next_button")
    assert "secureupgradeoptions" in fn_src
    assert fn_src.count("_B2B_STATE_UNKNOWN") < fn_src.count("return _B2B_STATE_"), \
        "state classifier should return specific states"


def test_setup_next_detection_requires_twofactor_url():
    fn_src = _function_src(_read(), "_b2b_get_page_state")
    m = re.search(r'if "twofactor" in url and _b2b_has_any_setup_next_button\(driver\):', fn_src)
    assert m, "setup-next DOM check must be gated on a twofactor URL (login pages carry no such URL)"


def test_finish_flow_delegates_and_clears_alerts():
    fn_src = _function_src(_read(), "_b2b_finish_login_flow")
    assert "_b2b_complete_setup_next_flow(driver" in fn_src, \
        "finish flow must delegate the setup walk"
    for state in ("_B2B_STATE_TRUST_DEVICE", "_B2B_STATE_SETUP_NEXT",
                  "_B2B_STATE_READY_TO_GO", "_B2B_STATE_SECURITY_UPG"):
        assert state in fn_src, f"finish flow must route {state} into the setup walk"
    assert "_b2b_clear_later_alerts(driver, log=log)" in fn_src, \
        "portal/alert popups must be dismissed after login"


def test_get_body_has_local_by_import():
    fn_src = _function_src(_read(), "_b2b_get_body")
    assert re.search(r"def _b2b_get_body\(driver\)[^\n]*:\n\s+from selenium.webdriver.common.by import By", fn_src), \
        "_b2b_get_body must import By locally (bare except used to swallow the NameError)"

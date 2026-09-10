"""Regression tests for the 2026-09-10 scheduler round:

1. Mode-aware monitoring tabs — B2B + GFH app always, WhatsApp Web ONLY when
   WhatsApp Web mode is selected, with an accurate "Opened … tabs." log line
   (the old code always opened WhatsApp and logged "Opened B2B and TS tabs.").
2. pytesseract detection like the VidaPay Transfer Bot — module-level guarded
   import (PyInstaller bundles it), module-level tesseract_cmd, tesseract
   BINARY auto-install (winget → silent installer) instead of disabling the
   OCR monitor, and pytesseract shipped via requirements.txt + spec
   hiddenimports (it was missing from the CI build env, so the frozen EXE
   reported "pytesseract Python package not installed").
3. Adaptive B2B AccountId wait — the SSO login page may not expose #AccountId;
   the flow now polls AccountId + Username together, types what appears, and
   logs the page's visible fields every 15s so stalls are self-explaining.
4. No doubled "[B2B] [B2B]" log prefixes.
"""
import ast
import os
import textwrap

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "GFH_Inventory_Audit_Automation.py")

with open(SCRIPT, encoding="utf-8") as _f:
    SRC = _f.read()
TREE = ast.parse(SRC)


def _get_source(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(SRC, node)
    raise AssertionError(f"function {name} not found")


def _get_class(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


# ── 1. Mode-aware monitoring tabs ───────────────────────────────────────────

def test_open_monitoring_tabs_is_mode_aware():
    src = _get_source("open_monitoring_tabs")
    assert "include_whatsapp" in src
    # Opens B2B and GFH app unconditionally, WhatsApp conditionally
    assert '"B2B"' in src and '"GFH app"' in src
    assert "if include_whatsapp" in src and '"WhatsApp Web"' in src
    # Returns the opened names so callers can log accurately
    assert "return opened" in src


def test_scheduler_opens_tabs_mode_aware_and_logs():
    src = _get_source("_sched_open_tabs")
    assert 'wa_mode_var' in src
    assert '.get() == "web"' in src
    assert "include_whatsapp=include_wa" in src
    startup = _get_source("_startup_sequence") if False else None
    # _startup_sequence is nested; check it via raw source
    assert '_sched_open_tabs()' in SRC
    assert '_humanize_list(names) + " tabs."' in SRC


def test_old_ts_tab_message_gone():
    assert "Opened B2B and TS tabs." not in SRC


def test_humanize_list_runtime():
    ns = {"__builtins__": __builtins__}
    exec(compile(textwrap.dedent(_get_source("_humanize_list")), "<t>", "exec"), ns)
    f = ns["_humanize_list"]
    assert f([]) == ""
    assert f(["B2B"]) == "B2B"
    assert f(["B2B", "GFH app"]) == "B2B, and GFH app"
    assert f(["B2B", "GFH app", "WhatsApp Web"]) == "B2B, GFH app, and WhatsApp Web"


# ── 2. pytesseract detection like VidaPay Transfer Bot ──────────────────────

def test_pytesseract_module_level_import():
    # import pytesseract must be at module level (guarded), not lazily inside
    # a method — PyInstaller bundles module-level imports reliably.
    assert "try:\n    import pytesseract\n    PYTESSERACT_AVAILABLE = True" in SRC
    assert "PYTESSERACT_AVAILABLE = False" in SRC
    # tesseract_cmd assigned at module level right after _locate_tesseract
    assert "pytesseract.pytesseract.tesseract_cmd = _locate_tesseract()" in SRC


def test_tesseract_binary_auto_install_present():
    src = _get_source("_install_tesseract_binary")
    assert "UB-Mannheim.TesseractOCR" in src          # winget path
    assert '"/S"' in src                              # silent installer fallback
    assert "_is_tesseract_installed()" in src         # verified after install


def test_ocr_entry_installs_then_runs():
    src = _get_source("_whatsapp_ocr_entry")
    assert "_install_tesseract_binary" in src
    assert "_refresh_tesseract_path()" in src
    assert "PYTESSERACT_AVAILABLE" in src
    assert "_whatsapp_ocr_loop()" in src
    loop = _get_source("_whatsapp_ocr_loop")
    # the loop itself must no longer bail with the old lazy-import messages
    assert "pytesseract Python package not installed" not in loop
    assert "import pytesseract" not in loop


def test_pytesseract_shipped_in_requirements_and_spec():
    req = open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8").read()
    assert "pytesseract" in req
    spec = open(os.path.join(ROOT, "GFH_Inventory_Audit_Automation.spec"), encoding="utf-8").read()
    assert "'pytesseract'," in spec


def test_dev_mode_auto_install_includes_pytesseract():
    fn = None
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == "_auto_install_packages":
            fn = node
            break
    assert fn is not None
    tuples = [n for n in ast.walk(fn) if isinstance(n, ast.Tuple)]
    consts = [tuple(c.value for c in t.elts) for t in tuples
              if len(t.elts) == 2 and all(isinstance(e, ast.Constant) for e in t.elts)]
    assert ("pytesseract", "pytesseract") in consts


# ── 3. Adaptive B2B AccountId wait ──────────────────────────────────────────

def test_b2b_login_adaptive_account_id():
    login = _get_source("login")
    assert "No Account ID field on this page — going straight to Username." in login
    assert 'find_elements(By.ID, "AccountId")' in login
    assert 'find_elements(By.ID, "Username")' in login
    # self-diagnosing stall: visible-field snapshot logged while waiting
    assert "Visible fields on page:" in login
    assert "_visible_fields_snapshot" in login


def test_b2b_account_id_optional():
    login = _get_source("login")
    assert "Account ID is optional" in login
    # credentials check must not require account_id anymore
    assert "not all([self.company_id, self.username, self.password])" in login


def test_b2b_empty_account_id_left_empty():
    login = _get_source("login")
    assert "no Account ID saved — leaving it empty" in login


# ── 4. No doubled [B2B] prefixes ────────────────────────────────────────────

def test_b2b_scraper_strips_hardcoded_prefix():
    cls = _get_class("B2BSoftScraper")
    init = None
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == "__init__":
            init = ast.get_source_segment(SRC, node)
    assert init is not None
    assert "_log_no_double_prefix" in init
    assert 'startswith("[B2B] ")' in init


# ── 5. Round 2: blank-tab reuse, SSO company-ID stage, thread-safe OCR log ──

def test_find_or_open_tab_reuses_blank_tab():
    src = _get_source("_find_or_open_tab")
    assert "_BLANK_TAB_URLS" in SRC and "about:blank" in SRC
    assert "blank_handle" in src
    # navigates the blank tab to the URL instead of window.open only
    assert "driver.get(url)" in src


def test_b2b_handles_sso_company_id_stage():
    login = _get_source("login")
    # The SSO login page shows ONLY #companyId (+ Edit/Clear/Submit) first;
    # the flow must submit it before Username/Password can appear.
    assert "sso_submits" in login
    assert "SSO company-ID page — company ID submitted" in login
    assert "sso_submits < 3" in login
    # types the company ID into the SSO field when its value differs
    assert 'get_attribute("value")' in login
    # step-1 transition wait breaks early once sso.b2bsoft.com is reached
    assert '"sso.b2bsoft.com" in (drv.current_url or "")' in login


def test_ocr_entry_logs_on_main_thread():
    src = _get_source("_whatsapp_ocr_entry")
    assert "_olog" in src
    assert "self.after(0" in src
    # no direct widget access from the background thread
    assert "self._log_scheduler(" not in src.replace("lambda mm=str(m): self._log_scheduler(mm)", "")

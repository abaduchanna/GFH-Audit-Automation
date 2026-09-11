"""Regression tests for the 2026-09-12 scheduler round:

1. Today-only fallback filter (user rule: "when you failed to download
   today's inventory count details then only select today's details from
   the sheet") — a Month-to-Date file made ONE employee appear at SEVERAL
   stores (his counts from earlier days at other stores). The filter must
   trigger on a multi-day filename range OR multi-day row dates, keep only
   today's rows, and fall back to the file's LATEST day when no row is
   dated today (never an empty audit).
2. Global browser lock — the startup tab-open, the export cycle's tab
   pre-open, both scraper drivers and WhatsApp tab recovery all ran
   check-then-act against each other ("automation is fighting over opening
   tabs"): tab ops must be serialized by _BROWSER_LOCK and the whole
   export cycle must hold it across tab pre-open + both scrapes.
3. The real "No date-range dropdown found" root cause — _select_date_range_today
   used `drv` without ever assigning it (it is a method, NOT nested inside
   login()); every drv.execute_script raised NameError, the bare except
   swallowed it, and the run ALWAYS fell back to the Month-to-Date range.
   It must bind drv = self.driver, scan same-origin iframes, and verify.
4. Late-evaluated error lambdas — `except ... as exc` deletes `exc` at
   block exit; scheduled `lambda: ...{exc}` callbacks NameError'd on the Tk
   thread so "⚠ B2B export error: …" never appeared. They must bind the
   exception via a default argument.
"""
import ast
import datetime
import re as _re
import textwrap
from typing import Dict, Iterable, List, Optional, Tuple
import os

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


def _exec_filter_ns():
    """Namespace with the filter function + its real dependencies exec'd."""
    ns = {
        "dt": datetime,
        "re": _re,
        "List": List, "Dict": Dict, "Tuple": Tuple, "Optional": Optional,
        "Iterable": Iterable,
    }
    for helper in ("safe_text", "normalize_header", "excel_serial_to_datetime",
                   "find_column", "filter_inventory_rows_to_today"):
        code = textwrap.dedent(_get_source(helper))
        exec(compile(code, "<" + helper + ">", "exec"), ns)
    return ns


def _serial(day: datetime.date) -> str:
    """Excel serial (1899-12-30 base) as text — the real Created Date form."""
    return str((day - datetime.date(1899, 12, 30)).days)


def _row(store: str, day: datetime.date, created_by: str = "rep") -> Dict[str, str]:
    return {
        "Store": store,
        "District": "Colorado East",
        "Status": "Matched",
        "Created By": created_by,
        "Created Date": _serial(day),
    }


# ── 1. Today-only fallback filter (runtime) ────────────────────────────────

def test_filter_drops_older_days_on_multiday_filename():
    ns = _exec_filter_ns()
    today = datetime.date.today()
    old = today - datetime.timedelta(days=9)
    records = [
        _row("Boulder Store", old, "Muhammad Shoaib"),      # MTD leftovers
        _row("Kipling Store", old, "Muhammad Shoaib"),
        _row("Boulder Store", today, "Syed Mudabbir"),
        _row("Airline Store", today, "Shabbeer Khalidi"),
    ]
    name = "Inventory_Count_Result_Details_09012026-09112026.Xlsx"
    filtered, m = ns["filter_inventory_rows_to_today"](records, source_name=name)
    assert m["today_filter_mode"] == "filename-range"
    assert m["today_rows_dropped"] == 2
    assert m["today_rows_kept"] == 2
    stores = {r["Store"] for r in filtered}
    assert stores == {"Boulder Store", "Airline Store"}
    # the employee's stale store rows are gone — no more rep at many stores
    assert all(r["Created By"] != "Muhammad Shoaib" for r in filtered)


def test_filter_triggers_on_multiday_data_without_filename():
    ns = _exec_filter_ns()
    today = datetime.date.today()
    old = today - datetime.timedelta(days=1)
    records = [_row("A Store", old), _row("B Store", today), _row("C Store", today)]
    filtered, m = ns["filter_inventory_rows_to_today"](records, source_name="renamed.xlsx")
    assert m["today_filter_mode"] == "multi-day-data"
    assert [r["Store"] for r in filtered] == ["B Store", "C Store"]


def test_filter_leaves_todaysingle_day_file_untouched():
    ns = _exec_filter_ns()
    today = datetime.date.today()
    records = [_row("A Store", today), _row("B Store", today)]
    filtered, m = ns["filter_inventory_rows_to_today"](records, source_name="renamed.xlsx")
    assert m["today_filter_mode"] == "off"
    assert filtered == records


def test_filter_falls_back_to_latest_day_when_today_missing():
    ns = _exec_filter_ns()
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    older = yesterday - datetime.timedelta(days=5)
    records = [_row("A Store", older), _row("B Store", yesterday)]
    filtered, m = ns["filter_inventory_rows_to_today"](
        records, source_name="Inventory_Count_Result_Details_09012026-09112026.Xlsx")
    # never an empty audit: the file's LATEST day wins, never a mix
    assert m["today_target_date"] == yesterday.isoformat()
    assert [r["Store"] for r in filtered] == ["B Store"]


def test_filter_off_when_no_date_column():
    ns = _exec_filter_ns()
    records = [{"Store": "A Store", "District": "X"}]
    name = "Inventory_Count_Result_Details_09012026-09112026.Xlsx"
    filtered, m = ns["filter_inventory_rows_to_today"](records, source_name=name)
    assert m["today_filter_mode"] == "off"
    assert filtered == records


def test_load_variances_applies_the_filter():
    lv = _get_source("load_variances")
    assert "filter_inventory_rows_to_today(" in lv
    assert "source_name=os.path.basename(inventory)" in lv
    assert "[Today filter]" in lv  # visible in the scheduler log


# ── 2. Global browser lock — no more tab fighting ──────────────────────────

def test_browser_lock_exists_and_serializes_tab_ops():
    assert "_BROWSER_LOCK = threading.RLock()" in SRC
    tab = _get_source("_find_or_open_tab")
    # the whole check-then-act body must be atomic
    assert "with _BROWSER_LOCK:" in tab
    # ladder markers the older pins rely on are still present
    assert "switch_to.new_window" in tab and "Target.createTarget" in tab


def test_open_monitoring_tabs_holds_lock_per_batch():
    src = _get_source("open_monitoring_tabs")
    assert "with _BROWSER_LOCK:" in src
    assert "_find_or_open_tab(driver, url, log=log)" in src
    assert "_ensure_edge_open(port, log=log)" in src  # retry ladder intact


def test_export_cycle_holds_browser_lock_across_scrapes():
    src = _get_source("_scheduler_run_export_cycle")
    assert "_BROWSER_LOCK.acquire()" in src
    assert "_BROWSER_LOCK.release()" in src
    # scrapes run INSIDE the locked region (make_driver before the release)
    assert src.index("_BROWSER_LOCK.acquire()") < src.index("brs._make_driver()")
    assert src.index("ts._make_driver()") < src.index("_BROWSER_LOCK.release()")


def test_export_cycle_lock_cannot_leak_on_make_driver_failure():
    """A scraper _make_driver() failure used to kill the thread before the
    export-cycle lock's release ran — every later cycle then logged
    'already running' and silently skipped for the whole session."""
    src = _get_source("_scheduler_run_export_cycle")
    # outer try/finally releases BOTH locks even on construction failures
    assert src.index("_BROWSER_LOCK.acquire()") < src.index("brs._make_driver()")
    assert src.index("brs._make_driver()") < src.index("except Exception as exc:")
    assert "finally:" in src
    assert src.index("_BROWSER_LOCK.release()") < src.index("threading.Thread(target=_run")


def test_scheduled_error_lambdas_bind_the_exception():
    """`except ... as exc` deletes `exc` at block exit — bare `lambda: …exc`
    callbacks NameError'd on the Tk thread, so error messages silently
    never appeared. Default-arg binding is required."""
    assert _re.search(r"after\(0, lambda: [^\n]*\{exc\}", SRC) is None
    assert "lambda e=exc: self._log_scheduler" in _get_source("_scheduler_run_export_cycle")


# ── 3. Date-range dropdown — drv NameError root cause + iframes ────────────

def test_select_date_range_today_binds_drv():
    src = _get_source("_select_date_range_today")
    assert "drv = self.driver" in src


def test_select_date_range_today_scans_iframes():
    src = _get_source("_select_date_range_today")
    assert "_try_set_today_everywhere" in src
    assert 'find_elements(By.TAG_NAME, "iframe")' in src
    assert "switch_to.frame(" in src
    assert "switch_to.default_content()" in src


def test_login_waits_for_url_change_or_portal_state():
    """The old blind 15s URL-only wait always expired on the SPA portal with
    a scary 'URL did not change' — portal state must be accepted as success."""
    login = _get_source("login")
    assert "_b2b_get_page_state(drv) == _B2B_STATE_PORTAL" in login
    assert "Portal reached after login (URL unchanged — SPA)." in login
    assert "URL did not change after login click" not in login


def test_sso_and_ladder_pins_untouched():
    login = _get_source("login")
    assert '"rsubmit", "submit", "enter", "formsubmit"' in login
    assert "sso_submits >= 4" in login

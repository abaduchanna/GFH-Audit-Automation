"""Selenium WebDriver lifecycle with a persistent browser profile.

Two modes (mirroring the VidaPay Transfer Bot pattern):

* ATTACH MODE (default) — a real Microsoft Edge window is launched with
  ``--remote-debugging-port=9226`` and a dedicated automation profile. Selenium
  then attaches through ``debuggerAddress=127.0.0.1:9226``. The window keeps
  the user's extensions and stays logged in across runs (WhatsApp Web QR is
  scanned only once, portal cookies persist).
* STANDALONE MODE — the driver launches the browser itself with a
  user-data-dir (headless-friendly).
"""
from __future__ import annotations

import json
import logging
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

from selenium import webdriver
from selenium.common.exceptions import (
    InvalidSessionIdException,
    NoSuchWindowException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait

logger = logging.getLogger("gfh.audit.whatsapp.driver")

DEFAULT_EDGE_DEBUG_PORT = 9226
DEFAULT_EDGE_PROFILE_DIR = r"C:\GFH_Edge_Automation_Profile"


def get_edge_exe_path() -> Optional[str]:
    """Locate the Microsoft Edge executable (same search order as VidaPay)."""
    candidates = [
        shutil.which("msedge"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    return None


def is_port_open(host: str = "127.0.0.1", port: int = DEFAULT_EDGE_DEBUG_PORT, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


class DriverManager:
    """Owns the single shared browser used for WhatsApp Web + portal scraping."""

    def __init__(
        self,
        profile_dir: Path,
        browser: str = "edge",
        headless: bool = False,
        attach: bool = True,
        debug_port: int = DEFAULT_EDGE_DEBUG_PORT,
        edge_profile_dir: str = DEFAULT_EDGE_PROFILE_DIR,
        download_dir: Optional[Path] = None,
    ):
        self.profile_dir = Path(profile_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.browser = (browser or "edge").lower()
        self.headless = headless
        # Edge attach-to-open-browser settings (ignored for chrome/standalone)
        self.attach = bool(attach)
        self.debug_port = int(debug_port or DEFAULT_EDGE_DEBUG_PORT)
        self.edge_profile_dir = Path(edge_profile_dir or DEFAULT_EDGE_PROFILE_DIR)
        self.download_dir = Path(download_dir) if download_dir else None
        self.driver: Optional[WebDriver] = None
        self.retry_count = 0
        self.is_valid = False

    # -- options -------------------------------------------------------------
    def _chrome_options(self):
        options = webdriver.ChromeOptions()
        options.add_argument(f"--user-data-dir={self.profile_dir}")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--window-size=1400,950")
        if self.headless:
            options.add_argument("--headless=new")
        prefs = {
            "download.default_directory": str(self.profile_dir.parent / "portal_downloads"),
            "download.prompt_for_download": False,
            "safebrowsing.enabled": True,
        }
        options.add_experimental_option("prefs", prefs)
        return options

    def _edge_options(self):
        options = webdriver.EdgeOptions()
        options.add_argument(f"--user-data-dir={self.profile_dir}")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--window-size=1400,950")
        if self.headless:
            options.add_argument("--headless=new")
        return options

    # -- edge attach helpers ---------------------------------------------------
    def _seed_edge_download_prefs(self) -> None:
        """Write the download directory into the automation profile's
        Preferences so portal exports land in portal_downloads even though the
        browser is launched outside Selenium (attach mode cannot set prefs)."""
        if not self.download_dir:
            return
        try:
            self.download_dir.mkdir(parents=True, exist_ok=True)
            prefs_path = self.edge_profile_dir / "Default" / "Preferences"
            prefs_path.parent.mkdir(parents=True, exist_ok=True)
            prefs: dict = {}
            if prefs_path.exists():
                try:
                    prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
                except Exception:
                    prefs = {}
            download = prefs.setdefault("download", {})
            download["default_directory"] = str(self.download_dir)
            download["prompt_for_download"] = False
            download["directory_upgrade"] = True
            prefs_path.write_text(json.dumps(prefs), encoding="utf-8")
            logger.debug("Seeded Edge download prefs -> %s", prefs_path)
        except Exception as exc:
            logger.warning("Could not seed Edge download prefs: %s", exc)

    def _launch_edge_subprocess(self) -> bool:
        """Open a real Edge window with the remote-debugging port (VidaPay style)."""
        edge_path = get_edge_exe_path()
        if not edge_path:
            logger.error("Microsoft Edge executable not found")
            return False
        self.edge_profile_dir.mkdir(parents=True, exist_ok=True)
        self._seed_edge_download_prefs()
        args = [
            edge_path,
            f"--remote-debugging-port={self.debug_port}",
            f"--user-data-dir={self.edge_profile_dir}",
            "--profile-directory=Default",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ]
        try:
            subprocess.Popen(args)
            logger.info("Launched automation Edge (port %s, profile %s)", self.debug_port, self.edge_profile_dir)
        except Exception as exc:
            logger.error("Failed to launch Edge: %s", exc)
            return False
        for _ in range(20):
            if is_port_open(port=self.debug_port):
                time.sleep(0.5)  # give DevTools a moment to settle
                return True
            time.sleep(0.5)
        logger.error("Edge remote debugging port %s never became ready", self.debug_port)
        return False

    @staticmethod
    def _prepare_automation_tab(driver: WebDriver) -> bool:
        """Attach to a real content tab (skip edge:// pages / downloads popup)."""
        chrome_prefixes = ("edge://", "chrome://", "devtools://", "edge-extension://", "chrome-extension://")
        try:
            for handle in driver.window_handles:
                try:
                    driver.switch_to.window(handle)
                    url = (driver.current_url or "").lower().strip()
                    if url and not url.startswith(chrome_prefixes):
                        return True
                except Exception:
                    continue
            # every tab is an internal page - open a fresh content tab
            driver.switch_to.new_window("tab")
            driver.get("about:blank")
            return True
        except Exception as exc:
            logger.warning("Could not prepare an automation tab: %s", exc)
            return False

    def _make_edge_attach(self) -> WebDriver:
        """Attach to the open automation Edge window (launch it if needed)."""
        if not is_port_open(port=self.debug_port):
            logger.info("Automation Edge not open on port %s - launching it", self.debug_port)
            if not self._launch_edge_subprocess():
                raise WebDriverException(
                    f"Edge is not reachable on remote debugging port {self.debug_port} "
                    "and could not be launched automatically"
                )
        options = webdriver.EdgeOptions()
        options.add_experimental_option("debuggerAddress", f"127.0.0.1:{self.debug_port}")
        options.set_capability("unhandledPromptBehavior", "dismiss")
        options.set_capability("acceptInsecureCerts", True)
        driver = self._new_edge_driver(options)
        if not self._prepare_automation_tab(driver):
            try:
                driver.quit()
            except Exception:
                pass
            raise WebDriverException(
                "Edge is open, but Selenium could not attach to a normal browser tab. "
                "Close any Edge popup/flyout and try again."
            )
        logger.info("Attached to automation Edge via 127.0.0.1:%s", self.debug_port)
        return driver

    # -- lifecycle ------------------------------------------------------------
    def initialize(self) -> bool:
        try:
            if self.browser == "edge" and self.attach:
                logger.info(
                    "Initializing Edge ATTACH mode (port=%s, profile=%s)",
                    self.debug_port, self.edge_profile_dir,
                )
            else:
                logger.info("Initializing %s WebDriver (profile=%s)...", self.browser, self.profile_dir)
            if self.browser == "edge":
                self.driver = self._make_edge()
            else:
                self.driver = self._make_chrome()
            self.driver.set_page_load_timeout(60)
            self.driver.set_script_timeout(60)
            try:
                self.driver.execute_cdp_cmd(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
                )
            except Exception:
                pass
            self.is_valid = True
            logger.info("WebDriver initialized")
            return True
        except Exception as exc:
            logger.error("Driver init failed: %s", exc)
            self.driver = None
            self.is_valid = False
            return False

    def _make_chrome(self) -> WebDriver:
        try:
            from webdriver_manager.chrome import ChromeDriverManager

            return webdriver.Chrome(
                options=self._chrome_options(),
                service=webdriver.ChromeService(ChromeDriverManager().install()),
            )
        except Exception:
            # Selenium Manager fallback (Selenium >= 4.6 ships its own resolver).
            return webdriver.Chrome(options=self._chrome_options())

    def _new_edge_driver(self, options) -> WebDriver:
        try:
            from webdriver_manager.microsoft import EdgeChromiumDriverManager

            return webdriver.Edge(
                options=options,
                service=webdriver.EdgeService(EdgeChromiumDriverManager().install()),
            )
        except Exception:
            # Selenium Manager fallback (Selenium >= 4.6 ships its own resolver).
            return webdriver.Edge(options=options)

    def _make_edge(self) -> WebDriver:
        if self.attach and not self.headless:
            return self._make_edge_attach()
        return self._new_edge_driver(self._edge_options())

    def _validate_session(self) -> bool:
        if self.driver is None:
            self.is_valid = False
            return False
        try:
            self.driver.window_handles
            self.is_valid = True
            return True
        except (InvalidSessionIdException, NoSuchWindowException, WebDriverException):
            self.is_valid = False
            return False

    def _recover_driver(self) -> None:
        logger.warning("Recovering WebDriver session...")
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        self.driver = None
        self.is_valid = False
        # Profile directory persists, so login cookies survive the restart.
        for _ in range(2):
            if self.initialize():
                return
            time.sleep(3)

    def navigate(self, url: str, timeout: int = 30) -> bool:
        try:
            if not self._validate_session() and not self.is_valid:
                return False
            self.driver.get(url)
            WebDriverWait(self.driver, timeout).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            logger.info("Navigated to %s", url)
            return True
        except TimeoutException:
            logger.warning("Navigation timeout for %s (continuing)", url)
            return True  # slow portals still usable
        except (InvalidSessionIdException, NoSuchWindowException):
            self._recover_driver()
            return False
        except Exception as exc:
            logger.error("Navigation failed for %s: %s", url, exc)
            return False

    def execute_script(self, script: str, *args):
        try:
            if not self._validate_session():
                return None
            return self.driver.execute_script(script, *args)
        except InvalidSessionIdException:
            self._recover_driver()
            return None
        except Exception as exc:
            logger.debug("execute_script error: %s", exc)
            return None

    def find_element_safe(self, by, value, timeout: int = 10, retry: int = 1):
        """Find element with retries; returns element or None (never raises)."""
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        for attempt in range(max(1, retry + 1)):
            try:
                if not self._validate_session():
                    return None
                return WebDriverWait(self.driver, timeout).until(
                    EC.presence_of_element_located((by, value))
                )
            except Exception:
                if attempt >= retry:
                    return None
                time.sleep(1)
        return None

    def quit(self) -> None:
        """End the WebDriver session.

        In attach mode the Edge window itself is left open (chromedriver did
        not launch it), so logins/cookies survive; the next initialize()
        simply re-attaches. Cookies also stay on disk in the profile.
        """
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        finally:
            self.driver = None
            self.is_valid = False
            if self.browser == "edge" and self.attach:
                logger.info("WebDriver session ended (automation Edge window left open)")
            else:
                logger.info("WebDriver closed (profile cookies retained)")

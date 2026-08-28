"""Selenium WebDriver lifecycle with a persistent browser profile.

The profile directory (whatsapp_web_profile) keeps WhatsApp Web cookies, so
the QR code only needs to be scanned once — exactly like a real browser.
"""
from __future__ import annotations

import logging
import shutil
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


class DriverManager:
    """Owns the single shared browser used for WhatsApp Web + portal scraping."""

    def __init__(self, profile_dir: Path, browser: str = "chrome", headless: bool = False):
        self.profile_dir = Path(profile_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.browser = (browser or "chrome").lower()
        self.headless = headless
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

    # -- lifecycle ------------------------------------------------------------
    def initialize(self) -> bool:
        try:
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

    def _make_edge(self) -> WebDriver:
        try:
            from webdriver_manager.microsoft import EdgeChromiumDriverManager

            return webdriver.Edge(
                options=self._edge_options(),
                service=webdriver.EdgeService(EdgeChromiumDriverManager().install()),
            )
        except Exception:
            return webdriver.Edge(options=self._edge_options())

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
        """Close the browser. Cookies stay on disk in the profile directory."""
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        finally:
            self.driver = None
            self.is_valid = False
            logger.info("WebDriver closed (profile cookies retained)")

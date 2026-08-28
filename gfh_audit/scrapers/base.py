"""Base class for portal scrapers sharing the Selenium driver."""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# BeautifulSoup for fast HTML parsing: one page_source fetch parsed locally
# instead of hundreds of Selenium DOM round-trips (VidaPay pattern).
try:
    from bs4 import BeautifulSoup

    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

logger = logging.getLogger("gfh.audit.scrapers")


class PortalScraperError(RuntimeError):
    pass


class PortalScraper(ABC):
    """Common login + download plumbing for the two web portals."""

    portal_name = "portal"

    def __init__(self, driver_manager, email: str, password: str, login_wait: int = 20):
        self.dm = driver_manager
        self.email = email
        self.password = password
        self.login_wait = login_wait

    # -- helpers -------------------------------------------------------------
    def log(self, message: str) -> None:
        logger.info("[%s] %s", self.portal_name, message)

    def _find_first(self, selectors, timeout: int = 8):
        for by, value in selectors:
            try:
                return WebDriverWait(self.dm.driver, timeout).until(
                    EC.presence_of_element_located((by, value))
                )
            except Exception:
                continue
        return None

    def _type(self, element, text: str) -> None:
        element.click()
        time.sleep(0.2)
        try:
            element.clear()
        except Exception:
            pass
        element.send_keys(text)

    # -- login ------------------------------------------------------------------
    EMAIL_SELECTORS = [
        (By.CSS_SELECTOR, "input[type='email']"),
        (By.CSS_SELECTOR, "input[name='email']"),
        (By.CSS_SELECTOR, "input[autocomplete*='email']"),
        (By.XPATH, "//input[contains(@placeholder,'mail') or contains(@name,'mail')]"),
    ]
    PASSWORD_SELECTORS = [
        (By.CSS_SELECTOR, "input[type='password']"),
        (By.XPATH, "//input[contains(@placeholder,'assword') or contains(@name,'assword')]"),
    ]
    SUBMIT_SELECTORS = [
        (By.XPATH, "//button[contains(translate(text(),'LOGIN','login'),'login') or contains(.,'Sign in') or contains(.,'Log in')]"),
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.CSS_SELECTOR, "input[type='submit']"),
        (By.XPATH, "//div[@role='button'][contains(.,'Login') or contains(.,'Sign in')]"),
    ]

    def login(self) -> bool:
        """Authenticate with the configured email/password."""
        if not self.email or not self.password:
            raise PortalScraperError(
                f"{self.portal_name} credentials are not configured in the Portal Credentials tab"
            )
        if not self.dm.is_valid and not self.dm.initialize():
            raise PortalScraperError("Browser driver could not be initialised")

        self.log(f"Navigating to {self.portal_url}")
        if not self.dm.navigate(self.portal_url, timeout=45):
            raise PortalScraperError(f"Could not open {self.portal_url}")
        time.sleep(2)

        # Already logged in from a previous run? (profile cookies persist)
        if self.is_authenticated():
            self.log("Session already authenticated")
            return True

        email_field = self._find_first(self.EMAIL_SELECTORS, timeout=self.login_wait)
        if email_field is None:
            if self.is_authenticated():
                return True
            raise PortalScraperError("Email input not found on login page")
        self._type(email_field, self.email)

        password_field = self._find_first(self.PASSWORD_SELECTORS, timeout=8)
        if password_field is None:
            raise PortalScraperError("Password input not found on login page")
        self._type(password_field, self.password)

        submit = self._find_first(self.SUBMIT_SELECTORS, timeout=5)
        if submit is not None:
            submit.click()
        else:
            from selenium.webdriver.common.keys import Keys

            password_field.send_keys(Keys.ENTER)

        self.log("Login submitted — waiting for authentication")
        deadline = time.time() + self.login_wait + 10
        while time.time() < deadline:
            if self.is_authenticated():
                self.log("Login successful")
                return True
            time.sleep(1)
        raise PortalScraperError(
            f"{self.portal_name} login did not complete within {self.login_wait + 10}s — check credentials"
        )

    # -- table scraping ------------------------------------------------------------
    TABLE_ROW_SELECTOR = "table tr, [role='row']"

    def scrape_tables_as_records(self, min_rows: int = 1) -> List[dict]:
        """Read visible HTML tables into list-of-dict records (header → key).

        Primary path fetches ``driver.page_source`` once and parses it with
        BeautifulSoup; falls back to Selenium DOM traversal when bs4 is not
        installed or parsing yields nothing."""
        if BS4_AVAILABLE:
            try:
                html = self.dm.driver.page_source
                if html:
                    records = self._parse_tables_bs4(html, min_rows)
                    if records:
                        return records
            except Exception as exc:
                self.log(f"BS4 table scraping warning: {exc}")
        return self._scrape_tables_selenium(min_rows)

    @staticmethod
    def _parse_tables_bs4(html: str, min_rows: int = 1) -> List[dict]:
        soup = BeautifulSoup(html, "html.parser")
        records: List[dict] = []
        tables = soup.select("table")
        if not tables:
            # role=grid/list tables used by some React portals
            tables = soup.select("div[role='table'], div[role='grid']")
        for table in tables:
            rows = table.select("tr")
            if not rows or len(rows) - 1 < min_rows:
                continue
            headers = [
                (cell.get_text(strip=True) or "Column")
                for cell in rows[0].select("th, td")
            ]
            if not headers:
                continue
            for row in rows[1:]:
                cells = row.select("td")
                if not cells:
                    continue
                record = {}
                for i, cell in enumerate(cells):
                    key = headers[i] if i < len(headers) else f"Column{i + 1}"
                    record[key] = cell.get_text(strip=True)
                if any(str(v).strip() for v in record.values()):
                    records.append(record)
        return records

    def _scrape_tables_selenium(self, min_rows: int = 1) -> List[dict]:
        records: List[dict] = []
        try:
            tables = self.dm.driver.find_elements(By.CSS_SELECTOR, "table")
            for table in tables:
                rows = table.find_elements(By.CSS_SELECTOR, "tr")
                if len(rows) - 1 < min_rows:
                    continue
                headers = []
                header_cells = rows[0].find_elements(By.CSS_SELECTOR, "th, td")
                for cell in header_cells:
                    headers.append((cell.text or "").strip() or "Column")
                if not headers:
                    continue
                for row in rows[1:]:
                    cells = row.find_elements(By.CSS_SELECTOR, "td")
                    if not cells:
                        continue
                    record = {}
                    for i, cell in enumerate(cells):
                        key = headers[i] if i < len(headers) else f"Column{i + 1}"
                        record[key] = (cell.text or "").strip()
                    if any(str(v).strip() for v in record.values()):
                        records.append(record)
        except Exception as exc:
            self.log(f"Table scraping warning: {exc}")
        return records

    def try_download_buttons(self, keywords: List[str], download_dir: Path, wait_seconds: int = 25) -> Optional[Path]:
        """Click any visible download/export button whose label matches keywords."""
        before = set(download_dir.glob("*")) if download_dir.exists() else set()
        xpath_parts = []
        for keyword in keywords:
            xpath_parts.append(
                f"contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword.lower()}')"
            )
        xpath = "//button[" + " or ".join(xpath_parts) + "] | //a[" + " or ".join(xpath_parts) + "]"
        try:
            buttons = self.dm.driver.find_elements(By.XPATH, xpath)
            for button in buttons[:5]:
                try:
                    if button.is_displayed():
                        self.log(f"Clicking download/export button: {(button.text or '')[:40]!r}")
                        button.click()
                        break
                except Exception:
                    continue
        except Exception as exc:
            self.log(f"Download button search failed: {exc}")
            return None

        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            time.sleep(1)
            if not download_dir.exists():
                continue
            new_files = {
                f for f in download_dir.glob("*")
                if f not in before and not f.name.endswith((".crdownload", ".tmp", ".part"))
            }
            if new_files:
                newest = max(new_files, key=lambda f: f.stat().st_mtime)
                self.log(f"Download completed: {newest.name}")
                return newest
        return None

    # -- contract ---------------------------------------------------------------
    @property
    @abstractmethod
    def portal_url(self) -> str: ...

    @abstractmethod
    def is_authenticated(self) -> bool: ...

    @abstractmethod
    def extract_for_district(self, district: str, download_dir: Path) -> dict:
        """Return {'records': [...], 'file_path': str|None, 'report_type': str}."""

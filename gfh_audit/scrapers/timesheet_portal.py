"""Timesheet portal scraper — https://gfh-telecom-app.web.app/timesheet

Authenticates with the configured email/password, then pulls the active
timesheet rows (per rep / store / district) either via an export download
button or by reading the rendered table."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List, Optional

from selenium.webdriver.common.by import By

from .base import PortalScraper, PortalScraperError

logger = logging.getLogger("gfh.audit.scrapers.timesheet")


class TimesheetPortalScraper(PortalScraper):
    portal_name = "timesheet"

    @property
    def portal_url(self) -> str:
        return "https://gfh-telecom-app.web.app/timesheet"

    def is_authenticated(self) -> bool:
        try:
            # After login the timesheet view renders a table or the app chrome;
            # the login form (password field) is gone.
            if self.dm.driver.find_elements(By.CSS_SELECTOR, "input[type='password']"):
                return False
            if self.dm.driver.find_elements(
                By.CSS_SELECTOR, "table, [role='table'], .timesheet, #root main"
            ):
                return True
            # Firebase apps often render everything in #root/#app
            return bool(self.dm.driver.find_elements(By.CSS_SELECTOR, "#root, #app, main"))
        except Exception:
            return False

    def extract_for_district(self, district: str, download_dir: Path) -> dict:
        """Fetch the active timesheet data for a district.

        Returns {'records': [...], 'file_path': str|None, 'report_type': 'timesheet'}."""
        self.login()
        download_dir = Path(download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        time.sleep(2)

        # 1) try a native download/export first (best fidelity)
        file_path = self.try_download_buttons(
            ["export", "download", "csv", "xlsx", "excel"], download_dir, wait_seconds=25
        )

        # 2) fall back to scraping the rendered table
        records: List[dict] = []
        if file_path is None:
            records = self.scrape_tables_as_records()
            if not records:
                # some Firebase apps paginate — try clicking through pages
                records = self._scrape_paginated()
            if not records:
                raise PortalScraperError(
                    "Timesheet data could not be extracted (no download button and no table rows)"
                )
            self.log(f"Scraped {len(records)} timesheet rows from rendered tables")
        else:
            records = self._read_downloaded_file(file_path)

        district_rows = [r for r in records if self._row_matches_district(r, district)] or records
        return {
            "records": district_rows,
            "file_path": str(file_path) if file_path else None,
            "report_type": "timesheet",
        }

    # -- internals ---------------------------------------------------------------
    def _scrape_paginated(self) -> List[dict]:
        all_records: List[dict] = []
        for _ in range(20):
            records = self.scrape_tables_as_records()
            if not records:
                break
            all_records.extend(records)
            next_button = self._find_first(
                [
                    (By.XPATH, "//button[contains(@aria-label,'next') or contains(.,'Next')]"),
                    (By.CSS_SELECTOR, "li[title='Next Page'] button, button[aria-label='Next Page']"),
                ],
                timeout=3,
            )
            if next_button is None:
                break
            try:
                if "disabled" in (next_button.get_attribute("class") or ""):
                    break
                next_button.click()
                time.sleep(1.5)
            except Exception:
                break
        return all_records

    @staticmethod
    def _read_downloaded_file(file_path: Path) -> List[dict]:
        suffix = file_path.suffix.lower()
        try:
            if suffix in (".xlsx", ".xlsm"):
                from ..xlsx_reader import read_xlsx_records

                return read_xlsx_records(file_path)
            if suffix == ".csv":
                import csv

                with file_path.open("r", newline="", encoding="utf-8-sig") as f:
                    return [dict(row) for row in csv.DictReader(f) if any(row.values())]
            if suffix == ".xls":
                # legacy format: convert via LibreOffice/excel not available — try pandas
                import pandas as pd

                frame = pd.read_excel(file_path, dtype=str)
                frame.columns = [str(c).strip() for c in frame.columns]
                return frame.fillna("").to_dict("records")
        except Exception as exc:
            logger.warning("Could not parse downloaded timesheet %s: %s", file_path.name, exc)
        return []

    @staticmethod
    def _row_matches_district(record: dict, district: str) -> bool:
        if not district:
            return True
        from ..textutils import normalize_district, normalize_header

        wanted = normalize_district(district).lower()
        for key, value in record.items():
            if normalize_header(key) in {"district", "region", "market"}:
                if wanted in str(value).lower():
                    return True
        return False

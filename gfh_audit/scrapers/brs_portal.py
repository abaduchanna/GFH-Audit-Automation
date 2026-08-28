"""BRS / count sheet portal scraper — wsreports.b2bsoft.com (platform=brs).

Authenticates with the configured email/password, then extracts the latest
store count sheet and inventory report for a district — via download buttons
when available, otherwise by scraping the rendered report tables."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List, Optional

from selenium.webdriver.common.by import By

from .base import PortalScraper, PortalScraperError

logger = logging.getLogger("gfh.audit.scrapers.brs")


class BRSCountSheetScraper(PortalScraper):
    portal_name = "brs"

    @property
    def portal_url(self) -> str:
        return "https://wsreports.b2bsoft.com/?platform=brs&performanceapp=0#"

    def is_authenticated(self) -> bool:
        try:
            if self.dm.driver.find_elements(By.CSS_SELECTOR, "input[type='password']"):
                return False
            return bool(
                self.dm.driver.find_elements(
                    By.CSS_SELECTOR, "table, [role='table'], #reportGrid, .report, main, #root"
                )
            )
        except Exception:
            return False

    def extract_for_district(self, district: str, download_dir: Path) -> dict:
        """Fetch the latest count sheet / inventory report for a district.

        Returns {'records': [...], 'file_path': str|None,
                 'report_type': 'count_sheet', 'inventory_file_path': str|None}."""
        self.login()
        download_dir = Path(download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        time.sleep(2)

        if not self._select_district_filter(district):
            self.log(f"District filter {district!r} not found — extracting all rows and filtering locally")

        # Count sheet report first
        count_sheet_file = self.try_download_buttons(
            ["count sheet", "countsheet", "download", "export"], download_dir, wait_seconds=30
        )
        records: List[dict] = []
        if count_sheet_file is not None:
            records = self._read_downloaded_file(count_sheet_file)
        else:
            records = self.scrape_tables_as_records()
            if not records:
                raise PortalScraperError(
                    "Count sheet data could not be extracted (no download button and no table rows)"
                )
            self.log(f"Scraped {len(records)} count-sheet rows from rendered tables")

        # Optional second pass: inventory report
        inventory_file: Optional[Path] = None
        try:
            inventory_file = self.try_download_buttons(
                ["inventory report", "inventory"], download_dir, wait_seconds=15
            )
        except Exception:
            pass

        district_records = [r for r in records if self._row_mentions(r, district)] or records
        return {
            "records": district_records,
            "file_path": str(count_sheet_file) if count_sheet_file else None,
            "inventory_file_path": str(inventory_file) if inventory_file else None,
            "report_type": "count_sheet",
        }

    # -- internals -----------------------------------------------------------------
    def _select_district_filter(self, district: str) -> bool:
        """Try to choose the district in any visible filter dropdown / search box."""
        try:
            selects = self.dm.driver.find_elements(By.CSS_SELECTOR, "select")
            for select in selects:
                options = select.find_elements(By.CSS_SELECTOR, "option")
                for option in options:
                    if district.lower() in (option.text or "").lower():
                        select.click()
                        time.sleep(0.4)
                        option.click()
                        time.sleep(1.5)
                        return True
            # search-style filters
            inputs = self.dm.driver.find_elements(
                By.CSS_SELECTOR,
                "input[type='search'], input[placeholder*='earch'], input[placeholder*='ilter']",
            )
            for field in inputs[:3]:
                field.clear()
                field.send_keys(district)
                time.sleep(1.5)
                return True
        except Exception as exc:
            self.log(f"District filter selection failed: {exc}")
        return False

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
            if suffix == ".pdf":
                # BRS sometimes exports PDF count sheets — return empty; the
                # audit engine can OCR it separately if needed.
                logger.info("PDF count sheet downloaded; table parse skipped: %s", file_path.name)
                return []
        except Exception as exc:
            logger.warning("Could not parse downloaded report %s: %s", file_path.name, exc)
        return []

    @staticmethod
    def _row_mentions(record: dict, district: str) -> bool:
        from ..textutils import normalize_district, normalize_header

        wanted = normalize_district(district).lower()
        for key, value in record.items():
            if normalize_header(key) in {"district", "region", "market"} and wanted in str(value).lower():
                return True
        return False

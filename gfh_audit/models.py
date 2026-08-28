"""Domain models shared across the audit engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class VarianceRow:
    key: str
    district: str
    store: str
    product: str
    imei: str
    status: str
    created_by: str = ""
    rep_name: str = ""
    created_date: str = ""
    document_status: str = ""
    source_file: str = ""
    cleared: bool = False
    sent_count: int = 0
    last_sent_at: str = ""
    cleared_at: str = ""
    cleared_via: str = ""          # "ocr" | "manual" | ""
    notes: str = ""


@dataclass
class InventoryStatusRow:
    key: str
    district: str
    store: str
    status: str
    rep_name: str = ""
    source_file: str = ""


@dataclass
class Employee:
    """A sales rep / employee with the phone number used for @mentions."""

    name: str
    phone: str = ""
    district: str = ""

    @property
    def name_key(self) -> str:
        from .textutils import person_name_key

        return person_name_key(self.name)


@dataclass
class TimesheetEntry:
    """One row scraped from the timesheet portal for a rep."""

    rep_name: str
    district: str = ""
    store: str = ""
    clock_in: str = ""
    clock_out: str = ""
    hours: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class CountSheetReport:
    """A downloaded count-sheet / inventory report from the BRS portal."""

    district: str = ""
    store: str = ""
    file_path: str = ""
    report_type: str = ""          # "count_sheet" | "inventory"
    downloaded_at: str = ""
    records: List[dict] = field(default_factory=list)

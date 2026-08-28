"""GFH Audit Automation — main tkinter application.

Tabs:
  • Inventory Audit Status   — per-store Completed/Pending view
  • Variance Audit           — variance rows, manual clear, export
  • Districts & Schedule     — per-district WhatsApp group + Audit Start Time
  • Employees                — rep → phone mapping used for @mentions
  • Portal Credentials       — Timesheet + BRS logins, reminders, OCR paths
  • Run & Logs               — Start / Start Now / Stop + live console
"""
from __future__ import annotations

import logging
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from .. import APP_TITLE, __version__
from ..config import AppConfig, ConfigStore, DistrictScheduleEntry, PortalCredentials
from ..database import VarianceDatabase
from ..models import InventoryStatusRow, VarianceRow
from ..ocr.engine import OcrEngine, find_ghostscript, find_tesseract
from ..paths import DB_PATH, EXPORT_DIR, STORE_CONFIG_PATH, ensure_runtime_dirs
from ..pipeline import build_inventory_status_rows, extract_variances
from ..scrapers.brs_portal import BRSCountSheetScraper
from ..scrapers.timesheet_portal import TimesheetPortalScraper
from ..textutils import normalize_district, now_text, parse_start_time, whatsapp_mention
from ..whatsapp.mentions import MentionResolver
from ..xlsx_reader import read_xlsx_records
from .widgets import LogConsole, make_treeview, sort_treeview

logger = logging.getLogger("gfh.audit.gui")


class GFHAuditApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE}  v{__version__}")
        self.geometry("1280x860")
        self.minsize(1100, 720)

        ensure_runtime_dirs()
        self.config_store = ConfigStore()
        self.config: AppConfig = self.config_store.load()
        self.db = VarianceDatabase(DB_PATH)

        self.engine = None  # created lazily on Start
        self.engine_lock = threading.RLock()

        # manual-load workbook state
        self.inventory_path_var = tk.StringVar()
        self.timesheet_path_var = tk.StringVar()
        self.loaded_keys: List[str] = []
        self.data_loaded = False

        self._build_styles()
        self._build_header()
        self._build_notebook()
        self._load_schedule_into_tree()
        self._load_employees_into_tree()
        self._refresh_status_table()
        self._refresh_variance_table()
        self._refresh_district_runs()

    # ------------------------------------------------------------------ styles
    def _build_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", rowheight=24, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("TButton", padding=(10, 5))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), foreground="#ffffff", background="#c8102e")
        style.configure("Stop.TButton", font=("Segoe UI", 10, "bold"), foreground="#ffffff", background="#8a1c1c")
        style.configure("Header.TLabel", font=("Segoe UI", 16, "bold"), foreground="#c8102e")
        style.configure("Sub.TLabel", foreground="#666666")

    def _build_header(self) -> None:
        header = ttk.Frame(self, padding=(16, 12, 16, 4))
        header.pack(fill="x")
        ttk.Label(header, text="GFH Telecom LLC — Audit Automation", style="Header.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="WhatsApp Web • Tesseract OCR • Timesheet & BRS portals",
            style="Sub.TLabel",
        ).pack(side="left", padx=(14, 0), pady=(6, 0))

        load_bar = ttk.LabelFrame(self, text="Manual workbook load (optional — portals automate this)", padding=10)
        load_bar.pack(fill="x", padx=16, pady=(8, 0))
        ttk.Button(load_bar, text="1. Inventory Count Results…", command=self._pick_inventory).grid(row=0, column=0, padx=4)
        ttk.Label(load_bar, textvariable=self.inventory_path_var, width=42, relief="sunken").grid(row=0, column=1, padx=4, sticky="ew")
        ttk.Button(load_bar, text="2. Employee Time Sheet…", command=self._pick_timesheet).grid(row=0, column=2, padx=4)
        ttk.Label(load_bar, textvariable=self.timesheet_path_var, width=42, relief="sunken").grid(row=0, column=3, padx=4, sticky="ew")
        ttk.Button(load_bar, text="Load Variances", style="Primary.TButton", command=self.load_variances).grid(row=0, column=4, padx=8)
        load_bar.columnconfigure(1, weight=1)
        load_bar.columnconfigure(3, weight=1)

    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=10)
        self._build_status_tab()
        self._build_variance_tab()
        self._build_schedule_tab()
        self._build_employees_tab()
        self._build_credentials_tab()
        self._build_run_tab()

    # ============================================================ STATUS TAB
    def _build_status_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="Inventory Audit Status")

        filters = ttk.Frame(tab)
        filters.pack(fill="x", pady=(0, 8))
        self.status_district_var = tk.StringVar(value="All Districts")
        self.status_store_var = tk.StringVar(value="All Stores")
        self.status_search_var = tk.StringVar()
        ttk.Label(filters, text="District:").pack(side="left")
        self.status_district_combo = ttk.Combobox(
            filters, textvariable=self.status_district_var, state="readonly", width=18,
            postcommand=self._populate_status_districts,
        )
        self.status_district_combo.pack(side="left", padx=(4, 10))
        ttk.Label(filters, text="Store:").pack(side="left")
        self.status_store_combo = ttk.Combobox(
            filters, textvariable=self.status_store_var, state="readonly", width=30,
            postcommand=self._populate_status_stores,
        )
        self.status_store_combo.pack(side="left", padx=(4, 10))
        ttk.Label(filters, text="Search:").pack(side="left")
        ttk.Entry(filters, textvariable=self.status_search_var, width=24).pack(side="left", padx=(4, 10))
        ttk.Button(filters, text="Refresh", command=self._refresh_status_table).pack(side="left")

        columns = [
            ("district", "District", 140, "w"),
            ("store", "Store", 300, "w"),
            ("status", "Status", 110, "center"),
            ("rep", "Latest Rep", 180, "w"),
            ("source", "Source", 220, "w"),
        ]
        frame, self.status_tree = make_treeview(tab, columns, height=16)
        frame.pack(fill="both", expand=True)
        for col, _h, _w, _a in columns:
            self.status_tree.heading(col, text=_h, command=lambda c=col: sort_treeview(self.status_tree, c, False))

        self.status_tree.tag_configure("completed", foreground="#1a7f37")
        self.status_tree.tag_configure("pending", foreground="#b42318")

    def _populate_status_districts(self) -> None:
        districts = ["All Districts"] + sorted({r["district"] for r in self.db.rows(include_cleared=True)} |
                                              {r.district for r in self.db.inventory_status_rows()})
        self.status_district_combo["values"] = districts

    def _populate_status_stores(self) -> None:
        stores = ["All Stores"]
        district = self.status_district_var.get()
        for row in self.db.inventory_status_rows():
            if district == "All Districts" or row.district == district:
                stores.append(row.store)
        self.status_store_combo["values"] = sorted(set(stores))

    def _refresh_status_table(self) -> None:
        self.status_tree.delete(*self.status_tree.get_children())
        district = self.status_district_var.get()
        store = self.status_store_var.get()
        search = self.status_search_var.get().lower().strip()
        for row in self.db.inventory_status_rows():
            if district != "All Districts" and row.district != district:
                continue
            if store != "All Stores" and row.store != store:
                continue
            haystack = f"{row.district} {row.store} {row.rep_name}".lower()
            if search and search not in haystack:
                continue
            tag = "completed" if row.status.lower() == "completed" else "pending"
            self.status_tree.insert("", "end", values=(
                row.district, row.store, row.status, row.rep_name, row.source_file), tags=(tag,))

    # ============================================================ VARIANCE TAB
    def _build_variance_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="Variance Audit")

        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 8))
        self.variance_district_var = tk.StringVar(value="All Districts")
        self.variance_include_cleared = tk.BooleanVar(value=True)
        ttk.Label(bar, text="District:").pack(side="left")
        self.variance_district_combo = ttk.Combobox(
            bar, textvariable=self.variance_district_var, state="readonly", width=18,
            postcommand=self._populate_variance_districts,
        )
        self.variance_district_combo.pack(side="left", padx=(4, 10))
        ttk.Checkbutton(bar, text="Include cleared", variable=self.variance_include_cleared,
                        command=self._refresh_variance_table).pack(side="left", padx=(0, 10))
        ttk.Button(bar, text="Refresh", command=self._refresh_variance_table).pack(side="left", padx=4)
        ttk.Button(bar, text="Mark Cleared (selected)", command=self._mark_selected_cleared).pack(side="left", padx=4)
        ttk.Button(bar, text="Un-Clear (selected)", command=self._mark_selected_uncleared).pack(side="left", padx=4)
        ttk.Button(bar, text="Export Audit Log (xlsx)", command=self._export_audit_log).pack(side="left", padx=4)

        columns = [
            ("district", "District", 120, "w"),
            ("store", "Store", 200, "w"),
            ("product", "Product", 260, "w"),
            ("imei", "IMEI", 150, "w"),
            ("status", "Status", 90, "center"),
            ("rep", "Rep", 130, "w"),
            ("cleared", "Cleared", 70, "center"),
            ("via", "Via", 60, "center"),
        ]
        frame, self.variance_tree = make_treeview(tab, columns, height=18)
        frame.pack(fill="both", expand=True)
        self.variance_tree.tag_configure("cleared", foreground="#1a7f37")
        self.variance_tree.tag_configure("open", foreground="#b42318")

    def _populate_variance_districts(self) -> None:
        districts = ["All Districts"] + sorted({r.district for r in self.db.rows(include_cleared=True)})
        self.variance_district_combo["values"] = districts

    def _refresh_variance_table(self) -> None:
        self.variance_tree.delete(*self.variance_tree.get_children())
        district = self.variance_district_var.get()
        include_cleared = self.variance_include_cleared.get()
        rows = self.db.rows(include_cleared=include_cleared, district="" if district == "All Districts" else district)
        for row in rows:
            tag = "cleared" if row.cleared else "open"
            self.variance_tree.insert("", "end", iid=row.key, values=(
                row.district, row.store, row.product, row.imei, row.status,
                row.rep_name, "Yes" if row.cleared else "No", row.cleared_via), tags=(tag,))

    def _selected_variance_rows(self) -> List[VarianceRow]:
        keys = self.variance_tree.selection()
        return self.db.get_rows_by_keys(keys)

    def _mark_selected_cleared(self) -> None:
        rows = self._selected_variance_rows()
        for row in rows:
            self.db.set_cleared(row.key, True, via="manual")
        self.db.log_event("", "manual_clear", f"{len(rows)} rows")
        self._refresh_variance_table()

    def _mark_selected_uncleared(self) -> None:
        rows = self._selected_variance_rows()
        for row in rows:
            self.db.set_cleared(row.key, False)
        self._refresh_variance_table()

    def _export_audit_log(self) -> None:
        path = EXPORT_DIR / f"inventory_audit_log_{now_text().replace(':', '').replace(' ', '_')}.xlsx"
        try:
            self.db.export_xlsx(path)
            messagebox.showinfo("Export complete", f"Audit log saved to:\n{path}")
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))

    # ============================================================ SCHEDULE TAB
    def _build_schedule_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="Districts & Schedule")
        ttk.Label(
            tab,
            text="Assign each district its WhatsApp group and Audit Start Time (HH:MM, 24h). "
                 "When you click Start, each district's workflow fires exactly at its scheduled time.",
            wraplength=1100, style="Sub.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        columns = [
            ("district", "District", 170, "w"),
            ("group", "WhatsApp Group", 380, "w"),
            ("start", "Audit Start Time", 130, "center"),
            ("enabled", "Enabled", 80, "center"),
            ("state", "Last Run State", 130, "center"),
        ]
        frame, self.schedule_tree = make_treeview(tab, columns, height=12)
        frame.pack(fill="both", expand=True)

        form = ttk.LabelFrame(tab, text="Add / update district", padding=8)
        form.pack(fill="x", pady=8)
        self.sched_district_var = tk.StringVar()
        self.sched_group_var = tk.StringVar()
        self.sched_start_var = tk.StringVar()
        self.sched_enabled_var = tk.BooleanVar(value=True)
        ttk.Label(form, text="District:").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.sched_district_var, width=22).grid(row=0, column=1, padx=6)
        ttk.Label(form, text="WhatsApp group:").grid(row=0, column=2, sticky="w")
        ttk.Entry(form, textvariable=self.sched_group_var, width=42).grid(row=0, column=3, padx=6)
        ttk.Label(form, text="Start (HH:MM):").grid(row=0, column=4, sticky="w")
        ttk.Entry(form, textvariable=self.sched_start_var, width=10).grid(row=0, column=5, padx=6)
        ttk.Checkbutton(form, text="Enabled", variable=self.sched_enabled_var).grid(row=0, column=6, padx=6)
        ttk.Button(form, text="Save", style="Primary.TButton", command=self._save_schedule_entry).grid(row=0, column=7, padx=6)
        ttk.Button(form, text="Delete", command=self._delete_schedule_entry).grid(row=0, column=8, padx=6)
        ttk.Button(form, text="Clear", command=self._clear_schedule_form).grid(row=0, column=9, padx=6)

        self.schedule_tree.bind("<<TreeviewSelect>>", self._on_schedule_select)

    def _load_schedule_into_tree(self) -> None:
        self.schedule_tree.delete(*self.schedule_tree.get_children())
        runs = {r.get("district"): r.get("state", "") for r in self.db.district_runs()}
        for district, entry in sorted(self.config.schedule.items()):
            self.schedule_tree.insert("", "end", values=(
                entry.district, entry.whatsapp_group, entry.start_time,
                "Yes" if entry.enabled else "No", runs.get(entry.district, "")))

    def _on_schedule_select(self, _event=None) -> None:
        selection = self.schedule_tree.selection()
        if not selection:
            return
        values = self.schedule_tree.item(selection[0], "values")
        if len(values) >= 4:
            self.sched_district_var.set(values[0])
            self.sched_group_var.set(values[1])
            self.sched_start_var.set(values[2])
            self.sched_enabled_var.set(values[3] == "Yes")

    def _save_schedule_entry(self) -> None:
        district = normalize_district(self.sched_district_var.get())
        start_time = self.sched_start_var.get().strip()
        if not district or district == "Unknown":
            messagebox.showwarning("District required", "Enter a district name.")
            return
        if start_time and parse_start_time(start_time) is None:
            messagebox.showwarning("Invalid time", "Start time must be HH:MM (24h), e.g. 09:30.")
            return
        group = self.sched_group_var.get().strip() or f"GFH TELECOM {district.upper()}"
        self.config.schedule[district] = DistrictScheduleEntry(
            district=district, whatsapp_group=group,
            start_time=start_time, enabled=self.sched_enabled_var.get(),
        )
        self.db.save_whatsapp_group(district, group)
        self.config_store.save(self.config)
        self._load_schedule_into_tree()

    def _delete_schedule_entry(self) -> None:
        district = self.sched_district_var.get().strip()
        if district in self.config.schedule:
            del self.config.schedule[district]
            self.config_store.save(self.config)
            self._load_schedule_into_tree()
            self._clear_schedule_form()

    def _clear_schedule_form(self) -> None:
        self.sched_district_var.set("")
        self.sched_group_var.set("")
        self.sched_start_var.set("")
        self.sched_enabled_var.set(True)

    # ============================================================ EMPLOYEES TAB
    def _build_employees_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="Employees")
        ttk.Label(
            tab,
            text="Map reps to their mobile numbers — used to build @mentions in WhatsApp "
                 "messages. Import from the Employee Time Sheet workbook or add manually.",
            wraplength=1100, style="Sub.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        columns = [("name", "Employee Name", 260, "w"),
                   ("phone", "Phone Number", 180, "w"),
                   ("district", "District", 180, "w")]
        frame, self.employees_tree = make_treeview(tab, columns, height=14)
        frame.pack(fill="both", expand=True)

        form = ttk.LabelFrame(tab, text="Add / update employee", padding=8)
        form.pack(fill="x", pady=8)
        self.emp_name_var = tk.StringVar()
        self.emp_phone_var = tk.StringVar()
        self.emp_district_var = tk.StringVar()
        ttk.Label(form, text="Name:").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.emp_name_var, width=26).grid(row=0, column=1, padx=6)
        ttk.Label(form, text="Phone:").grid(row=0, column=2, sticky="w")
        ttk.Entry(form, textvariable=self.emp_phone_var, width=20).grid(row=0, column=3, padx=6)
        ttk.Label(form, text="District:").grid(row=0, column=4, sticky="w")
        ttk.Entry(form, textvariable=self.emp_district_var, width=20).grid(row=0, column=5, padx=6)
        ttk.Button(form, text="Save", style="Primary.TButton", command=self._save_employee).grid(row=0, column=6, padx=6)
        ttk.Button(form, text="Delete", command=self._delete_employee).grid(row=0, column=7, padx=6)
        ttk.Button(form, text="Import from Time Sheet…", command=self._import_employees_from_timesheet).grid(row=0, column=8, padx=6)
        ttk.Button(form, text="Import xlsx…", command=self._import_employees_xlsx).grid(row=0, column=9, padx=6)
        ttk.Button(form, text="Export xlsx", command=self._export_employees_xlsx).grid(row=0, column=10, padx=6)
        self.employees_tree.bind("<<TreeviewSelect>>", self._on_employee_select)

    def _load_employees_into_tree(self) -> None:
        self.employees_tree.delete(*self.employees_tree.get_children())
        for emp in self.db.employees():
            self.employees_tree.insert("", "end", values=(emp["name"], emp["phone"], emp["district"]))

    def _on_employee_select(self, _event=None) -> None:
        selection = self.employees_tree.selection()
        if not selection:
            return
        values = self.employees_tree.item(selection[0], "values")
        if len(values) >= 3:
            self.emp_name_var.set(values[0])
            self.emp_phone_var.set(values[1])
            self.emp_district_var.set(values[2])

    def _save_employee(self) -> None:
        name = self.emp_name_var.get().strip()
        if not name:
            messagebox.showwarning("Name required", "Enter the employee name.")
            return
        self.db.save_employee(name, self.emp_phone_var.get().strip(), self.emp_district_var.get().strip())
        self._load_employees_into_tree()

    def _delete_employee(self) -> None:
        name = self.emp_name_var.get().strip()
        if name:
            self.db.delete_employee(name)
            self._load_employees_into_tree()

    def _import_employees_from_timesheet(self) -> None:
        path = filedialog.askopenfilename(title="Select Employee_Time_Sheet.xlsx",
                                          filetypes=[("Excel", "*.xlsx *.xlsm")])
        if not path:
            return
        try:
            records = read_xlsx_records(path)
            self._ingest_timesheet_for_employees(records)
            self._load_employees_into_tree()
            messagebox.showinfo("Import complete", "Employees imported from time sheet.")
        except Exception as exc:
            messagebox.showerror("Import failed", str(exc))

    def _ingest_timesheet_for_employees(self, records: List[dict]) -> int:
        from ..xlsx_reader import find_column

        if not records:
            return 0
        sample = records[0]
        name_col = find_column(sample, ["Employee Name", "Employee", "Salesperson", "Rep Name", "User Login"])
        phone_col = find_column(sample, ["Phone", "Phone Number", "Mobile", "Cell"])
        district_col = find_column(sample, ["District"])
        if not name_col:
            return 0
        count = 0
        for rec in records:
            name = (rec.get(name_col) or "").strip()
            if not name:
                continue
            phone = (rec.get(phone_col, "") or "").strip() if phone_col else ""
            district = (rec.get(district_col, "") or "").strip() if district_col else ""
            if phone:
                self.db.save_employee(name, phone, district)
                count += 1
        return count

    def _import_employees_xlsx(self) -> None:
        path = filedialog.askopenfilename(title="Select employees workbook",
                                          filetypes=[("Excel", "*.xlsx *.xlsm *.csv")])
        if not path:
            return
        try:
            if path.lower().endswith(".csv"):
                import csv

                with open(path, "r", newline="", encoding="utf-8-sig") as f:
                    records = [dict(r) for r in csv.DictReader(f)]
            else:
                records = read_xlsx_records(path)
            from ..xlsx_reader import find_column

            sample = records[0] if records else {}
            name_col = find_column(sample, ["Employee Name", "Name", "Employee"])
            phone_col = find_column(sample, ["Phone Number", "Phone", "Mobile"])
            district_col = find_column(sample, ["District"])
            if not name_col or not phone_col:
                messagebox.showwarning("Columns missing", "Need 'Employee Name' and 'Phone Number' columns.")
                return
            for rec in records:
                name = (rec.get(name_col) or "").strip()
                phone = (rec.get(phone_col) or "").strip()
                if name and phone:
                    self.db.save_employee(name, phone, (rec.get(district_col, "") or "").strip() if district_col else "")
            self._load_employees_into_tree()
        except Exception as exc:
            messagebox.showerror("Import failed", str(exc))

    def _export_employees_xlsx(self) -> None:
        from openpyxl import Workbook

        path = EXPORT_DIR / f"employees_{now_text().replace(':', '').replace(' ', '_')}.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "Employees"
        ws.append(["Employee Name", "Phone Number", "District"])
        for emp in self.db.employees():
            ws.append([emp["name"], emp["phone"], emp["district"]])
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        wb.save(path)
        messagebox.showinfo("Export complete", f"Employees exported to:\n{path}")

    # ============================================================ CREDENTIALS TAB
    def _build_credentials_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="Portal Credentials")

        ts_frame = ttk.LabelFrame(tab, text="Timesheet Portal — https://gfh-telecom-app.web.app/timesheet", padding=10)
        ts_frame.pack(fill="x", pady=(0, 10))
        self.ts_email_var = tk.StringVar(value=self.config.timesheet.email)
        self.ts_password_var = tk.StringVar(value=self.config.timesheet.password)
        ttk.Label(ts_frame, text="Email:").grid(row=0, column=0, sticky="w")
        ttk.Entry(ts_frame, textvariable=self.ts_email_var, width=36, show="").grid(row=0, column=1, padx=8, pady=3, sticky="ew")
        ttk.Label(ts_frame, text="Password:").grid(row=1, column=0, sticky="w")
        ttk.Entry(ts_frame, textvariable=self.ts_password_var, width=36, show="•").grid(row=1, column=1, padx=8, pady=3, sticky="ew")
        ttk.Button(ts_frame, text="Test login", command=self._test_timesheet_login).grid(row=0, column=2, rowspan=2, padx=8)
        ts_frame.columnconfigure(1, weight=1)

        brs_frame = ttk.LabelFrame(tab, text="BRS / Count Sheet Portal — wsreports.b2bsoft.com", padding=10)
        brs_frame.pack(fill="x", pady=(0, 10))
        self.brs_email_var = tk.StringVar(value=self.config.brs.email)
        self.brs_password_var = tk.StringVar(value=self.config.brs.password)
        ttk.Label(brs_frame, text="Email:").grid(row=0, column=0, sticky="w")
        ttk.Entry(brs_frame, textvariable=self.brs_email_var, width=36).grid(row=0, column=1, padx=8, pady=3, sticky="ew")
        ttk.Label(brs_frame, text="Password:").grid(row=1, column=0, sticky="w")
        ttk.Entry(brs_frame, textvariable=self.brs_password_var, width=36, show="•").grid(row=1, column=1, padx=8, pady=3, sticky="ew")
        ttk.Button(brs_frame, text="Test login", command=self._test_brs_login).grid(row=0, column=2, rowspan=2, padx=8)
        brs_frame.columnconfigure(1, weight=1)

        rem_frame = ttk.LabelFrame(tab, text="Reminders & engine", padding=10)
        rem_frame.pack(fill="x", pady=(0, 10))
        self.reminder_interval_var = tk.StringVar(value=str(self.config.reminders.reminder_interval_minutes))
        self.audit_timeout_var = tk.StringVar(value=str(self.config.reminders.audit_timeout_minutes))
        self.max_reminders_var = tk.StringVar(value=str(self.config.reminders.max_reminders))
        self.poll_interval_var = tk.StringVar(value=str(self.config.engine.poll_interval_seconds))
        self.browser_var = tk.StringVar(value=self.config.whatsapp_browser)
        self.tesseract_var = tk.StringVar(value=self.config.tesseract_path or find_tesseract() or "")
        self.ghostscript_var = tk.StringVar(value=self.config.ghostscript_path or find_ghostscript() or "")

        rows = [
            ("Reminder interval (minutes):", self.reminder_interval_var),
            ("Audit timeout (minutes):", self.audit_timeout_var),
            ("Max reminders:", self.max_reminders_var),
            ("WhatsApp poll interval (s):", self.poll_interval_var),
        ]
        for i, (label, var) in enumerate(rows):
            ttk.Label(rem_frame, text=label).grid(row=i, column=0, sticky="w")
            ttk.Entry(rem_frame, textvariable=var, width=8).grid(row=i, column=1, padx=8, pady=3, sticky="w")
        ttk.Label(rem_frame, text="WhatsApp browser:").grid(row=4, column=0, sticky="w")
        ttk.Combobox(rem_frame, textvariable=self.browser_var, values=["chrome", "edge"],
                     state="readonly", width=8).grid(row=4, column=1, padx=8, sticky="w")
        ttk.Label(rem_frame, text="Tesseract path (auto if empty):").grid(row=0, column=3, sticky="w")
        ttk.Entry(rem_frame, textvariable=self.tesseract_var, width=44).grid(row=0, column=4, padx=8, sticky="ew")
        ttk.Label(rem_frame, text="Ghostscript path (auto if empty):").grid(row=1, column=3, sticky="w")
        ttk.Entry(rem_frame, textvariable=self.ghostscript_var, width=44).grid(row=1, column=4, padx=8, sticky="ew")
        rem_frame.columnconfigure(4, weight=1)

        ttk.Button(tab, text="Save configuration", style="Primary.TButton", command=self._save_credentials).pack(anchor="w")

    def _collect_credentials(self) -> AppConfig:
        cfg = self.config
        cfg.timesheet = PortalCredentials(email=self.ts_email_var.get().strip(),
                                         password=self.ts_password_var.get())
        cfg.brs = PortalCredentials(email=self.brs_email_var.get().strip(),
                                    password=self.brs_password_var.get())
        try:
            cfg.reminders.reminder_interval_minutes = max(1, int(self.reminder_interval_var.get() or 5))
            cfg.reminders.audit_timeout_minutes = max(1, int(self.audit_timeout_var.get() or 15))
            cfg.reminders.max_reminders = max(1, int(self.max_reminders_var.get() or 3))
            cfg.engine.poll_interval_seconds = max(3, int(self.poll_interval_var.get() or 10))
        except ValueError:
            pass
        cfg.whatsapp_browser = self.browser_var.get()
        cfg.tesseract_path = self.tesseract_var.get().strip()
        cfg.ghostscript_path = self.ghostscript_var.get().strip()
        return cfg

    def _save_credentials(self) -> None:
        self.config = self._collect_credentials()
        self.config_store.save(self.config)
        messagebox.showinfo("Saved", "Configuration saved (passwords stored obfuscated, file excluded from git).")

    def _test_timesheet_login(self) -> None:
        self._test_portal(TimesheetPortalScraper, "Timesheet",
                          self.ts_email_var.get(), self.ts_password_var.get())

    def _test_brs_login(self) -> None:
        self._test_portal(BRSCountSheetScraper, "BRS",
                          self.brs_email_var.get(), self.brs_password_var.get())

    def _test_portal(self, scraper_class, label: str, email: str, password: str) -> None:
        if not email or not password:
            messagebox.showwarning("Missing credentials", f"Enter {label} email and password first.")
            return

        def work() -> None:
            from ..whatsapp.driver_manager import DriverManager
            from ..paths import WHATSAPP_PROFILE_DIR, DOWNLOAD_DIR

            dm = None
            try:
                dm = DriverManager(
                    profile_dir=WHATSAPP_PROFILE_DIR.parent / "portal_test_profile",
                    browser=self.browser_var.get(), headless=False,
                )
                if not dm.initialize():
                    raise RuntimeError("Browser init failed")
                scraper = scraper_class(dm, email, password, login_wait=15)
                scraper.login()
                self.log_console.log(f"✅ {label} login OK", "success")
                messagebox.showinfo("Login OK", f"{label} portal login successful.")
            except Exception as exc:
                self.log_console.log(f"❌ {label} login failed: {exc}", "error")
                messagebox.showerror("Login failed", str(exc))
            finally:
                if dm:
                    try:
                        dm.quit()
                    except Exception:
                        pass

        threading.Thread(target=work, daemon=True).start()

    # ============================================================ RUN TAB
    def _build_run_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="Run & Logs")

        controls = ttk.Frame(tab)
        controls.pack(fill="x", pady=(0, 10))
        self.start_button = ttk.Button(controls, text="START (scheduled)", style="Primary.TButton",
                                       command=self._start_scheduled)
        self.start_button.pack(side="left", padx=4)
        self.start_now_button = ttk.Button(controls, text="START NOW (all districts)", style="Primary.TButton",
                                           command=self._start_now)
        self.start_now_button.pack(side="left", padx=4)
        self.stop_button = ttk.Button(controls, text="STOP", style="Stop.TButton",
                                      command=self._stop_engine, state="disabled")
        self.stop_button.pack(side="left", padx=4)
        self.engine_status_var = tk.StringVar(value="Engine idle")
        ttk.Label(controls, textvariable=self.engine_status_var, style="Sub.TLabel").pack(side="left", padx=16)

        paned = ttk.PanedWindow(tab, orient="vertical")
        paned.pack(fill="both", expand=True)

        runs_frame = ttk.LabelFrame(paned, text="District run state", padding=6)
        columns = [
            ("district", "District", 170, "w"),
            ("state", "State", 130, "center"),
            ("cleared", "Cleared / Total", 130, "center"),
            ("reminders", "Reminders", 100, "center"),
            ("started", "Started", 160, "center"),
            ("completed", "Completed", 160, "center"),
            ("last", "Last Message", 220, "w"),
        ]
        frame, self.runs_tree = make_treeview(runs_frame, columns, height=7)
        frame.pack(fill="both", expand=True)
        paned.add(runs_frame, weight=1)

        logs_frame = ttk.LabelFrame(paned, text="Live log", padding=6)
        self.log_console = LogConsole(logs_frame)
        self.log_console.pack(fill="both", expand=True)
        paned.add(logs_frame, weight=2)

    def _refresh_district_runs(self) -> None:
        self.runs_tree.delete(*self.runs_tree.get_children())
        for run in self.db.district_runs():
            cleared = f"{run.get('cleared_variances', 0)}/{run.get('total_variances', 0)}"
            self.runs_tree.insert("", "end", values=(
                run.get("district", ""), run.get("state", ""), cleared,
                run.get("reminders_sent", 0), run.get("started_at", ""),
                run.get("completed_at", ""), run.get("last_message", "")))

    # -- manual workbook load ---------------------------------------------------
    def _pick_inventory(self) -> None:
        path = filedialog.askopenfilename(title="Inventory_Count_Result_Details.xlsx",
                                          filetypes=[("Excel", "*.xlsx *.xlsm")])
        if path:
            self.inventory_path_var.set(path)

    def _pick_timesheet(self) -> None:
        path = filedialog.askopenfilename(title="Employee_Time_Sheet.xlsx",
                                          filetypes=[("Excel", "*.xlsx *.xlsm")])
        if path:
            self.timesheet_path_var.set(path)

    def load_variances(self) -> None:
        inventory_path = self.inventory_path_var.get().strip()
        if not inventory_path:
            messagebox.showinfo("Select file", "Select the Inventory Count Results workbook first.")
            return
        try:
            inventory_records = read_xlsx_records(inventory_path)
            timesheet_records = []
            timesheet_path = self.timesheet_path_var.get().strip()
            if timesheet_path:
                timesheet_records = read_xlsx_records(timesheet_path)
                self._ingest_timesheet_for_employees(timesheet_records)
            master_records = self._read_store_master()
            variances, summary = extract_variances(
                inventory_records, timesheet_records, master_records,
                source_file=Path(inventory_path).name,
            )
            existing = {r.key: r for r in self.db.rows(include_cleared=True)}
            for row in variances:
                prior = existing.get(row.key)
                if prior and prior.cleared:
                    row.cleared = True
                    row.cleared_at = prior.cleared_at
                    row.cleared_via = prior.cleared_via
            self.db.upsert_rows(variances)
            status_rows, _ = build_inventory_status_rows(
                inventory_records, timesheet_records, master_records,
                source_file=Path(inventory_path).name,
            )
            self.db.upsert_inventory_status_rows(status_rows)
            self.loaded_keys = [r.key for r in variances]
            self.data_loaded = True
            self._refresh_variance_table()
            self._refresh_status_table()
            self._load_employees_into_tree()
            self.log_console.log(
                f"Loaded {len(variances)} variances "
                f"(stores: {summary.get('stores_total')}, completed: {summary.get('completed')}, "
                f"pending: {summary.get('pending')}, SIMs skipped: {summary.get('skipped_sims')})",
                "success",
            )
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))

    def _read_store_master(self) -> List[dict]:
        import csv

        if not STORE_CONFIG_PATH.exists():
            return []
        with STORE_CONFIG_PATH.open("r", newline="", encoding="utf-8-sig") as f:
            return [dict(r) for r in csv.DictReader(f)]

    # -- engine controls -----------------------------------------------------------
    def _ensure_engine(self):
        with self.engine_lock:
            if self.engine is None or not self.engine.is_running():
                from ..engine.audit_engine import AuditEngine

                self.config = self._collect_credentials()
                self.config_store.save(self.config)
                self.engine = AuditEngine(self.db, self.config, log_callback=self._engine_log)
        return self.engine

    def _engine_log(self, message: str) -> None:
        level = "info"
        lowered = message.lower()
        if "error" in lowered or "⚠" in message or "❌" in lowered:
            level = "error"
        elif "✅" in message or "completed" in lowered:
            level = "success"
        elif "warning" in lowered:
            level = "warn"
        self.log_console.log(message, level)
        self.after(0, self._refresh_district_runs)

    def _start_scheduled(self) -> None:
        self._start_engine(manual_start_all=False)

    def _start_now(self) -> None:
        self._start_engine(manual_start_all=True)

    def _start_engine(self, manual_start_all: bool) -> None:
        engine = self._ensure_engine()
        self.config = self._collect_credentials()
        if not self.config.schedule:
            messagebox.showwarning(
                "No districts configured",
                "Add districts and audit start times in the 'Districts & Schedule' tab first.",
            )
            return
        if manual_start_all and not messagebox.askyesno(
            "Start now", "Fire every enabled district's audit immediately?"
        ):
            return
        self.stop_button.configure(state="normal")
        self.start_button.configure(state="disabled")
        self.start_now_button.configure(state="disabled")
        self.engine_status_var.set("Engine running…")
        ok = engine.start(manual_start_all=manual_start_all)
        if not ok:
            self._reset_run_buttons()
            self.log_console.log("Engine is already running", "warn")

        # watch for engine completion to re-enable buttons
        def watcher() -> None:
            while engine.is_running():
                if not engine.stop_event.is_set() and engine.fsms:
                    self.after(0, self._refresh_district_runs)
                import time as _t

                _t.sleep(5)
            self.after(0, self._reset_run_buttons)
            self.after(0, lambda: self.engine_status_var.set("Engine idle"))
            self.after(0, self._refresh_district_runs)

        threading.Thread(target=watcher, daemon=True).start()

    def _stop_engine(self) -> None:
        if self.engine:
            self.engine.stop()
        self._reset_run_buttons()
        self.log_console.log("Stop requested by user", "warn")

    def _reset_run_buttons(self) -> None:
        self.stop_button.configure(state="disabled")
        self.start_button.configure(state="normal")
        self.start_now_button.configure(state="normal")


def run_gui() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = GFHAuditApp()
    app.mainloop()

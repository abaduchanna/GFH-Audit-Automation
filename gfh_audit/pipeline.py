"""Audit pipeline: build store maps, filter latest counts, extract variances.

Ported from the original GFH_Inventory_Audit.py v27 monolith with the same
behaviour, refactored into pure functions."""
from __future__ import annotations

import hashlib
from typing import Dict, List, Optional, Tuple

from .models import InventoryStatusRow, VarianceRow
from .textutils import (
    display_store,
    excel_serial_to_date_text,
    is_sim_product,
    normalize_district,
    normalize_header,
    normalize_store,
    numeric_excel_date,
    safe_text,
    variance_key,
)
from .xlsx_reader import find_column


def build_store_maps(
    time_sheet_records: List[Dict[str, str]],
) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
    """Build district, display-name, and rep-name lookups from timesheet rows."""
    district_by_store: Dict[str, str] = {}
    display_by_store: Dict[str, str] = {}
    rep_by_store: Dict[str, str] = {}
    latest_clock_by_store: Dict[str, float] = {}

    if not time_sheet_records:
        return district_by_store, display_by_store, rep_by_store

    sample = time_sheet_records[0]
    store_col = find_column(sample, ["Store"])
    district_col = find_column(sample, ["District"])
    rep_col = find_column(sample, ["Salesperson", "Sales Person", "Rep Name", "Employee", "Employee Name"])
    clock_in_col = find_column(sample, ["Clock In", "Clock-In", "Date", "Work Date"])
    user_login_col = find_column(sample, ["User Login", "Username", "Login"])

    if not store_col:
        return district_by_store, display_by_store, rep_by_store

    for index, rec in enumerate(time_sheet_records):
        store_raw = rec.get(store_col, "")
        norm = normalize_store(store_raw)
        if not norm:
            continue

        display_by_store[norm] = display_store(store_raw)
        if district_col:
            district = normalize_district(rec.get(district_col, ""))
            if district and district != "Unknown":
                district_by_store[norm] = district

        rep_name = safe_text(rec.get(rep_col, "")) if rep_col else ""
        if not rep_name and user_login_col:
            rep_name = safe_text(rec.get(user_login_col, ""))

        date_score = numeric_excel_date(rec.get(clock_in_col, "")) if clock_in_col else float(index)
        if rep_name and date_score >= latest_clock_by_store.get(norm, -1.0):
            rep_by_store[norm] = rep_name
            latest_clock_by_store[norm] = date_score

    return district_by_store, display_by_store, rep_by_store


def filter_latest_inventory_records(
    inventory_records: List[Dict[str, str]],
) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    """Keep only the latest count rows per Store + Created By."""
    metrics = {
        "raw_inventory_rows": len(inventory_records),
        "latest_inventory_rows": len(inventory_records),
        "stale_inventory_rows": 0,
        "latest_created_by_groups": 0,
    }
    if not inventory_records:
        return inventory_records, metrics

    sample = inventory_records[0]
    store_col = find_column(sample, ["Store"])
    created_by_col = find_column(sample, ["Created By", "Count By", "User Login"])
    created_date_col = find_column(sample, ["Created Date", "Created Date/Time", "Date Time", "Date"])

    if not store_col or not created_by_col or not created_date_col:
        return inventory_records, metrics

    latest_by_group: Dict[Tuple[str, str], float] = {}
    scores: List[Tuple[Tuple[str, str], float]] = []

    for index, rec in enumerate(inventory_records):
        store_key = normalize_store(rec.get(store_col, "")) or "__unknown_store__"
        created_by_key = safe_text(rec.get(created_by_col, "")).lower().strip() or "__unknown_created_by__"
        group_key = (store_key, created_by_key)
        score = numeric_excel_date(rec.get(created_date_col, ""))
        if score < 0:
            score = float(index) / 1000000.0
        scores.append((group_key, score))
        if group_key not in latest_by_group or score > latest_by_group[group_key]:
            latest_by_group[group_key] = score

    filtered: List[Dict[str, str]] = []
    for rec, (group_key, score) in zip(inventory_records, scores):
        latest_score = latest_by_group.get(group_key, score)
        if abs(score - latest_score) <= 0.0000001:
            filtered.append(rec)

    metrics["latest_inventory_rows"] = len(filtered)
    metrics["stale_inventory_rows"] = len(inventory_records) - len(filtered)
    metrics["latest_created_by_groups"] = len(latest_by_group)
    return filtered, metrics


def extract_variances(
    inventory_records: List[Dict[str, str]],
    time_sheet_records: List[Dict[str, str]],
    master_store_records: Optional[List[Dict[str, str]]] = None,
    source_file: str = "",
) -> Tuple[List[VarianceRow], Dict[str, int]]:
    """Extract non-Matched rows as variances (dedup by Store+IMEI)."""
    empty_metrics = {
        "completed": 0, "pending": 0, "stores_total": 0, "skipped_sims": 0,
        "raw_inventory_rows": 0, "latest_inventory_rows": 0,
        "stale_inventory_rows": 0, "latest_created_by_groups": 0,
    }
    if not inventory_records:
        return [], dict(empty_metrics)

    district_by_store, display_by_store, _rep_by_store = build_store_maps(time_sheet_records)
    for rec in master_store_records or []:
        district = normalize_district(rec.get("District", ""))
        store = display_store(rec.get("Store", ""))
        norm = normalize_store(store)
        if not norm:
            continue
        if district and district != "Unknown":
            district_by_store[norm] = district
        if store and norm not in display_by_store:
            display_by_store[norm] = store

    sample = inventory_records[0]
    store_col = find_column(sample, ["Store"])
    product_col = find_column(sample, ["Product Description", "Product"])
    imei_col = find_column(sample, ["Serial #", "Serial", "IMEI", "ESN"])
    status_col = find_column(sample, ["Status"])
    created_by_col = find_column(sample, ["Created By", "Count By", "User Login"])
    created_date_col = find_column(sample, ["Created Date", "Date"])
    document_status_col = find_column(sample, ["Document Status"])

    missing = [
        name for name, col in [
            ("Store", store_col),
            ("Product Description", product_col),
            ("Serial # / IMEI", imei_col),
            ("Status", status_col),
        ] if not col
    ]
    if missing:
        raise RuntimeError("Missing required inventory column(s): " + ", ".join(missing))

    inventory_records, latest_metrics = filter_latest_inventory_records(inventory_records)

    # Deduplicate by (Store, IMEI) keeping the most recent count.
    dedup_map: Dict[Tuple[str, str], Tuple[Dict[str, str], float]] = {}
    for rec in inventory_records:
        store_raw = rec.get(store_col, "")
        imei = safe_text(rec.get(imei_col, ""))
        if not imei:
            continue
        norm_store = normalize_store(store_raw)
        key = (norm_store, imei.lower())
        date_score = numeric_excel_date(rec.get(created_date_col, "")) if created_date_col else 0
        if key not in dedup_map or date_score > dedup_map[key][1]:
            dedup_map[key] = (rec, date_score)
    inventory_records = [rec for rec, _ in dedup_map.values()]
    latest_metrics["dedup_inventory_rows"] = len(inventory_records)

    completed_store_norms = set()
    for rec in inventory_records:
        norm = normalize_store(rec.get(store_col, ""))
        if norm:
            completed_store_norms.add(norm)

    all_store_norms = set(display_by_store.keys()) | completed_store_norms
    pending_store_norms = all_store_norms - completed_store_norms

    variance_rows: List[VarianceRow] = []
    skipped_sims = 0

    for rec in inventory_records:
        status = safe_text(rec.get(status_col, ""))
        if not status:
            continue
        if normalize_header(status) in {"matched", "match", "ok", "balanced"}:
            continue

        store = display_store(rec.get(store_col, ""))
        product = safe_text(rec.get(product_col, ""))
        imei = safe_text(rec.get(imei_col, ""))

        if not imei:
            continue
        if product and normalize_header(product) in {"accessorycommission", "accessoriescommission"}:
            continue
        if is_sim_product(product):
            skipped_sims += 1
            continue

        norm_store = normalize_store(store)
        district = normalize_district(district_by_store.get(norm_store, "Unknown"))
        created_by = safe_text(rec.get(created_by_col, "")) if created_by_col else ""
        rep_name = created_by
        created_date = excel_serial_to_date_text(rec.get(created_date_col, "")) if created_date_col else ""
        document_status = safe_text(rec.get(document_status_col, "")) if document_status_col else ""
        key = variance_key(store, imei, product, status, created_by, created_date)

        variance_rows.append(
            VarianceRow(
                key=key,
                district=district,
                store=store,
                product=product,
                imei=imei,
                status=status,
                created_by=created_by,
                rep_name=rep_name,
                created_date=created_date,
                document_status=document_status,
                source_file=source_file,
            )
        )

    summary = {
        "completed": len(completed_store_norms),
        "pending": len(pending_store_norms),
        "stores_total": len(all_store_norms),
        "skipped_sims": skipped_sims,
        **latest_metrics,
    }
    return variance_rows, summary


def build_inventory_status_rows(
    inventory_records: List[Dict[str, str]],
    time_sheet_records: List[Dict[str, str]],
    master_store_records: Optional[List[Dict[str, str]]] = None,
    source_file: str = "",
) -> Tuple[List[InventoryStatusRow], Dict[str, int]]:
    """Per-store Completed/Pending rows for the Inventory Audit Status tab."""
    district_by_store, display_by_store, rep_by_store = build_store_maps(time_sheet_records)
    master_display_by_store: Dict[str, str] = dict(display_by_store)
    inv_display_by_store: Dict[str, str] = {}
    completed_store_norms: set = set()

    filtered_records = inventory_records
    latest_metrics = {
        "raw_inventory_rows": len(inventory_records),
        "latest_inventory_rows": len(inventory_records),
        "stale_inventory_rows": 0,
        "latest_created_by_groups": 0,
    }

    if inventory_records:
        sample = inventory_records[0]
        store_col = find_column(sample, ["Store"])
        if store_col:
            filtered_records, latest_metrics = filter_latest_inventory_records(inventory_records)
            for rec in filtered_records:
                store_raw = rec.get(store_col, "")
                norm = normalize_store(store_raw)
                if not norm:
                    continue
                inv_display_by_store[norm] = display_store(store_raw)
                completed_store_norms.add(norm)

    # Master store list (from DB store master) contributes districts for unknown stores.
    for rec in master_store_records or []:
        district = normalize_district(rec.get("District", ""))
        store = display_store(rec.get("Store", ""))
        norm = normalize_store(store)
        if norm and district and district != "Unknown":
            district_by_store.setdefault(norm, district)
        if norm and store and norm not in master_display_by_store:
            master_display_by_store[norm] = store

    all_store_norms = sorted(
        set(master_display_by_store.keys())
        | set(display_by_store.keys())
        | set(inv_display_by_store.keys())
        | completed_store_norms
    )
    rows: List[InventoryStatusRow] = []
    for norm in all_store_norms:
        store = (
            master_display_by_store.get(norm)
            or display_by_store.get(norm)
            or inv_display_by_store.get(norm)
            or norm.title()
        )
        district = normalize_district(district_by_store.get(norm, "Unknown"))
        rep_name = safe_text(rep_by_store.get(norm, ""))
        status = "Completed" if norm in completed_store_norms else "Pending"
        key_raw = f"{norm}|{district}|{status}|status"
        key = hashlib.sha1(key_raw.encode("utf-8", errors="ignore")).hexdigest()
        rows.append(
            InventoryStatusRow(
                key=key, district=district, store=store,
                status=status, rep_name=rep_name, source_file=source_file,
            )
        )

    summary = {
        "completed": len(completed_store_norms),
        "pending": len([r for r in rows if r.status == "Pending"]),
        "stores_total": len(rows),
        **latest_metrics,
    }
    return rows, summary

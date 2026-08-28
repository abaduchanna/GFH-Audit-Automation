import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gfh_audit.pipeline import (
    build_store_maps,
    extract_variances,
    filter_latest_inventory_records,
)
from gfh_audit.xlsx_reader import RobustXlsxReader, find_column, read_xlsx_records


def _make_xlsx(path, headers, rows, sheet_name="Sheet1"):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(headers)
    for row in rows:
        ws.append(row)
    wb.save(path)


class TestXlsxReader(unittest.TestCase):
    def test_roundtrip_basic(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "count.xlsx"
            _make_xlsx(path, ["Store", "Serial #", "Status"], [["Store A", "111", "Deficit"]])
            records = read_xlsx_records(path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["Store"], "Store A")
            self.assertEqual(records[0]["Serial #"], "111")

    def test_corrupt_zip_fallback(self):
        """RobustXlsxReader reads local headers even without a central directory."""
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "count.xlsx"
            _make_xlsx(path, ["Store", "Status"], [["Store A", "Deficit"]])
            import zipfile

            data = path.read_bytes()
            # truncate the End Of Central Directory record
            eocd_index = data.rfind(b"PK\x05\x06")
            if eocd_index != -1:
                path.write_bytes(data[:eocd_index])
            reader = RobustXlsxReader(path)
            records = reader.read_sheet()
            self.assertGreaterEqual(len(records), 1)
            self.assertEqual(records[0]["Store"], "Store A")

    def test_find_column_fuzzy(self):
        record = {"Created Date/Time": "x", "Store ": "y"}
        self.assertEqual(find_column(record, ["Created Date"]), "Created Date/Time")
        self.assertEqual(find_column(record, ["Store"]), "Store ")
        self.assertIsNone(find_column(record, ["Missing"]))


class TestPipeline(unittest.TestCase):
    def test_build_store_maps(self):
        ts = [
            {"Store": "Store A", "District": "Houston", "Employee Name": "Abad", "Date": "45295"},
            {"Store": "Store B", "District": "Arizona - D1", "Employee Name": "Zed", "Date": "45296"},
        ]
        district_by_store, display_by_store, rep_by_store = build_store_maps(ts)
        self.assertEqual(district_by_store["store a"], "Houston")
        self.assertEqual(district_by_store["store b"], "Arizona")
        self.assertEqual(rep_by_store["store b"], "Zed")

    def test_filter_latest_inventory_records(self):
        records = [
            {"Store": "A", "Created By": "rep1", "Created Date": "45295"},
            {"Store": "A", "Created By": "rep1", "Created Date": "45296"},  # newer
            {"Store": "A", "Created By": "rep2", "Created Date": "45295"},
        ]
        filtered, metrics = filter_latest_inventory_records(records)
        self.assertEqual(len(filtered), 2)
        self.assertEqual(metrics["stale_inventory_rows"], 1)

    def test_extract_variances(self):
        inventory = [
            {"Store": "A", "Product Description": "iPhone 15", "Serial #": "111", "Status": "Matched", "Created By": "rep1", "Created Date": "45295"},
            {"Store": "A", "Product Description": "iPhone 15", "Serial #": "222", "Status": "Deficit", "Created By": "rep1", "Created Date": "45295"},
            {"Store": "B", "Product Description": "SIM Card", "Serial #": "333", "Status": "Deficit", "Created By": "rep2", "Created Date": "45295"},
            {"Store": "B", "Product Description": "Galaxy S24", "Serial #": "", "Status": "Deficit", "Created By": "rep2", "Created Date": "45295"},
        ]
        timesheet = [{"Store": "A", "District": "Houston"}, {"Store": "B", "District": "Arizona - D2"}]
        rows, summary = extract_variances(inventory, timesheet, source_file="test.xlsx")
        # matched row skipped, SIM skipped, blank IMEI skipped
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].imei, "222")
        self.assertEqual(rows[0].district, "Houston")
        self.assertEqual(rows[0].rep_name, "rep1")
        self.assertEqual(summary["skipped_sims"], 1)
        self.assertEqual(summary["stores_total"], 2)
        self.assertEqual(summary["completed"], 2)

    def test_extract_variances_requires_columns(self):
        with self.assertRaises(RuntimeError):
            extract_variances([{"Wrong": "1"}], [], None)


if __name__ == "__main__":
    unittest.main()

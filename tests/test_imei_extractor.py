import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gfh_audit.ocr.engine import OcrEngine, match_imei_against_variances


class FakeRow:
    def __init__(self, imei):
        self.imei = imei


class TestImeiExtraction(unittest.TestCase):
    def test_plain_15_digits(self):
        text = "Some device info\n356938035194802\nother text"
        self.assertEqual(OcrEngine.extract_imeis(text), ["356938035194802"])

    def test_spaced_groups_rescue(self):
        text = "IMEI: 356 938 035 194 802 (label)"
        self.assertEqual(OcrEngine.extract_imeis(text), ["356938035194802"])

    def test_multiple_and_dedupe(self):
        text = "111111111111111 356938035194802 111111111111111"
        self.assertEqual(OcrEngine.extract_imeis(text), ["111111111111111", "356938035194802"])

    def test_rejects_14_and_16(self):
        text = "1234567890123456 12345678901234"
        self.assertEqual(OcrEngine.extract_imeis(text), [])

    def test_luhn(self):
        self.assertTrue(OcrEngine.luhn_valid("356938035194803"))
        self.assertFalse(OcrEngine.luhn_valid("356938035194802"))
        self.assertFalse(OcrEngine.luhn_valid("1234"))

    def test_match_exact(self):
        rows = [FakeRow("356938035194802")]
        self.assertEqual(len(match_imei_against_variances("356938035194802", rows)), 1)

    def test_match_partial_ocr_read(self):
        """OCR misread leading digits — trailing 12+ digits still match."""
        rows = [FakeRow("356938035194802")]
        self.assertEqual(len(match_imei_against_variances("856938035194802", rows)), 1)

    def test_no_match_too_short(self):
        rows = [FakeRow("356938035194802")]
        self.assertEqual(match_imei_against_variances("12345", rows), [])

    def test_no_false_positive(self):
        rows = [FakeRow("356938035194802")]
        self.assertEqual(match_imei_against_variances("990000111122223", rows), [])


if __name__ == "__main__":
    unittest.main()

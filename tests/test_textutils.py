import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gfh_audit.textutils import (
    combine_digits,
    excel_serial_to_date_text,
    is_sim_product,
    mention_line,
    normalize_district,
    normalize_phone,
    parse_start_time,
    person_name_key,
    safe_text,
    variance_key,
    whatsapp_mention,
)


class TestTextUtils(unittest.TestCase):
    def test_safe_text_trims_float_suffix(self):
        self.assertEqual(safe_text("12345.0"), "12345")
        self.assertEqual(safe_text("abc.0"), "abc.0")
        self.assertEqual(safe_text(None), "")

    def test_normalize_phone(self):
        self.assertEqual(normalize_phone("(281) 555-1234"), "2815551234")
        self.assertEqual(normalize_phone("00 1 281 555 1234"), "+12815551234")
        self.assertEqual(normalize_phone(""), "")

    def test_whatsapp_mention(self):
        self.assertEqual(whatsapp_mention("281-555-1234"), "@2815551234")
        self.assertEqual(whatsapp_mention(""), "")
        self.assertEqual(whatsapp_mention("@2815551234"), "@2815551234")

    def test_mention_line(self):
        line = mention_line(["2815551234", "(713) 555-9999", "2815551234"], "please share.")
        self.assertEqual(line, "@2815551234 @7135559999 please share.")
        self.assertEqual(mention_line([], "x"), "")

    def test_normalize_district(self):
        self.assertEqual(normalize_district("arizona - d1"), "Arizona")
        self.assertEqual(normalize_district("Arizona - D2"), "Arizona")
        self.assertEqual(normalize_district("houston"), "Houston")
        self.assertEqual(normalize_district("Colorado West"), "Colorado West")
        self.assertEqual(normalize_district(""), "Unknown")
        self.assertEqual(normalize_district("  Texas   Gulf  "), "Texas Gulf")

    def test_person_name_key_order_independent(self):
        self.assertEqual(person_name_key("Channa, Abad"), person_name_key("Abad Channa"))

    def test_is_sim_product(self):
        self.assertTrue(is_sim_product("AT&T SIM Card"))
        self.assertTrue(is_sim_product("eSIM Starter Kit"))
        self.assertFalse(is_sim_product("iPhone 15 Pro"))
        self.assertFalse(is_sim_product(""))

    def test_excel_serial_date(self):
        # Excel serial 45295 == 2024-01-04 (base 1899-12-30)
        self.assertEqual(excel_serial_to_date_text("45295"), "01/04/2024 12:00 AM")
        self.assertEqual(excel_serial_to_date_text("not a date"), "not a date")
        self.assertEqual(excel_serial_to_date_text(""), "")

    def test_variance_key_is_stable(self):
        a = variance_key("Store A", "123456789012345", "iPhone", "Deficit", "rep", "01/02/2024")
        b = variance_key("store  a", "123456789012345", "iphone", "deficit", "REP", "01/02/2024")
        self.assertEqual(a, b)

    def test_parse_start_time(self):
        from datetime import time as dt_time

        self.assertEqual(parse_start_time("09:30"), dt_time(9, 30))
        self.assertEqual(parse_start_time("14:05:00"), dt_time(14, 5))
        self.assertEqual(parse_start_time("2:30 PM"), dt_time(14, 30))
        self.assertIsNone(parse_start_time(""))
        self.assertIsNone(parse_start_time("banana"))

    def test_combine_digits(self):
        self.assertEqual(combine_digits("IMEI: 35-6938-035194 802"), "356938035194802")


if __name__ == "__main__":
    unittest.main()

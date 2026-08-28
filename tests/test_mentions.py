import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gfh_audit.whatsapp.mentions import MentionResolver
from gfh_audit.whatsapp.whatsapp_web import WhatsAppWeb


class TestMentionResolver(unittest.TestCase):
    def test_exact_lookup(self):
        resolver = MentionResolver({"abad channa": "2815551234", "zed": "7135559999"})
        tags, missing = resolver.mentions_for_rows(["Abad Channa", "zed", "Abad Channa"])
        self.assertEqual(tags, ["@2815551234", "@7135559999"])
        self.assertEqual(missing, [])

    def test_missing_rep(self):
        resolver = MentionResolver({})
        tags, missing = resolver.mentions_for_rows(["Nobody"])
        self.assertEqual(tags, [])
        self.assertEqual(missing, ["Nobody"])

    def test_fuzzy_token_match(self):
        resolver = MentionResolver({"abad channa": "2815551234"})
        tags, missing = resolver.mentions_for_rows(["Channa Abad"])  # order swapped
        self.assertEqual(tags, ["@2815551234"])
        self.assertEqual(missing, [])

    def test_tag_line(self):
        resolver = MentionResolver({"a": "111", "b": "222"})
        tags, _ = resolver.mentions_for_rows(["a", "b"])
        self.assertEqual(resolver.tag_line(tags, "please share images."), "@111 @222 please share images.")


class TestMentionSplitting(unittest.TestCase):
    def test_split_mentions(self):
        message = "@2815551234 @7135559999 please share the images."
        segments = WhatsAppWeb._split_mentions(message)
        kinds = [k for k, _v in segments]
        self.assertEqual(kinds.count("mention"), 2)
        self.assertIn("please share the images.", [v for k, v in segments if k == "text"])
        values = [v for k, v in segments if k == "mention"]
        self.assertEqual(values, ["2815551234", "7135559999"])

    def test_no_mentions(self):
        segments = WhatsAppWeb._split_mentions("plain message with no tags")
        self.assertEqual(segments, [("text", "plain message with no tags")])


if __name__ == "__main__":
    unittest.main()

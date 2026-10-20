import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import phone_links as pl

BASE = "https://example.com/call.html"
pl._load_config = lambda: {}  # tests never read the real config/phone_links.json


class FindPhones(unittest.TestCase):
    def found(self, text):
        return [m[2] for m in pl.find_phones(text)]

    def test_star_code(self):
        self.assertEqual(self.found("להתקשר למוביט - *5090"), ["*5090"])

    def test_mobile_formats(self):
        for n in ["<phone>", "<phone>", "<phone>", "<phone>"]:
            self.assertEqual(self.found(f"תתקשר ל {n} מחר"), [n], n)

    def test_landline_and_intl(self):
        self.assertEqual(self.found("<phone>"), ["<phone>"])
        self.assertEqual(self.found("<phone>"), ["<phone>"])
        self.assertEqual(self.found("<phone>"), ["<phone>"])
        self.assertEqual(self.found("1-800-123-456"), ["1-800-123-456"])

    def test_no_false_positives(self):
        for t in ["פגישה ב-16:00", "20/10/2026", "לשלם 1500 ש״ח", "חדר 12345", "גרסה 2.0", "id 123456789"]:
            self.assertEqual(self.found(t), [], t)


class Linkify(unittest.TestCase):
    def test_bot_param_for_return_to_chat(self):
        self.assertEqual(pl.dial_url("*5090", BASE, bot="My_bot"), BASE + "?n=%2A5090&b=My_bot")
        self.assertEqual(pl.dial_url("*5090", BASE), BASE + "?n=%2A5090")

    def test_mini_app_link(self):
        self.assertEqual(pl.dial_url("*5090", BASE, bot="My_bot", mini_app=True),
                         "https://t.me/My_bot?startapp=s5090")
        self.assertEqual(pl.mini_app_code("<phone>"), "p972501234567")
        self.assertEqual(pl.mini_app_code("*2700#"), "s2700h")
        # no bot configured: falls back to the plain page
        self.assertEqual(pl.dial_url("*5090", BASE, bot="", mini_app=True), BASE + "?n=%2A5090")

    def test_no_base_or_no_phone_is_none(self):
        self.assertIsNone(pl.linkify_html("*5090", base=""))
        self.assertIsNone(pl.linkify_html("לקנות חלב", base=BASE))

    def test_star_link_keeps_text(self):
        out = pl.linkify_html("⏰ להתקשר למוביט - *5090", base=BASE)
        self.assertEqual(out, '⏰ להתקשר למוביט - <a href="https://example.com/call.html?n=%2A5090">*5090</a>')

    def test_escapes_html(self):
        out = pl.linkify_html("A&B <x> <phone>", base=BASE)
        self.assertTrue(out.startswith("A&amp;B &lt;x&gt; <a href="))
        self.assertIn("n=<phone>", out)

    def test_plus_kept(self):
        self.assertIn("n=<phone>", pl.linkify_html("<phone>", base=BASE))

    def test_payload(self):
        orig = {"chat_id": "1", "text": "לקנות חלב"}
        self.assertIs(pl.telegram_payload(orig), orig)
        pl._load_base_orig = pl._load_base
        pl._load_base = lambda: BASE
        try:
            p = pl.telegram_payload({"chat_id": "1", "text": "*5090"})
            self.assertEqual(p["parse_mode"], "HTML")
            self.assertIn("<a href=", p["text"])
            self.assertEqual(pl.telegram_payload({"chat_id": "1", "text": "*5090"}, linkify=False)["text"], "*5090")
        finally:
            pl._load_base = pl._load_base_orig


if __name__ == "__main__":
    unittest.main()

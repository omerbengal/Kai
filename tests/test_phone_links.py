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
        cfg_orig = pl._load_config
        self.assertEqual(pl.telegram_payload({"chat_id": "1", "text": "*5090"})["text"], "*5090")  # mini_app off
        pl._load_config = lambda: {"mini_app": True}
        try:
            p = pl.telegram_payload({"chat_id": "1", "text": "*5090"})
            self.assertEqual(p["parse_mode"], "HTML")
            self.assertIn("<a href=", p["text"])
            self.assertEqual(pl.telegram_payload({"chat_id": "1", "text": "*5090"}, linkify=False)["text"], "*5090")
        finally:
            pl._load_base = pl._load_base_orig
            pl._load_config = cfg_orig


class ContactCards(unittest.TestCase):
    def setUp(self):
        self.cfg = {}
        self._orig = pl._load_config
        pl._load_config = lambda: self.cfg

    def tearDown(self):
        pl._load_config = self._orig

    def test_intl_format_is_the_autolinked_one(self):
        # <phone> is one of the two shapes Telegram's server links (tested 20/10)
        self.assertEqual(pl.to_intl("<phone>"), "<phone>")
        self.assertEqual(pl.to_intl("<phone>"), "<phone>")
        self.assertIsNone(pl.to_intl("<phone>"))
        self.assertIsNone(pl.to_intl("*5090"))
        self.assertEqual(pl.intl_text("לדני <phone> מחר"), "לדני <phone> מחר")

    def test_card_numbers_only_unlinkable(self):
        t = "דני <phone>, עירייה <phone>, מוביט *5090, שוב *5090, <phone>"
        self.assertEqual(pl.card_numbers(t), ["<phone>", "*5090"])

    def test_reminder_alert_becomes_card(self):
        plan = pl.plan("⏰ להתקשר למוביט - *5090", alert=True)
        self.assertEqual(plan["mode"], "card")
        self.assertEqual(plan["cards"], [{"number": "*5090", "first": "⏰ להתקשר למוביט", "last": None}])

    def test_below_mode_and_lists(self):
        self.cfg = {"contact_mode": "below"}
        plan = pl.plan("⏰ להתקשר למוביט - *5090", alert=True)
        self.assertEqual(plan["mode"], "text")
        self.assertEqual(plan["cards"][0]["first"], "להתקשר למוביט")
        self.cfg = {}
        lst = pl.plan("‏📋 תזכורות:\n‏• 20/10 13:00 - להתקשר למוביט - *5090\n‏• לקנות חלב")
        self.assertEqual(lst["mode"], "text")  # lists never become a card
        self.assertEqual(len(lst["cards"]), 1)
        self.assertNotIn("*5090", lst["cards"][0]["first"])

    def test_two_codes_in_alert_go_below(self):
        plan = pl.plan("⏰ להתקשר לעירייה <phone> ולמוביט *5090", alert=True)
        self.assertEqual(plan["mode"], "text")
        self.assertEqual([c["number"] for c in plan["cards"]], ["<phone>", "*5090"])
        for c in plan["cards"]:
            self.assertNotIn("*5090", c["first"])
            self.assertNotIn("<phone>", c["first"])

    def test_mobile_only_alert_is_text(self):
        plan = pl.plan("⏰ להתקשר לדני <phone>", alert=True)
        self.assertEqual(plan, {"mode": "text", "text": "⏰ להתקשר לדני <phone>", "cards": []})

    def test_long_text_split_and_shortened(self):
        long = "⏰ " + "מילה " * 80 + "*5090"
        first, last = pl.card_names(long, "*5090")
        self.assertLessEqual(len(first), pl.CARD_FIRST_MAX)
        self.assertLessEqual(len(first) + 1 + len(last), pl.CARD_NAME_MAX + 1)
        self.assertTrue(last.endswith("…"))
        self.assertTrue(first.startswith("⏰ "))

    def test_mini_app_on_is_legacy(self):
        self.cfg = {"mini_app": True}
        self.assertEqual(pl.plan("*5090", alert=True)["mode"], "legacy")

    def test_contact_payload(self):
        p = pl.contact_payload("1", "*5090", "⏰ מוביט", None, reply_to=7, silent=True)
        self.assertEqual(p["phone_number"], "*5090")
        self.assertNotIn("last_name", p)
        self.assertEqual(p["reply_parameters"]["message_id"], 7)
        self.assertTrue(p["disable_notification"])


if __name__ == "__main__":
    unittest.main()

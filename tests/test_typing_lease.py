"""Typing lease: typing must stop BEFORE the final reply and never come back
from trailing tool calls (fix 2026-09-23, msg 14616)."""
import os, sys, time, json, threading, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import typing_lease as tl


class TypingLeaseTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._saved = (tl.LEASE_FILE, tl.INFLIGHT_FILE, tl.RESUME_GRACE, tl._ensure_loop,
                       tl.LAST_SEND_FILE, tl.TYPING_VISIBLE)
        tl.LAST_SEND_FILE = os.path.join(self.dir, "last_send.txt")
        tl.LEASE_FILE = os.path.join(self.dir, "lease.json")
        tl.INFLIGHT_FILE = os.path.join(self.dir, "inflight.txt")
        tl._ensure_loop = lambda lease: dict(lease, pid=1234)   # never spawn a real loop

    def tearDown(self):
        (tl.LEASE_FILE, tl.INFLIGHT_FILE, tl.RESUME_GRACE, tl._ensure_loop,
         tl.LAST_SEND_FILE, tl.TYPING_VISIBLE) = self._saved

    def lease(self):
        with open(tl.LEASE_FILE, encoding="utf-8") as f:
            return json.load(f)

    def test_trailing_call_after_reply_does_not_revive(self):
        tl.open_lease("1")
        tl.pause()                       # PreToolUse(reply)
        tl.renew()                       # ACK delete / action_log / CronCreate after reply
        self.assertEqual(self.lease()["deadline"], 0)

    def test_continued_work_after_grace_resumes(self):
        tl.RESUME_GRACE = 0.2
        tl.open_lease("1")
        tl.pause()                       # interim reply
        time.sleep(0.3)
        tl.renew()                       # real work continues
        self.assertGreater(self.lease()["deadline"], time.time())
        self.assertEqual(self.lease()["paused_at"], 0)

    def test_new_message_clears_pause(self):
        tl.open_lease("1")
        tl.pause()
        tl.open_lease("1")
        tl.renew()
        self.assertGreater(self.lease()["deadline"], time.time())

    def test_renew_never_opens(self):
        tl.renew()
        self.assertFalse(os.path.exists(tl.LEASE_FILE))

    def test_pause_waits_for_inflight_send(self):
        tl.open_lease("1")
        open(tl.INFLIGHT_FILE, "w").close()          # loop is mid-sendChatAction
        threading.Timer(0.4, os.remove, [tl.INFLIGHT_FILE]).start()
        t0 = time.time()
        tl.pause()
        self.assertGreaterEqual(time.time() - t0, 0.35)   # reply waited for the send to land
        self.assertFalse(os.path.exists(tl.INFLIGHT_FILE))

    def test_pause_fast_when_idle(self):
        tl.open_lease("1")
        t0 = time.time()
        tl.pause()
        self.assertLess(time.time() - t0, 0.3)

    # --- react (msg 14623): a reaction does not clear client-side typing ---
    def _mark_send(self, ago):
        with open(tl.LAST_SEND_FILE, "w") as f:
            f.write(repr(time.time() - ago))

    def test_react_waits_until_typing_expired(self):
        tl.TYPING_VISIBLE = 0.6
        tl.open_lease("1")
        self._mark_send(0.1)                         # loop sent typing 0.1s ago
        t0 = time.time()
        tl.pause(wait_expire=True)
        self.assertGreaterEqual(time.time() - t0, 0.45)   # waited ~0.5s for expiry
        self.assertEqual(self.lease()["deadline"], 0)

    def test_react_no_wait_when_last_send_old(self):
        tl.TYPING_VISIBLE = 0.6
        tl.open_lease("1")
        self._mark_send(5)
        t0 = time.time()
        tl.pause(wait_expire=True)
        self.assertLess(time.time() - t0, 0.3)

    def test_react_no_wait_without_lease(self):
        tl.TYPING_VISIBLE = 0.6
        self._mark_send(0)
        t0 = time.time()
        tl.pause(wait_expire=True)
        self.assertLess(time.time() - t0, 0.3)

    def test_reply_stays_fast_despite_recent_send(self):
        tl.TYPING_VISIBLE = 0.6
        tl.open_lease("1")
        self._mark_send(0)                           # typing just sent
        t0 = time.time()
        tl.pause()                                   # reply: message clears typing itself
        self.assertLess(time.time() - t0, 0.3)

    def test_react_wait_is_capped(self):
        tl.TYPING_VISIBLE = 0.4
        tl.open_lease("1")
        self._mark_send(-100)                        # clock skew / future timestamp
        t0 = time.time()
        tl.pause(wait_expire=True)
        self.assertLess(time.time() - t0, 0.7)


if __name__ == "__main__":
    unittest.main()

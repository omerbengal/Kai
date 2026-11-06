"""Typing lease: typing must stop BEFORE the final reply and never come back
from trailing tool calls (fix 2026-09-23, msg 14616)."""
import os, sys, time, json, threading, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import typing_lease as tl


class TypingLeaseTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._saved = (tl.LEASE_FILE, tl.INFLIGHT_FILE, tl.RESUME_GRACE, tl._ensure_loop,
                       tl.LAST_SEND_FILE, tl.TYPING_VISIBLE, tl.ACK_REGISTRY)
        tl.ACK_REGISTRY = self.dir
        tl.LAST_SEND_FILE = os.path.join(self.dir, "last_send.txt")
        tl.LEASE_FILE = os.path.join(self.dir, "lease.json")
        tl.INFLIGHT_FILE = os.path.join(self.dir, "inflight.txt")
        tl._ensure_loop = lambda lease: dict(lease, pid=1234)   # never spawn a real loop

    def tearDown(self):
        (tl.LEASE_FILE, tl.INFLIGHT_FILE, tl.RESUME_GRACE, tl._ensure_loop,
         tl.LAST_SEND_FILE, tl.TYPING_VISIBLE, tl.ACK_REGISTRY) = self._saved

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

    # --- tasks (05/11/2026, Omer msgs 22485 / 22505) ---------------------------------------------
    def _ack(self, key, ack_id):
        tl.ACK_REGISTRY = self.dir
        with open(os.path.join(self.dir, key + ".ack"), "w") as f:
            f.write(str(ack_id))

    def _delete_ack(self, ack_id):
        cmd = ("python -c \"urllib.request.urlopen('https://api.telegram.org/botX/deleteMessage"
               "?chat_id=${KAI_TELEGRAM_CHAT_ID}&message_id=%s')\"" % ack_id)
        tl.renew("Bash", {"command": cmd})

    def test_worker_keeps_typing_after_dispatcher_turn_ends(self):
        self._ack("msg_100", 101)
        tl.open_lease("1", msg_id="100", ack_key="msg_100")
        tl.renew("Agent", {"prompt": "x"})            # dispatcher spawns the worker
        self.assertEqual(tl.end_turn(), 1)             # Stop: the lease stays
        self.assertTrue(os.path.exists(tl.LEASE_FILE))
        tl.renew("Bash", {"command": "echo work"})     # the worker's calls keep it alive
        self.assertGreater(self.lease()["deadline"], time.time())

    def test_inline_task_ends_at_turn_end(self):
        tl.open_lease("1", msg_id="200", ack_key="msg_200")
        tl.pause(reply_to="200")
        self.assertEqual(tl.end_turn(), 0)
        self.assertFalse(os.path.exists(tl.LEASE_FILE))
        tl.renew("Bash", {"command": "echo later"})    # a later unrelated call never revives it
        self.assertFalse(os.path.exists(tl.LEASE_FILE))

    def test_two_workers_reply1_resumes_reply2_stops_for_good(self):
        self._ack("msg_10", 11)
        self._ack("msg_12", 13)
        tl.open_lease("1", msg_id="10", ack_key="msg_10")
        tl.renew("Agent", {})
        tl.end_turn()
        tl.open_lease("1", msg_id="12", ack_key="msg_12")
        tl.renew("Agent", {})
        tl.end_turn()
        self.assertEqual(set(self.lease()["tasks"]), {"10", "12"})
        tl.pause(reply_to="10")                        # worker 1 final reply
        self.assertGreater(self.lease()["resume_at"], time.time())   # typing comes back for worker 2
        self._delete_ack(11)                           # worker 1 cleanup
        self.assertEqual(set(self.lease()["tasks"]), {"12"})
        self.assertTrue(self.lease()["resume_at"] > 0)
        tl.pause(reply_to="12")                        # worker 2 final reply
        self.assertEqual(self.lease()["resume_at"], 0)
        self._delete_ack(13)                           # last ACK gone: the lease is removed
        self.assertFalse(os.path.exists(tl.LEASE_FILE))
        tl.renew("Bash", {"command": "action_log insert"})
        self.assertFalse(os.path.exists(tl.LEASE_FILE))

    def test_single_worker_final_reply_no_resume(self):
        self._ack("msg_30", 31)
        tl.open_lease("1", msg_id="30", ack_key="msg_30")
        tl.renew("Agent", {})
        tl.end_turn()
        tl.pause(reply_to="30")
        self.assertEqual(self.lease()["resume_at"], 0)
        tl.renew("Bash", {"command": "unpin 31"})      # trailing call (no deleteMessage): no revive
        self.assertEqual(self.lease()["deadline"], 0)
        self._delete_ack(31)
        self.assertFalse(os.path.exists(tl.LEASE_FILE))

    def test_unrelated_delete_does_not_end_task(self):
        self._ack("msg_40", 41)
        tl.open_lease("1", msg_id="40", ack_key="msg_40")
        tl.renew("Agent", {})
        self._delete_ack(4100)                         # different id (prefix of digits must not match)
        self.assertIn("40", self.lease()["tasks"])

    def test_never_sticks_replied_task_without_ack_delete_expires(self):
        tl.open_lease("1", msg_id="50")
        tl.renew("Agent", {})
        tl.end_turn()
        tl.pause(reply_to="50")
        lease = self.lease()
        lease["tasks"]["50"]["replied_at"] = time.time() - tl.REPLIED_TTL - 1
        lease["paused_at"] = time.time() - tl.RESUME_GRACE - 1
        with open(tl.LEASE_FILE, "w") as f:
            json.dump(lease, f)
        tl.renew("Bash", {"command": "unrelated later call"})
        self.assertFalse(os.path.exists(tl.LEASE_FILE))

    def test_never_sticks_crashed_worker_ttl(self):
        tl.open_lease("1", msg_id="60")
        tl.renew("Agent", {})
        tl.end_turn()
        lease = self.lease()
        lease["tasks"]["60"]["opened"] = time.time() - tl.TASK_TTL - 1
        with open(tl.LEASE_FILE, "w") as f:
            json.dump(lease, f)
        tl.renew("Bash", {"command": "x"})
        self.assertFalse(os.path.exists(tl.LEASE_FILE))

    def test_loop_state_wait_then_send_then_exit(self):
        import typing_loop as lp
        lp.LEASE_FILE = tl.LEASE_FILE
        now = time.time()
        with open(tl.LEASE_FILE, "w") as f:
            json.dump({"pid": 77, "deadline": 0, "resume_at": now + 5}, f)
        self.assertEqual(lp.lease_state(77), "wait")
        with open(tl.LEASE_FILE, "w") as f:
            json.dump({"pid": 77, "deadline": 0, "resume_at": now - 1}, f)
        self.assertEqual(lp.lease_state(77), "send")
        with open(tl.LEASE_FILE, "w") as f:
            json.dump({"pid": 77, "deadline": 0, "resume_at": now - 20}, f)
        self.assertEqual(lp.lease_state(77), "exit")    # resume window bounded
        with open(tl.LEASE_FILE, "w") as f:
            json.dump({"pid": 77, "deadline": 0, "resume_at": 0, "paused_at": now}, f)
        self.assertEqual(lp.lease_state(77), "exit")    # a plain pause ends the loop
        os.remove(tl.LEASE_FILE)
        self.assertEqual(lp.lease_state(77), "exit")


if __name__ == "__main__":
    unittest.main()

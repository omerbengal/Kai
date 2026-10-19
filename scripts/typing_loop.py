#!/usr/bin/env python3
"""
Typing loop — sends 'typing' chat action to Telegram every 4 seconds.
Accepts its own PID file path and auto-exits when:
  - The PID file is deleted (normal stop_typing flow)
  - The PID file contains a different PID (race condition — another script took over)

Usage: python typing_loop.py <chat_id> <pid_file_path> [initial_delay_seconds]
Kill (Windows): taskkill /PID <pid> /F

initial_delay_seconds (optional, default 0): wait this long before the FIRST
typing send, while still honoring pid-file removal. Used by the "resume after an
intermediate reply" flow (typing_session_resume.py): if the turn ends during the
delay, the Stop hook removes the pid file and this loop exits WITHOUT ever sending,
so no typing flash appears after the final message.
"""
import sys, time, os, json, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import net_ipv4  # noqa: F401  IPv6 to Telegram is broken here: +3s per send without this

LEASE_FILE = "D:/Kai/tmp/typing_lease.json"
INFLIGHT_FILE = "D:/Kai/tmp/typing_inflight.txt"  # see typing_lease.pause()
LAST_SEND_FILE = "D:/Kai/tmp/typing_last_send.txt"  # see typing_lease._wait_typing_expired()

BOT_TOKEN = os.environ["KAI_TELEGRAM_BOT_TOKEN"]


def _tlog(event, extra=""):
    """Timestamped trace of the typing loop (investigation: indicator not visible)."""
    try:
        import datetime
        stamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = "{}  {:<14} {}".format(stamp, event, extra)
        with open("D:/Kai/tmp/typing_trace.log", "a", encoding="utf-8") as f:
            f.write(line + chr(10))
    except Exception:
        pass


def _record_last_send():
    """Atomically store the epoch of the last SUCCESSFUL sendChatAction. Used by
    typing_lease.pause(wait_expire=True) before a react: a reaction does not
    clear client-side typing, so it waits ~5.5s past this timestamp."""
    try:
        tmp = LAST_SEND_FILE + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(repr(time.time()))
        os.replace(tmp, LAST_SEND_FILE)
    except Exception:
        pass


def send_typing(chat_id):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendChatAction?chat_id={chat_id}&action=typing"
    try:
        body = urllib.request.urlopen(url, timeout=3).read().decode("utf-8", "replace")
        _tlog("send", body[:60])
        if '"ok":true' in body.replace(" ", ""):
            _record_last_send()
    except Exception as e:
        _tlog("send-FAIL", repr(e)[:80])


def pid_file_still_mine(pid_file, my_pid):
    """Return True if the PID file exists and still contains our PID."""
    try:
        with open(pid_file) as f:
            return int(f.read().strip()) == my_pid
    except Exception:
        return False  # file gone or unreadable → exit


def _read_lease():
    try:
        with open(LEASE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def lease_claims_me(my_pid):
    """True if the lease file currently names this process as its loop.

    Used once to latch into 'lease mode'. The lease is written by
    typing_lease._ensure_loop immediately after Popen, so it is on disk long
    before this interpreter finishes starting.
    """
    lease = _read_lease()
    return bool(lease) and int(lease.get("pid", 0)) == my_pid


def lease_alive(my_pid):
    """True while this loop still holds a valid, unexpired lease."""
    lease = _read_lease()
    if lease is None:
        return False                      # lease gone → exit
    if int(lease.get("pid", 0)) != my_pid:
        return False                      # another loop took over → exit
    return float(lease.get("deadline", 0)) > time.time()


def main():
    if len(sys.argv) < 3:
        print("Usage: typing_loop.py <chat_id> <pid_file_path>")
        sys.exit(1)

    chat_id = sys.argv[1]
    pid_file = sys.argv[2]
    try:
        initial_delay = int(sys.argv[3]) if len(sys.argv) >= 4 else 0
    except ValueError:
        initial_delay = 0
    my_pid = os.getpid()

    # Write own PID before the loop starts — prevents race condition where the
    # parent process (start_typing.py) hasn't written the file yet and the first
    # pid_file_still_mine() check returns False, causing the loop to exit immediately.
    try:
        with open(pid_file, "w") as f:
            f.write(str(my_pid))
    except Exception:
        return  # can't establish PID file, exit cleanly

    # Optional initial delay (resume-after-reply flow) — send NOTHING yet, but exit
    # promptly if the pid file is removed (turn ended → no typing flash after the
    # final message). Checked once per second so a stop is honored within ~1s.
    for _ in range(initial_delay):
        if not pid_file_still_mine(pid_file, my_pid):
            return
        time.sleep(1)

    # No MAX_RUNTIME cap any more (investigation #043). The lease IS the bound:
    # it must be renewed by tool activity (PreToolUse hook) or this loop dies
    # within LEASE_SECONDS. This also fixes the opposite complaint (indicator
    # vanishing at minute 10/30 during genuinely long work) — as long as tools
    # are running, the lease lives.
    #
    # Two exit disciplines, never mixed:
    #   lease mode  — this loop was spawned by typing_lease.py (the lease names
    #                 our pid). Exit condition is the lease alone.
    #   legacy mode — spawned by start_typing.py for a per-message key. Exit
    #                 condition is the pid file, exactly as before, so the old
    #                 path keeps working until it is fully removed.
    # (Deviation from the spec's `lease_alive(...) or pid_file_still_mine(...)`:
    #  this loop writes its OWN pid into pid_file at startup, so that OR would
    #  always be true and a lease loop could never expire.)
    lease_mode = "--lease" in sys.argv

    if lease_mode:
        # typing_lease.py writes the lease naming our pid a few milliseconds
        # after Popen, which can land after this interpreter starts. Wait up to
        # 2s for it. If it never names us, the lease was closed before we got
        # going (or another loop took over) - exit without ever typing.
        for _ in range(20):
            if lease_claims_me(my_pid):
                break
            time.sleep(0.1)
        else:
            try:
                os.remove(pid_file)
            except Exception:
                pass
            return

    def should_continue():
        # In lease mode the lease is the ONLY exit condition - never fall back to
        # the pid file, which this process writes itself and would keep alive
        # forever.
        if lease_mode:
            return lease_alive(my_pid)
        return pid_file_still_mine(pid_file, my_pid)

    def guarded_send():
        """Lease mode: mark the send in flight, THEN re-check the lease. pause()
        writes deadline=0 THEN waits for the marker to clear - so either we see
        the pause and skip, or pause waits for our send to land before the reply
        goes out. Returns False if the lease ended."""
        if not lease_mode:
            send_typing(chat_id)
            return True
        try:
            with open(INFLIGHT_FILE, "w") as f:
                f.write(str(my_pid))
        except Exception:
            pass
        try:
            if not lease_alive(my_pid):
                return False
            send_typing(chat_id)
            return True
        finally:
            try:
                os.remove(INFLIGHT_FILE)
            except Exception:
                pass

    while should_continue():
        if not guarded_send():
            break
        # Check once per second so a stop is honored within ~1s.
        stopped = False
        for _ in range(4):
            time.sleep(1)
            if not should_continue():
                stopped = True
                break
        if stopped:
            break

    try:
        os.remove(pid_file)
    except Exception:
        pass


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Single owner of the Telegram typing indicator (investigation #043).

The indicator is a LEASE, not a process someone must remember to kill:
  open_lease(chat_id)  - only on an incoming user message (UserPromptSubmit)
  renew()              - on every tool call, from ANY agent (PreToolUse)
  pause()              - before every reply (PreToolUse reply): deadline=0
  pause(wait_expire)   - before every react: also waits until the client-side
                         typing (~5s after the last send) has expired
  close()              - at turn end (Stop): remove the lease entirely

"Stop typing FIRST, then send" (fix 2026-09-23, msg 14616). Omer saw typing
for ~12s AFTER the final reply. Trace showed PAUSE followed by more sends until
CLOSE: tool calls running with/after the final reply (ACK delete, CronCreate,
action_log, and the async tick of a call that started before the reply) called
renew() and revived the paused lease. Three guarantees now:
  1. pause() stamps paused_at. renew() will not revive a paused lease for
     RESUME_GRACE seconds, so trailing cleanup can never bring typing back. A
     tool call after the grace is genuine continued work (interim reply) and
     does resume typing.
  2. All read-modify-write of the lease is under a file lock, so an async tick
     that read the lease before pause() cannot write back a live deadline.
  3. pause() returns only when no sendChatAction is in flight (the loop marks
     INFLIGHT_FILE around each send and re-checks the lease after marking), so
     no typing action can reach Telegram after the reply it precedes.

If renewals stop for ANY reason - turn ended, background worker finished,
agent crashed, session compacted, async race - the loop dies within
LEASE_SECONDS. No Stop hook, no SubagentStop hook and no ordering guarantee
is required for correctness.
"""
import os, sys, json, time, subprocess

LEASE_FILE = "D:/Kai/tmp/typing_lease.json"
PID_FILE = "D:/Kai/tmp/typing_pid_session.txt"
INFLIGHT_FILE = "D:/Kai/tmp/typing_inflight.txt"   # written by typing_loop.py around each send
RESUME_GRACE = 10       # seconds after a pause during which renew() cannot revive typing
INFLIGHT_WAIT = 3.5     # max seconds pause() waits for an in-flight send (loop timeout is 3s)
LAST_SEND_FILE = "D:/Kai/tmp/typing_last_send.txt"  # epoch of last SUCCESSFUL sendChatAction (typing_loop.py)
TYPING_VISIBLE = 5.5    # Telegram clients show "typing" ~5s after the last sendChatAction
TYPING_LOOP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "typing_loop.py")
LEASE_SECONDS = 15
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def _read():
    try:
        with open(LEASE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write(lease):
    tmp = LEASE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lease, f)
    os.replace(tmp, LEASE_FILE)   # atomic - no half-written lease is ever read


class _Lock:
    """Tiny cross-process lock (O_EXCL lock file). Stale after 2s; if it cannot
    be taken within 1s we proceed anyway - typing must never block a tool."""
    def __enter__(self):
        self.path = LEASE_FILE + ".lock"
        self.fd = None
        end = time.time() + 1.0
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > 2:
                        os.remove(self.path)
                        continue
                except Exception:
                    pass
                if time.time() > end:
                    return self
                time.sleep(0.02)
            except Exception:
                return self

    def __exit__(self, *a):
        if self.fd is not None:
            try:
                os.close(self.fd)
                os.remove(self.path)
            except Exception:
                pass


def _alive(pid):
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:
        return False


def _ensure_loop(lease):
    """Spawn a loop only if none is alive. Writes the pid INTO the lease first,
    so the loop can never be born orphaned (root cause #2 of investigation #043)."""
    if lease.get("pid") and _alive(lease["pid"]):
        return lease
    proc = subprocess.Popen(
        # "--lease": tell the loop its exit condition is the lease, not the pid
        # file. Without it a lease loop would fall back to pid-file semantics and
        # never expire, because the loop writes its own pid into that file.
        [sys.executable, TYPING_LOOP, str(lease["chat_id"]), PID_FILE, "0", "--lease"],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        close_fds=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    lease["pid"] = proc.pid
    return lease


def open_lease(chat_id):
    """New incoming message: the ONLY thing that clears a pause."""
    with _Lock():
        lease = {"chat_id": str(chat_id), "deadline": time.time() + LEASE_SECONDS,
                 "turn_open": True, "pid": 0, "paused_at": 0}
        lease = _ensure_loop(lease)
        _write(lease)


def renew():
    """Extend an OPEN lease. Never creates one - a tool call alone must not be
    able to switch the indicator on. Never revives a lease paused less than
    RESUME_GRACE seconds ago (trailing calls after the final reply)."""
    with _Lock():
        lease = _read()
        if not lease or not lease.get("turn_open"):
            return
        paused_at = float(lease.get("paused_at", 0) or 0)
        if paused_at and time.time() - paused_at < RESUME_GRACE:
            return
        lease["paused_at"] = 0
        lease["deadline"] = time.time() + LEASE_SECONDS
        lease = _ensure_loop(lease)
        _write(lease)


def _wait_no_inflight():
    end = time.time() + INFLIGHT_WAIT
    while time.time() < end:
        try:
            if time.time() - os.path.getmtime(INFLIGHT_FILE) > INFLIGHT_WAIT + 1:
                return          # stale marker from a killed loop
        except Exception:
            return              # no marker -> nothing in flight
        time.sleep(0.02)


def _last_send():
    try:
        with open(LAST_SEND_FILE, encoding="utf-8") as f:
            return float(f.read().strip() or 0)
    except Exception:
        return 0.0


def _wait_typing_expired():
    """A REACTION does not clear the client-side typing status the way a sent
    message does (fix 2026-09-23, msg 14622/14623): typing stays visible ~5s
    after the last sendChatAction. Wait until it has expired, so the reaction
    appears only after typing is gone. No wait if the last send is old/absent."""
    remaining = _last_send() + TYPING_VISIBLE - time.time()
    if remaining > 0:
        time.sleep(min(remaining, TYPING_VISIBLE))


def pause(wait_expire=False):
    """Before a reply/react: stop typing NOW (synchronous PreToolUse hook).
    Returns only after any in-flight sendChatAction has completed, so the
    output is the last thing Telegram sees.

    wait_expire=True (react only): additionally wait until the typing status
    has visibly expired on the client. A reply does not need this - a sent
    message clears typing immediately - so replies stay fast."""
    had_lease = False
    with _Lock():
        lease = _read()
        if lease:
            had_lease = True
            lease["deadline"] = 0
            lease["paused_at"] = time.time()
            _write(lease)
    _wait_no_inflight()
    if wait_expire and had_lease:
        _wait_typing_expired()


def close():
    """Turn end. Best-effort only - lease expiry already bounds everything."""
    for path in (LEASE_FILE, "D:/Kai/tmp/typing_resume_armed.json"):
        try:
            os.remove(path)
        except Exception:
            pass


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "open" and len(sys.argv) > 2:
        open_lease(sys.argv[2])
    elif cmd == "renew":
        renew()
    elif cmd == "pause":
        pause(wait_expire="--react" in sys.argv)
    elif cmd == "close":
        close()

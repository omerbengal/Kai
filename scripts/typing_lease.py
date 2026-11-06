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

TASKS (fix 05/11/2026, Omer msgs 22485 / 22505). Two flaws of the single lease:
  a. the Stop hook dropped the lease when the DISPATCHER's turn ended, i.e. right after it spawned the
     background worker - the worker's tool calls then renewed nothing and typing died while it worked;
  b. with two workers, worker 1's final reply paused the lease and nothing brought it back in time for
     worker 2.
Now the lease counts tasks: every incoming message opens one (its message id + the auto-ACK registry key).
  - an Agent / SendMessage call hands the turn's tasks to a worker ("handed");
  - Stop (end_turn) drops the tasks that were NOT handed (inline / fast-script answers) and removes the lease
    only when no task is left, so a worker keeps renewing after the dispatcher's turn;
  - a task ends when its ACK is deleted (any tool call whose input carries "deleteMessage" and the ACK id -
    the dispatcher's inline urllib, the worker completion block), when 90s pass after a reply to it, or after
    TASK_TTL; when the LAST task ends the lease file is removed, so no trailing call can revive typing;
  - a reply to one task while another handed task is open sets resume_at: the loop waits RESUME_DELAY and
    resumes typing for the other task without waiting for its next tool call.
"""
import re
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
AGENT_HOLD = 60         # Agent / SendMessage: a background worker needs up to ~55s for its first call
RESUME_DELAY = 3        # another task is still open after a reply: typing back after this
TASK_TTL = 1800         # a task never closed (crashed worker) stops counting after 30 min
REPLIED_TTL = 90        # a task that got a reply and whose ACK delete was not seen: gone after this
ACK_REGISTRY = "D:/Kai/tmp/auto_ack"   # scripts/auto_ack.py: <key>.ack holds the ACK message id
HANDOFF_TOOLS = ("Agent", "Task", "SendMessage")
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


def _tasks(lease, now=None):
    """Open tasks, without the expired ones (TASK_TTL; REPLIED_TTL after a reply)."""
    now = now or time.time()
    out = {}
    for k, v in (lease.get("tasks") or {}).items():
        if now - float(v.get("opened", 0) or 0) >= TASK_TTL:
            continue
        r = float(v.get("replied_at", 0) or 0)
        if r and now - r >= REPLIED_TTL:
            continue
        out[k] = v
    return out


def _ack_id(task):
    key = task.get("ack_key")
    if not key:
        return None
    try:
        with open(os.path.join(ACK_REGISTRY, key + ".ack"), encoding="utf-8") as f:
            v = f.read().strip()
        return v if v.isdigit() else None
    except Exception:
        return None


def _acks_deleted(tasks, tool_input):
    """Task keys whose ACK this tool call deletes (Bot API deleteMessage with the ACK id in its input)."""
    try:
        text = tool_input if isinstance(tool_input, str) else json.dumps(tool_input or {})
    except Exception:
        return []
    if "deleteMessage" not in text:
        return []
    gone = []
    for k, t in tasks.items():
        ack = _ack_id(t)
        if ack and re.search(r"(?<!\d)" + ack + r"(?!\d)", text):
            gone.append(k)
    return gone


def _remove_lease():
    try:
        os.remove(LEASE_FILE)
    except Exception:
        pass


def open_lease(chat_id, msg_id=None, ack_key=None, thread_id=None):
    """New incoming message: the ONLY thing that opens the indicator / clears a pause. Opens a task.
    thread_id: forum topic to type in (General of the bots group = "1", 06/11/2026). The running loop reads
    chat_id / thread_id from the lease before every send, so a new message in another chat moves typing there."""
    now = time.time()
    with _Lock():
        old = _read() or {}
        tasks = _tasks(old, now)
        if msg_id:
            tasks[str(msg_id)] = {"opened": now, "handed": False, "ack_key": ack_key, "replied_at": 0,
                                  "chat_id": str(chat_id), "thread_id": str(thread_id) if thread_id else None}
        lease = {"chat_id": str(chat_id), "thread_id": str(thread_id) if thread_id else None,
                 "deadline": now + LEASE_SECONDS,
                 "turn_open": True, "dispatcher_turn": True, "pid": old.get("pid", 0) or 0,
                 "paused_at": 0, "resume_at": 0, "tasks": tasks}
        lease = _ensure_loop(lease)
        _write(lease)


def renew(tool_name=None, tool_input=None):
    """Extend an OPEN lease. Never creates one - a tool call alone must not be
    able to switch the indicator on. Never revives a lease paused less than
    RESUME_GRACE seconds ago (trailing calls after the final reply).
    Also: an Agent / SendMessage call hands the open tasks to a worker; a call that deletes a task's ACK
    ends that task, and the last task's end removes the lease."""
    now = time.time()
    with _Lock():
        lease = _read()
        if not lease or not lease.get("turn_open"):
            return
        tasks = _tasks(lease, now)
        gone = _acks_deleted(tasks, tool_input) if tool_input else []
        for k in gone:
            tasks.pop(k, None)
        if gone and not tasks:
            _remove_lease()          # the last task is done: nothing may revive typing
            return
        hold = LEASE_SECONDS
        if tool_name in HANDOFF_TOOLS:
            for t in tasks.values():
                t["handed"] = True
            hold = AGENT_HOLD
        lease["tasks"] = tasks
        if not tasks and not lease.get("dispatcher_turn", True):
            _remove_lease()          # turn over, every task expired
            return
        paused_at = float(lease.get("paused_at", 0) or 0)
        if paused_at and now - paused_at < RESUME_GRACE:
            if gone and tasks:       # another task's ACK went: let the loop resume for the rest
                lease["resume_at"] = max(float(lease.get("resume_at", 0) or 0), now + 1)
            _write(lease)
            return
        lease["paused_at"] = 0
        lease["resume_at"] = 0
        lease["deadline"] = max(float(lease.get("deadline", 0) or 0), now + hold)
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


def pause(wait_expire=False, reply_to=None):
    """Before a reply/react: stop typing NOW (synchronous PreToolUse hook).
    Returns only after any in-flight sendChatAction has completed, so the
    output is the last thing Telegram sees.

    wait_expire=True (react only): additionally wait until the typing status
    has visibly expired on the client. A reply does not need this - a sent
    message clears typing immediately - so replies stay fast."""
    had_lease = False
    now = time.time()
    with _Lock():
        lease = _read()
        if lease:
            had_lease = True
            lease["deadline"] = 0
            lease["paused_at"] = now
            lease["resume_at"] = 0
            tasks = _tasks(lease, now)
            if reply_to and str(reply_to) in tasks:
                tasks[str(reply_to)]["replied_at"] = now
                others = [k for k, t in tasks.items() if k != str(reply_to) and t.get("handed")]
                if others:           # another worker is still busy: typing back after the reply
                    lease["resume_at"] = now + RESUME_DELAY
                    last = max(others, key=lambda k: float(tasks[k].get("opened", 0) or 0))
                    if tasks[last].get("chat_id"):   # ...in that task's chat / topic
                        lease["chat_id"] = tasks[last]["chat_id"]
                        lease["thread_id"] = tasks[last].get("thread_id")
            lease["tasks"] = tasks
            _write(lease)
    _wait_no_inflight()
    if wait_expire and had_lease:
        _wait_typing_expired()


def close():
    """Remove the lease entirely (session start cleanup; no task survives)."""
    for path in (LEASE_FILE, "D:/Kai/tmp/typing_resume_armed.json"):
        try:
            os.remove(path)
        except Exception:
            pass


def end_turn():
    """Stop hook: the dispatcher's turn is over. Tasks it handed to a worker stay (the worker keeps
    renewing); the rest were answered inline / by a fast script and end now. No task left = close().
    Returns the number of tasks kept."""
    with _Lock():
        lease = _read()
        if not lease:
            return 0
        tasks = {k: t for k, t in _tasks(lease).items() if t.get("handed")}
        if tasks:
            lease["tasks"] = tasks
            lease["dispatcher_turn"] = False
            _write(lease)
            return len(tasks)
    close()
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "open" and len(sys.argv) > 2:
        open_lease(sys.argv[2], thread_id=sys.argv[3] if len(sys.argv) > 3 else None)
    elif cmd == "renew":
        renew()
    elif cmd == "pause":
        pause(wait_expire="--react" in sys.argv)
    elif cmd == "close":
        close()
    elif cmd == "end-turn":
        end_turn()

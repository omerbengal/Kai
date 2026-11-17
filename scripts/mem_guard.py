#!/usr/bin/env python
"""Memory guard (investigation 044, 17/11/2026).

On 17/11 a Flutter build in a domain bot pushed the PC past its commit limit: Windows logged
"low virtual memory" (event 2004) and main Kai's claude.exe died in the same minute. The pagefile was
200MB-2GB, so the limit was barely above the RAM, and 6 Claude sessions + a Flutter build hit it.

What matters on Windows is the COMMIT that is still free (RAM + pagefile not yet promised to any
process), not free physical RAM: a process dies when a commit request fails, even with RAM to spare.

CLI (heavy jobs call it first: flutter build / full flutter test / flutter run, big local models):
  python D:/Kai/scripts/mem_guard.py                 exit 0 = enough free, 1 = not enough (prints why)
  python D:/Kai/scripts/mem_guard.py --min-gb 6      the threshold (default 6GB free commit)
  python D:/Kai/scripts/mem_guard.py --wait 300      re-check every 15s for up to 300s before failing
  python D:/Kai/scripts/mem_guard.py --status        print the numbers + top memory users, exit 0
Never raises: if the numbers cannot be read it says so and exits 0 (a guard must not block work by itself).
"""
import ctypes
import sys
import time

DEFAULT_MIN_GB = 6.0
GB = 1024 ** 3


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),   # = the commit LIMIT (RAM + pagefile)
        ("ullAvailPageFile", ctypes.c_ulonglong),   # = commit still free
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def commit_status():
    """{'limit', 'free', 'phys_total', 'phys_free'} in bytes, or None if it cannot be read."""
    try:
        st = _MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        return {"limit": st.ullTotalPageFile, "free": st.ullAvailPageFile,
                "phys_total": st.ullTotalPhys, "phys_free": st.ullAvailPhys}
    except Exception:
        return None


def top_memory(n: int = 3):
    """[(name, pid, private_bytes)] of the n processes holding the most commit (private bytes)."""
    try:
        import psutil
    except Exception:
        return []
    rows = []
    for p in psutil.process_iter(["name", "pid"]):
        try:
            mi = p.memory_info()
            rows.append((p.info["name"] or "?", p.info["pid"], getattr(mi, "private", 0) or mi.rss))
        except Exception:
            continue
    rows.sort(key=lambda r: r[2], reverse=True)
    return rows[:n]


def fmt_top(rows) -> str:
    return ", ".join(f"{name} ({pid}) {b / GB:.1f}GB" for name, pid, b in rows)


def describe(st=None, top=None) -> str:
    st = st if st is not None else commit_status()
    if not st:
        return "memory status unavailable"
    top = top if top is not None else top_memory(3)
    line = (f"commit free {st['free'] / GB:.1f}GB of {st['limit'] / GB:.1f}GB, "
            f"RAM free {st['phys_free'] / GB:.1f}GB of {st['phys_total'] / GB:.1f}GB")
    return line + (f"; top: {fmt_top(top)}" if top else "")


def enough(min_gb: float = DEFAULT_MIN_GB, st=None) -> bool:
    st = st if st is not None else commit_status()
    if not st:
        return True  # unknown never blocks
    return st["free"] >= min_gb * GB


def main(argv) -> int:
    min_gb = DEFAULT_MIN_GB
    wait_s = 0
    if "--min-gb" in argv:
        min_gb = float(argv[argv.index("--min-gb") + 1])
    if "--wait" in argv:
        wait_s = int(argv[argv.index("--wait") + 1])
    if "--status" in argv:
        print(describe())
        return 0
    deadline = time.time() + wait_s
    while True:
        st = commit_status()
        if enough(min_gb, st):
            print(f"OK: {describe(st, top=[])}")
            return 0
        if time.time() >= deadline:
            print(f"NOT ENOUGH MEMORY (need {min_gb:.0f}GB free commit): {describe(st, top_memory(5))}")
            print("Do not start the heavy job now: wait, or stop a dev server / build you started.")
            return 1
        time.sleep(15)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""Publish web/call.html to Omer's GitHub Pages site and switch on tap-to-dial links.

Run ONLY after Omer approved adding the page to his personal public site (repo and URL
in config/call_page.json: {"repo_url": ..., "page_url": ...}). Steps: clone the site repo
into tmp/, copy call.html to its root, commit, push, wait until the page is live, then
write config/phone_links.json so phone_links.py starts linking numbers.

Usage: python D:/Kai/scripts/deploy_call_page.py [--check]   (--check = only report state)
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "web", "call.html")
SRC_JS = os.path.join(ROOT, "web", "call.js")  # dial logic, loaded by call.html with a cache-buster
with open(os.path.join(ROOT, "config", "call_page.json"), encoding="utf-8") as _f:
    _site = json.load(_f)
REPO_URL = _site["repo_url"]
PAGE_URL = _site["page_url"]
WORK = os.path.join(ROOT, "tmp", "call_page_site")
CONFIG = os.path.join(ROOT, "config", "phone_links.json")


def live():
    try:
        js_url = PAGE_URL.rsplit("/", 1)[0] + "/call.js?t=" + str(time.time())
        with open(SRC_JS, "rb") as f:
            want = f.read()
        with urllib.request.urlopen(js_url, timeout=15) as r:
            got = r.read()
        norm = lambda b: b.replace(bytes((13, 10)), bytes((10,))).strip()
        return r.status == 200 and norm(got) == norm(want)
    except Exception:
        return False


def _rmtree(path):
    """rmtree that also removes git's read-only object files on Windows."""
    import stat
    def _onerror(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if os.path.exists(path):
        shutil.rmtree(path, onerror=_onerror)


def git(*args):
    subprocess.run(["git", "-C", WORK, *args], check=True)


def main():
    if "--check" in sys.argv:
        print(f"live={live()} config={os.path.exists(CONFIG)}")
        return
    _rmtree(WORK)
    subprocess.run(["git", "clone", "--depth", "1", REPO_URL, WORK], check=True)
    shutil.copyfile(SRC, os.path.join(WORK, "call.html"))
    shutil.copyfile(SRC_JS, os.path.join(WORK, "call.js"))
    git("add", "call.html", "call.js")
    if subprocess.run(["git", "-C", WORK, "diff", "--cached", "--quiet"]).returncode != 0:
        git("commit", "-m", "Add call.html: tap-to-dial bridge for Kai reminders")
        git("push", "origin", "HEAD")
    for _ in range(40):  # GitHub Pages build usually takes 30-90s
        if live():
            break
        time.sleep(6)
    else:
        print("NOT_LIVE (pushed, Pages not serving yet - re-run with --check later)")
        sys.exit(1)
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    cfg = {}
    if os.path.exists(CONFIG):  # keep "bot" / "mini_app" set earlier
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg["dial_base"] = PAGE_URL
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    _rmtree(WORK)
    print("LIVE " + PAGE_URL)


if __name__ == "__main__":
    main()

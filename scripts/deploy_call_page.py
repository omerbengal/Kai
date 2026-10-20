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
with open(os.path.join(ROOT, "config", "call_page.json"), encoding="utf-8") as _f:
    _site = json.load(_f)
REPO_URL = _site["repo_url"]
PAGE_URL = _site["page_url"]
WORK = os.path.join(ROOT, "tmp", "call_page_site")
CONFIG = os.path.join(ROOT, "config", "phone_links.json")


def live():
    try:
        with urllib.request.urlopen(PAGE_URL + "?n=1", timeout=15) as r:
            return r.status == 200 and b"tel:" in r.read()
    except Exception:
        return False


def git(*args):
    subprocess.run(["git", "-C", WORK, *args], check=True)


def main():
    if "--check" in sys.argv:
        print(f"live={live()} config={os.path.exists(CONFIG)}")
        return
    if os.path.exists(WORK):
        shutil.rmtree(WORK, ignore_errors=True)
    subprocess.run(["git", "clone", "--depth", "1", REPO_URL, WORK], check=True)
    shutil.copyfile(SRC, os.path.join(WORK, "call.html"))
    git("add", "call.html")
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
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump({"dial_base": PAGE_URL}, f, indent=2)
    shutil.rmtree(WORK, ignore_errors=True)
    print("LIVE " + PAGE_URL)


if __name__ == "__main__":
    main()

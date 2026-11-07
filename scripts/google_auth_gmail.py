"""
OAuth flow that mints Kai's Gmail token (credentials/google_token_gmail.json).

Scopes: gmail.send + gmail.compose (fast_email.py sends and drafts) and, since
07/11/2026, gmail.readonly (read messages and download attachments, which the
Gmail MCP cannot do for large emails).

Two ways to run it:

1. On the PC, interactively (opens a local browser):
       python D:/Kai/scripts/google_auth_gmail.py --local

2. From Omer's phone, in two steps (no browser on the PC needed):
       python D:/Kai/scripts/google_auth_gmail.py --url
           prints the Google consent URL. Omer opens it on the phone and approves.
           Google then redirects to http://localhost/?code=... which fails to load
           on the phone; Omer copies that address-bar URL back to Kai.
       python D:/Kai/scripts/google_auth_gmail.py --code "<that URL or the bare code>"
           exchanges the code and writes the token.

The existing token is backed up and replaced only after a successful exchange,
so the send flow keeps working until the new token is in place. The pending
PKCE verifier lives in credentials/ (gitignored), never in Telegram.
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from google_auth_oauthlib.flow import Flow, InstalledAppFlow

CLIENT_SECRET = "D:/Kai/credentials/google_client_secret.json"
TOKEN_PATH = "D:/Kai/credentials/google_token_gmail.json"
PENDING_PATH = "D:/Kai/credentials/gmail_oauth_pending.json"
REDIRECT_URI = "http://localhost"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def _save_token(creds):
    data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES),
        "universe_domain": "googleapis.com",
        "account": "",
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }
    if not creds.refresh_token:
        print("ERROR: no refresh_token returned, token NOT saved. Run --url again.",
              file=sys.stderr)
        return 1
    if os.path.exists(TOKEN_PATH):
        bak = TOKEN_PATH + ".bak-" + datetime.now().strftime("%Y%m%d%H%M%S")
        shutil.copy2(TOKEN_PATH, bak)
        print(f"backup: {bak}")
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"saved: {TOKEN_PATH}")
    print("scopes:", ", ".join(data["scopes"]))
    return 0


def cmd_local():
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
    # access_type=offline + prompt=consent guarantees a refresh_token comes back.
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    return _save_token(creds)


def cmd_url():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET, scopes=SCOPES, redirect_uri=REDIRECT_URI,
        autogenerate_code_verifier=True)
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    with open(PENDING_PATH, "w", encoding="utf-8") as f:
        json.dump({"state": state, "code_verifier": flow.code_verifier,
                   "created": datetime.now().isoformat()}, f)
    print(url)
    return 0


def cmd_code(value):
    if not os.path.exists(PENDING_PATH):
        print("no pending auth: run --url first", file=sys.stderr)
        return 1
    with open(PENDING_PATH, encoding="utf-8") as f:
        pending = json.load(f)
    value = value.strip()
    code = value
    if value.startswith("http"):
        qs = parse_qs(urlparse(value).query)
        if "error" in qs:
            print("google returned error: " + qs["error"][0], file=sys.stderr)
            return 1
        if qs.get("state", [pending["state"]])[0] != pending["state"]:
            print("state mismatch: this URL is from another --url run", file=sys.stderr)
            return 1
        code = qs.get("code", [""])[0]
    if not code:
        print("no code found in the input", file=sys.stderr)
        return 1
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRET, scopes=SCOPES, redirect_uri=REDIRECT_URI,
        state=pending["state"], code_verifier=pending["code_verifier"])
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    flow.fetch_token(code=code)
    rc = _save_token(flow.credentials)
    if rc == 0:
        os.remove(PENDING_PATH)
    return rc


def main():
    if not os.path.exists(CLIENT_SECRET):
        print(f"missing client secret: {CLIENT_SECRET}", file=sys.stderr)
        return 1
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--local", action="store_true", help="interactive flow on this PC")
    g.add_argument("--url", action="store_true", help="print the consent URL (phone flow)")
    g.add_argument("--code", help="redirect URL or bare code from the phone flow")
    a = ap.parse_args()
    if a.local:
        return cmd_local()
    if a.url:
        return cmd_url()
    return cmd_code(a.code)


if __name__ == "__main__":
    sys.exit(main())

"""
One-time OAuth flow that mints a Gmail-send token for Kai.

Run this once, interactively, from a terminal on Omer's machine:

    python D:/Kai/scripts/google_auth_gmail.py

It opens a browser, asks Omer to approve the gmail.send + gmail.compose scopes,
and writes credentials/google_token_gmail.json. After that, fast_email.py can
send mail (with attachments) straight from disk, with no base64 round-trip
through an MCP tool call.

The existing google_token.json (calendar) and google_token_docs.json (docs) are
left untouched — each scope set gets its own token file, so re-authorising one
never invalidates the others.
"""
import json
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

CLIENT_SECRET = "D:/Kai/credentials/google_client_secret.json"
TOKEN_PATH = "D:/Kai/credentials/google_token_gmail.json"

# gmail.send is enough to send. gmail.compose is included so a future
# fast_email.py --draft can create drafts without another consent round.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
]


def main():
    if not os.path.exists(CLIENT_SECRET):
        print(f"missing client secret: {CLIENT_SECRET}", file=sys.stderr)
        return 1

    if os.path.exists(TOKEN_PATH):
        print(f"{TOKEN_PATH} already exists.")
        print("Delete it first if you want to re-authorise.")
        return 0

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
    # access_type=offline + prompt=consent guarantees a refresh_token comes back,
    # otherwise the token silently expires in an hour and never renews.
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

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
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    if not creds.refresh_token:
        print("WARNING: no refresh_token returned — the token will stop working "
              "in about an hour. Delete the file and run this again.", file=sys.stderr)

    print(f"saved: {TOKEN_PATH}")
    print("scopes:", ", ".join(data["scopes"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

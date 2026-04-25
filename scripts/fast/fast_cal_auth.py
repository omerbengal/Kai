#!/usr/bin/env python3
"""
One-time Google Calendar OAuth setup.
Run this script ONCE from a machine with a browser to generate google_token.json.

Steps:
  1. Place google_client_secret.json in D:/Kai/credentials/
  2. Run: python D:/Kai/scripts/fast/fast_cal_auth.py
  3. A browser will open — sign in with your Google account and grant access
  4. google_token.json is saved to D:/Kai/credentials/
  5. Done — all calendar fast scripts will now work automatically
"""
import os
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CLIENT_SECRET_PATH = "D:/Kai/credentials/google_client_secret.json"
TOKEN_PATH = "D:/Kai/credentials/google_token.json"


def main():
    if not os.path.exists(CLIENT_SECRET_PATH):
        print(f"ERROR: Client secret not found at {CLIENT_SECRET_PATH}")
        print("Download it from Google Cloud Console → APIs & Services → Credentials → Download JSON")
        return

    print("Opening browser for Google authentication...")
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_PATH, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())

    print(f"✅ Authentication successful! Token saved to {TOKEN_PATH}")
    print("All calendar fast scripts will now work automatically.")


if __name__ == "__main__":
    main()

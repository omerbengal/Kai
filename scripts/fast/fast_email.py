"""
Fast script: send an email (optionally with attachments) via the Gmail API.

Reads attachments straight off disk, so a 40MB-free 40KB docx costs nothing —
unlike the Gmail MCP, which needs the whole file inlined as base64 inside the
tool call. That round-trip is what made the 2026-09-01 send take ~20 minutes.

Requires credentials/google_token_gmail.json. Mint it once with:
    python D:/Kai/scripts/google_auth_gmail.py

Usage:
    python D:/Kai/scripts/fast/fast_email.py --to "a@b.com" --subject "..." \
        --body "..." --file "D:/path/one.docx" --file "D:/path/two.pdf" \
        --ack_id ACK --orig_id ORIG --chat_id ${KAI_TELEGRAM_CHAT_ID}
"""
import base64
import json
import mimetypes
import os
import sys
from email.message import EmailMessage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _base import base_args, finish, fail, RLM  # noqa: E402

TOKEN_PATH = "D:/Kai/credentials/google_token_gmail.json"

# Gmail rejects anything over 25MB, counting base64 overhead (~4/3 of raw bytes).
# Cap the raw total a little under that so we fail with a clear message instead
# of a 400 from the API.
MAX_ATTACHMENT_BYTES = 18 * 1024 * 1024


def load_credentials():
    """Load the Gmail token, refreshing it if expired. Returns (creds, error_text)."""
    if not os.path.exists(TOKEN_PATH):
        return None, ("אין טוקן Gmail. הרץ פעם אחת: "
                      "python D:/Kai/scripts/google_auth_gmail.py")
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        return None, "חסרות ספריות google-auth. התקן google-auth ו-google-api-python-client"

    try:
        with open(TOKEN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        creds = Credentials.from_authorized_user_info(data, data.get("scopes"))
    except Exception as e:
        return None, f"טוקן Gmail לא תקין: {e}"

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                return None, f"רענון הטוקן נכשל: {e}"
            # Persist the refreshed access token so the next run starts valid.
            try:
                with open(TOKEN_PATH, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
            except Exception:
                pass  # refresh worked; caching it is best-effort
        else:
            return None, "הטוקן פג ואין refresh_token. הרץ שוב את google_auth_gmail.py"
    return creds, None


def build_message(to, cc, subject, body, files):
    """Build a MIME message with attachments read from disk. Returns (msg, error_text)."""
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject or ""
    msg.set_content(body or "")

    total = 0
    for path in files:
        if not os.path.exists(path):
            return None, f"קובץ לא נמצא: {path}"
        size = os.path.getsize(path)
        total += size
        if total > MAX_ATTACHMENT_BYTES:
            return None, f"הקבצים גדולים מדי ({total // (1024*1024)}MB). המגבלה היא 18MB"
        ctype, encoding = mimetypes.guess_type(path)
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        with open(path, "rb") as f:
            msg.add_attachment(f.read(), maintype=maintype, subtype=subtype,
                               filename=os.path.basename(path))
    return msg, None


def main():
    p = base_args("Send an email via the Gmail API, with optional attachments")
    p.add_argument("--to", required=True, action="append",
                   help="Recipient address. Repeat the flag for multiple recipients.")
    p.add_argument("--cc", action="append", default=[], help="CC address. Repeatable.")
    p.add_argument("--subject", default="")
    p.add_argument("--body", default="")
    p.add_argument("--file", action="append", default=[], dest="files",
                   help="Absolute path to an attachment. Repeatable.")
    args = p.parse_args()

    creds, err = load_credentials()
    if err:
        fail(args, err)
        return 1

    msg, err = build_message(args.to, args.cc, args.subject, args.body, args.files)
    if err:
        fail(args, err)
        return 1

    try:
        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    except Exception as e:
        fail(args, f"שליחת המייל נכשלה: {e}")
        return 1

    recipients = ", ".join(args.to)
    lines = [f"{RLM}✅ המייל נשלח אל {recipients}"]
    if args.subject:
        lines.append(f"{RLM}נושא: {args.subject}")
    if args.files:
        names = ", ".join(os.path.basename(f) for f in args.files)
        lines.append(f"{RLM}מצורף: {names}")
    finish(args, "\n\n".join(lines))
    print("gmail_id:", sent.get("id"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

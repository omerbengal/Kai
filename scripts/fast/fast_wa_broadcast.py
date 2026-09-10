#!/usr/bin/env python3
"""
Bulk WhatsApp broadcast — same text to N phone numbers.

DIFFERENT from fast_whatsapp.py:
  - No SUFFIX (`[נשלח על ידי קאי 🤖]`) — Omer explicitly opted out for broadcasts.
  - Reads phones from a file (one per line, # comments allowed).
  - Sends sequentially with a configurable delay (default 1.0s).
  - Batches: pauses for `--pause_duration` seconds (default 60) every
    `--pause_every` sends (default 11) to avoid WhatsApp rate-limiting.
    Set `--pause_every 0` to disable batching.
  - Updates ACK with progress every 10 sends, plus before/after each pause.
  - Per-number success/failure reporting.

Usage:
  python fast_wa_broadcast.py \
      --phones_file D:/Kai/tmp/phones.txt \
      --message "Text to send" \
      --delay 1.0 --pause_every 11 --pause_duration 60 \
      --ack_id N --orig_id N --chat_id ${KAI_TELEGRAM_CHAT_ID}
"""
import sys
import os
import re
import csv
import time
import json as _json
import urllib.request
from datetime import datetime
sys.path.insert(0, 'D:/Kai/scripts/fast')
from _base import *

LOGS_DIR = "D:/Kai/logs"


def edit_ack(chat_id, msg_id, text):
    payload = _json.dumps({
        "chat_id": chat_id,
        "message_id": int(msg_id),
        "text": text,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def clean_phone(raw):
    """Normalize a phone string to 972XXXXXXXXX. Returns None if invalid."""
    # Strip whitespace, ASCII hyphen, common Unicode dashes (U+2010..U+2015, U+2212), parens, plus.
    s = re.sub(r"[\s\-‐-―−\(\)\+]", "", raw)
    if not s.isdigit():
        return None
    if s.startswith("972"):
        return s if len(s) == 12 else None
    if s.startswith("0"):
        return ("972" + s[1:]) if len(s) == 10 else None
    if s.startswith("5") and len(s) == 9:
        return "972" + s
    return None


def main():
    p = base_args("WhatsApp bulk broadcast — same text to all phones, no signature")
    p.add_argument("--phones_file", required=True,
                   help="Path to file with one phone per line. Lines starting with # are comments.")
    p.add_argument("--message", required=True,
                   help="Message text. Sent verbatim to all recipients. No signature appended.")
    p.add_argument("--delay", type=float, default=1.0,
                   help="Seconds between sends (default 1.0).")
    p.add_argument("--pause_every", type=int, default=11,
                   help="Pause every N sends (default 11). Set 0 to disable batching.")
    p.add_argument("--pause_duration", type=float, default=60.0,
                   help="Pause duration in seconds between batches (default 60).")
    args = p.parse_args()

    if not os.path.isfile(args.phones_file):
        fail(args, f"קובץ לא נמצא: {args.phones_file}")
        return

    raw_lines = []
    with open(args.phones_file, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            raw_lines.append(stripped)

    if not raw_lines:
        fail(args, "קובץ המספרים ריק")
        return

    valid = []
    invalid = []
    for raw in raw_lines:
        c = clean_phone(raw)
        if c:
            valid.append((raw, c))
        else:
            invalid.append(raw)

    if not valid:
        sample = ", ".join(invalid[:5])
        fail(args, f"כל המספרים בקובץ לא תקינים. דוגמאות: {sample}")
        return

    total = len(valid)
    sent = 0
    failed = []  # list of (raw_phone, error_msg)

    # Per-send CSV log so we can always reconstruct the per-number outcome.
    os.makedirs(LOGS_DIR, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOGS_DIR, f"broadcast_{run_id}.csv")
    log_fh = open(log_path, "w", encoding="utf-8-sig", newline="")
    log_w = csv.writer(log_fh)
    log_w.writerow(["idx", "raw_phone", "normalized", "status", "error", "ts"])
    log_fh.flush()

    edit_ack(args.chat_id, args.ack_id, f"📤 שולח 0/{total}...")
    log_ack(args.ack_id, "edited", f"📤 שולח 0/{total}...")

    for idx, (raw, phone) in enumerate(valid, 1):
        jid = f"{phone}@s.whatsapp.net"
        err_msg = ""
        status = "sent"
        try:
            ok = whatsapp_send(jid, args.message)
            if ok:
                sent += 1
            else:
                status = "failed"
                err_msg = "send returned false"
                failed.append((raw, err_msg))
        except Exception as e:
            status = "failed"
            err_msg = str(e)[:200]
            failed.append((raw, err_msg[:60]))
        log_w.writerow([idx, raw, phone, status, err_msg, datetime.now().isoformat(timespec="seconds")])
        log_fh.flush()

        if idx % 10 == 0 or idx == total:
            edit_ack(
                args.chat_id, args.ack_id,
                f"📤 שולח {idx}/{total} — נכשלו {len(failed)}...",
            )

        if idx < total:
            # Batch pause: every N sends, pause for a longer duration to avoid rate-limiting
            if args.pause_every > 0 and idx % args.pause_every == 0:
                pause_secs = int(args.pause_duration)
                if pause_secs >= 60 and pause_secs % 60 == 0:
                    pause_label = f"{pause_secs // 60} דקות" if pause_secs > 60 else "דקה"
                else:
                    pause_label = f"{pause_secs}ש"
                edit_ack(
                    args.chat_id, args.ack_id,
                    f"⏸️ הפסקה ({pause_label}) — נשלחו {idx}/{total}, נכשלו {len(failed)}...",
                )
                time.sleep(args.pause_duration)
                edit_ack(
                    args.chat_id, args.ack_id,
                    f"📤 ממשיך — נשלחו {idx}/{total}, נכשלו {len(failed)}...",
                )
            else:
                time.sleep(args.delay)

    parts = [f"{RLM}✅ שידור הסתיים: {sent}/{total} נשלחו."]
    if failed:
        parts.append(f"{RLM}❌ נכשלו {len(failed)}:")
        for raw, err in failed[:20]:
            parts.append(f"{RLM}• {raw} — {err}")
        if len(failed) > 20:
            parts.append(f"{RLM}…+ עוד {len(failed) - 20} נכשלו")
    if invalid:
        parts.append(f"{RLM}⚠️ {len(invalid)} מספרים לא תקינים בקובץ (דולגו):")
        for raw in invalid[:10]:
            parts.append(f"{RLM}• {raw}")
        if len(invalid) > 10:
            parts.append(f"{RLM}…+ עוד {len(invalid) - 10}")

    log_fh.close()
    parts.append(f"{RLM}📄 לוג מלא: `{log_path}`")
    final_text = "\n".join(parts)

    stop_typing(args.orig_id)
    if getattr(args, "voice_transcription", None):
        final_text = f'{RLM}🎤 "{args.voice_transcription}"\n\n{final_text}'
    tg_reply(args.chat_id, final_text, reply_to=args.orig_id)
    delete_ack(args.ack_id, args.chat_id)
    action_log(
        "fast_wa_broadcast",
        "broadcast",
        f"sent={sent}/{total} failed={len(failed)} invalid={len(invalid)}",
    )
    print("ok")


if __name__ == "__main__":
    main()

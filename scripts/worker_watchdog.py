#!/usr/bin/env python3
"""
Worker watchdog — called by dispatcher after spawning a worker.
Waits for timeout, then checks if the ACK message was deleted (= worker completed).
If ACK still exists after timeout → sends fallback error to Omer.

Usage: python worker_watchdog.py <ack_msg_id> <original_msg_id> <timeout_seconds>
Run in background: python D:/Kai/scripts/worker_watchdog.py 993 992 180 &
"""
import sys, time, urllib.request, sqlite3, json

BOT_TOKEN = __import__("os").environ["KAI_TELEGRAM_BOT_TOKEN"]
CHAT_ID = __import__("os").environ.get("KAI_TELEGRAM_CHAT_ID", "")
DB_PATH = "D:/Kai/kai.db"


def ack_still_exists(ack_msg_id):
    """Check if ACK message still exists in DB (not yet deleted by worker)."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM messages WHERE telegram_message_id = ?", (int(ack_msg_id),))
        count = cur.fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        # If DB check fails, assume worker is still running — don't fire false positive
        return False


def send_fallback(original_msg_id):
    text = "⚠️ נראה שהעובד נתקע — לא התקבלה תשובה תוך 3 דקות. שלח את הבקשה מחדש."
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": CHAT_ID,
        "text": text,
        "reply_to_message_id": int(original_msg_id)
    }).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"watchdog: failed to send fallback: {e}", file=sys.stderr)


def main():
    if len(sys.argv) < 4:
        print("Usage: worker_watchdog.py <ack_msg_id> <original_msg_id> <timeout_seconds>")
        sys.exit(1)

    ack_msg_id = sys.argv[1]
    original_msg_id = sys.argv[2]
    timeout = int(sys.argv[3])

    time.sleep(timeout)

    if ack_still_exists(ack_msg_id):
        send_fallback(original_msg_id)


if __name__ == "__main__":
    main()

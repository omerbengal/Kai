#!/usr/bin/env python3
"""
Typing loop — sends 'typing' chat action to Telegram every 4 seconds.
Accepts its own PID file path and auto-exits when:
  - The PID file is deleted (normal stop_typing flow)
  - The PID file contains a different PID (race condition — another script took over)

Usage: python typing_loop.py <chat_id> <pid_file_path>
Kill (Windows): taskkill /PID <pid> /F
"""
import sys, time, os, urllib.request

BOT_TOKEN = os.environ["KAI_TELEGRAM_BOT_TOKEN"]


def send_typing(chat_id):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendChatAction?chat_id={chat_id}&action=typing"
    try:
        urllib.request.urlopen(url, timeout=3)
    except Exception:
        pass


def pid_file_still_mine(pid_file, my_pid):
    """Return True if the PID file exists and still contains our PID."""
    try:
        with open(pid_file) as f:
            return int(f.read().strip()) == my_pid
    except Exception:
        return False  # file gone or unreadable → exit


def main():
    if len(sys.argv) < 3:
        print("Usage: typing_loop.py <chat_id> <pid_file_path>")
        sys.exit(1)

    chat_id = sys.argv[1]
    pid_file = sys.argv[2]
    my_pid = os.getpid()

    # Write own PID before the loop starts — prevents race condition where the
    # parent process (start_typing.py) hasn't written the file yet and the first
    # pid_file_still_mine() check returns False, causing the loop to exit immediately.
    try:
        with open(pid_file, "w") as f:
            f.write(str(my_pid))
    except Exception:
        return  # can't establish PID file, exit cleanly

    # Safety timeout — auto-exit after 10 minutes regardless of PID file state.
    # Caps any forgotten stop_typing call from leaking indefinitely (investigation #016).
    MAX_RUNTIME_SECONDS = 10 * 60
    start_time = time.time()

    while pid_file_still_mine(pid_file, my_pid):
        if time.time() - start_time > MAX_RUNTIME_SECONDS:
            try:
                os.remove(pid_file)
            except Exception:
                pass
            return
        send_typing(chat_id)
        time.sleep(4)


if __name__ == "__main__":
    main()

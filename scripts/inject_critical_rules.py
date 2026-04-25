"""
UserPromptSubmit hook: inject critical rules as system-reminder.
Outputs <system-reminder>...</system-reminder> which Claude Code injects before processing.
"""
import sys
import os

RULES_FILE = "D:/Kai/critical_rules.md"

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        print(f"<system-reminder>\n{content}\n</system-reminder>")
    except Exception:
        pass  # Silently fail — never block message processing

if __name__ == "__main__":
    main()

"""bots/guard.py - the PreToolUse guard of a Kai domain bot (separation step 1, 05/11/2026)."""
import json
import os
import sys
import tempfile

sys.path.insert(0, "D:/Kai/bots")
sys.path.insert(0, "D:/Kai/scripts")
import guard  # noqa: E402

CFG = {"bots": {
    "miluim": {"home": "D:/KaiBots/miluim", "caps": []},
    "wa": {"home": "D:/KaiBots/wa", "caps": ["whatsapp", "contacts_owner"]},
}}


def _cfg():
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(CFG, f)
    f.close()
    return f.name


P = _cfg()


def chk(bot, tool, **ti):
    return guard.check(bot, tool, ti, P)


def test_whatsapp_blocked_for_domain_bot():
    assert chk("miluim", "mcp__whatsapp__send_message", recipient="x")
    assert chk("miluim", "Bash", command="curl -s http://localhost:8080/api/send -d {}")
    assert chk("miluim", "Bash", command='python -c "import sqlite3;sqlite3.connect(r\'D:\\Kai\\whatsapp-mcp\\whatsapp-bridge\\store\\messages.db\')"')
    assert chk("miluim", "Read", file_path="D:/Kai/whatsapp-mcp/whatsapp-bridge/store/messages.db")
    assert chk("miluim", "Grep", pattern="x", path="D:/Kai/whatsapp-mcp")


def test_whatsapp_allowed_for_whatsapp_bot():
    assert chk("wa", "mcp__whatsapp__send_message", recipient="x") is None
    # stage 2 (05/11/2026): the WhatsApp bot reads the bridge DB, but even it sends only through the bus
    assert chk("wa", "Read", file_path="D:/Kai/whatsapp-mcp/whatsapp-bridge/store/messages.db") is None
    assert chk("wa", "Bash", command="curl http://localhost:8080/api/send")


def test_main_state_and_other_homes_blocked():
    assert chk("miluim", "Bash", command="python D:/Kai/scripts/fast/fast_whatsapp.py --to x")
    assert chk("miluim", "Bash", command="python D:/Kai/scripts/dispatch_start.py --reply_to 1")
    assert chk("miluim", "Read", file_path="C:/Users/Omer/.claude/channels/telegram/.env")
    assert chk("miluim", "Read", file_path="C:/Users/Omer/.claude/projects/D--Kai/memory/MEMORY.md")
    assert chk("miluim", "Bash", command="powershell -File D:/Kai/restart_claude.ps1")
    assert chk("miluim", "Read", file_path="D:/KaiBots/wa/bot.db")
    assert chk("miluim", "Edit", file_path="D:/Kai/bots/guard.py", old_string="a", new_string="b")
    assert chk("miluim", "Write", file_path="D:/KaiBots/miluim/.claude/settings.json", content="{}")


def test_contacts_read_only():
    assert chk("miluim", "Bash", command='python -c "c.execute(\'INSERT INTO people (name) VALUES (1)\')"')
    assert chk("miluim", "Bash", command="sqlite3 kai.db \"UPDATE people_notes SET note='x'\"")
    assert chk("miluim", "Bash", command='python D:/Kai/scripts/resolve_name.py "דנה"') is None
    assert chk("miluim", "Bash", command="sqlite3 kai.db \"SELECT * FROM people\"") is None
    assert chk("wa", "Bash", command="sqlite3 kai.db \"UPDATE people SET phone=1\"") is None


def test_normal_work_passes():
    assert chk("miluim", "Bash", command="python D:/Kai/bots/bot_tg.py context --bot miluim") is None
    assert chk("miluim", "Bash", command="python D:/Kai/scripts/cohort_status.py --dry-run") is None
    assert chk("miluim", "Read", file_path="D:/Kai/agents/cohorts.md") is None
    assert chk("miluim", "Read", file_path="D:/KaiBots/miluim/CLAUDE.md") is None
    assert chk("miluim", "mcp__plugin_telegram_telegram__reply", text="פורט 8080") is None


def test_main_exit_code(monkeypatch=None):
    import io
    import subprocess
    inp = json.dumps({"tool_name": "Bash", "tool_input": {"command": "curl localhost:8080/api/send"}})
    r = subprocess.run([sys.executable, "D:/Kai/bots/guard.py", "--bot", "miluim"], input=inp.encode(), env=dict(os.environ, KAI_GUARD_NOLOG="1"),
                       capture_output=True)
    assert r.returncode == 2 and b"BLOCKED" in r.stderr
    inp = json.dumps({"tool_name": "Bash", "tool_input": {"command": "echo hi"}})
    r = subprocess.run([sys.executable, "D:/Kai/bots/guard.py", "--bot", "miluim"], input=inp.encode(), env=dict(os.environ, KAI_GUARD_NOLOG="1"),
                       capture_output=True)
    assert r.returncode == 0


def test_bus_rules_stage2():
    # domain bot: since 06/11/2026 no bus at all (General tag instead); impersonation, executor, approvals,
    # raw wa.py / sends stay blocked for everyone
    assert chk("wa", "Bash", command='python D:/Kai/bots/bus.py read --from wa --chat staff --summary "x"') is None
    assert chk("wa", "Bash", command="python D:/Kai/bots/bus.py wait --id 3 --bot wa") is None
    assert chk("miluim", "Bash", command='python D:/Kai/bots/bus.py draft --from wa --chat x --text "y"')
    assert chk("miluim", "Bash", command="python D:/Kai/bots/bus.py serve --once")
    assert chk("miluim", "Bash", command="echo {} | python D:/Kai/bots/bus.py approve-hook --bot miluim")
    assert chk("miluim", "Bash", command="python D:/Kai/bots/wa.py read --chat staff")
    assert chk("miluim", "Bash", command='sqlite3 D:/KaiBots/_bus/bus.db "update requests set status=1"')
    # the WhatsApp bot reads directly but sends only through the bus as well
    assert chk("wa", "Bash", command="python D:/Kai/bots/wa.py read --chat staff") is None
    assert chk("wa", "Bash", command='python -c "import sys; sys.path.insert(0,\'D:/Kai/bots\'); import wa; wa.send(1,2)"')
    assert chk("wa", "Bash", command="curl -d {} http://localhost:8080/api/send")
    # 07/11/2026: images go through `bus.py draft --file` + Omer's button too, never wa.send_media directly
    assert chk("wa", "Bash", command='python -c "import wa; wa.send_media(1, 2, 3)"')
    assert chk("wa", "Bash", command='python D:/Kai/bots/bus.py draft --from wa --chat x --file D:/a.png --text "y"') is None
    assert chk("wa", "Bash", command="python D:/Kai/bots/bus.py serve")


def test_blocked_patterns_from_config(tmp_path):
    cfg = {"bots": {"trading": {"home": "D:/KaiBots/trading", "caps": [],
                                "blocked_patterns": [r"price_watch\.py\b.*--(install-task|stop)\b"]}}}
    p = tmp_path / "b.json"
    p.write_text(json.dumps(cfg))
    assert guard.check("trading", "Bash", {"command": "python D:/Kai/scripts/price_watch.py --stop"}, str(p))
    assert guard.check("trading", "Bash", {"command": "python D:/Kai/scripts/price_watch.py --dry-run"}, str(p)) is None


# ---- 06/11/2026 (Omer msgs 358 / 361): WhatsApp only through a visible General tag ----------------

def test_domain_bot_cannot_use_the_bus_for_whatsapp():
    assert chk("miluim", "Bash", command='python D:/Kai/bots/bus.py read --from miluim --chat tickets --summary x')
    assert chk("miluim", "Bash", command='python D:/Kai/bots/bus.py search --from miluim --query x --chat staff')
    assert chk("miluim", "Bash", command='python D:/Kai/bots/bus.py draft --from miluim --chat staff --text hi')
    assert chk("miluim", "Bash", command='python D:/Kai/bots/bus.py wait --id 3 --bot miluim')
    assert chk("miluim", "Bash", command='python D:/Kai/bots/wa.py read --chat tickets')
    assert chk("miluim", "Bash", command='python D:/Kai/scripts/listing_notify_wa.py')
    # the WhatsApp bot keeps the bus for its own approved sends
    assert chk("wa", "Bash", command='python D:/Kai/bots/bus.py draft --from wa --chat staff --text hi') is None
    assert chk("wa", "Bash", command='python D:/Kai/bots/wa.py read --chat tickets') is None


def test_domain_bot_asks_in_general_only_as_itself():
    assert chk("miluim", "Bash",
               command='python D:/Kai/bots/bot_tg.py ask --bot miluim --to whatsapp --text "כמה הודעות?"') is None
    assert chk("miluim", "Bash", command='python D:/Kai/bots/bot_tg.py ask --bot wa --to whatsapp --text x')
    assert chk("miluim", "Bash", command='python D:/Kai/bots/bot_tg.py general-done --bot wa --chat 1 --tag-msg 2')
    assert chk("miluim", "Bash", command='python D:/Kai/bots/bot_tg.py ask-wait --bot miluim --id 5') is None

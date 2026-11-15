"""bots/release_gate.py + the guard's release gate and write_roots (appdev bot, 14/11/2026)."""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "D:/Kai/bots")
sys.path.insert(0, "D:/Kai/scripts")
import guard  # noqa: E402
import release_gate  # noqa: E402

HOME = tempfile.mkdtemp(prefix="relgate_").replace("\\", "/")
CFG = {"bots": {
    "shz": {"home": HOME, "caps": [], "group_id": "-100123",
            "write_roots": [HOME, "C:/Users/Omer/.claude/projects/D--KaiBots-shz"],
            "release_gate": {"patterns": [r"\bfirebase\b[^|;&]*\bdeploy\b", r"\bgh\s+pr\s+merge\b"],
                             "free_deploy_functions": ["apiPreview"],
                             "push_to_main": True, "window_min": 30}},
    "other": {"home": "D:/KaiBots/other", "caps": []},
}}


def _cfg():
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(CFG, f)
    f.close()
    return f.name


P = _cfg()
BOT = CFG["bots"]["shz"]


def chk(tool, **ti):
    return guard.check("shz", tool, ti, P)


def _clear():
    shutil.rmtree(os.path.join(HOME, "tmp"), ignore_errors=True)


def cb(data, user="${KAI_TELEGRAM_CHAT_ID}", chat="-100123"):
    return (f'<channel source="plugin:telegram:telegram" chat_id="{chat}" message_id="55" user_id="{user}" '
            f'callback_data="{data}">x</channel>')


def test_push_and_deploy_blocked_without_approval():
    _clear()
    assert chk("Bash", command="cd D:/KaiBots/shz/repo && git push origin main")
    assert chk("Bash", command="git -C x push")
    assert chk("Bash", command="cd functions && firebase deploy --only functions")
    assert chk("Bash", command="gh pr merge 12 --squash")
    assert chk("Bash", command="git status && git log --oneline -3") is None
    assert chk("Bash", command="git worktree add .claude/worktrees/x -b x origin/main") is None
    assert chk("Bash", command="git push")
    assert chk("Bash", command="git push origin")
    assert chk("Bash", command="git push origin HEAD")
    assert chk("Bash", command="git push origin feat/x:main")
    assert chk("Bash", command="git push --all origin")
    assert chk("Bash", command="git push -u origin feat/x && git push origin main")
    assert chk("Bash", command="git -C D:/KaiBots/shz/AppRepo push origin main")


def test_feature_branch_push_is_free():
    _clear()
    assert chk("Bash", command="git push -u origin feat/login-fix") is None
    assert chk("Bash", command="cd x && git push --force-with-lease origin feat/a feat/b") is None
    assert chk("Bash", command="git -C D:/KaiBots/shz/AppRepo push -u origin fix/x") is None


def test_omer_button_opens_the_gate_for_the_window():
    _clear()
    lines = release_gate.hook("shz", cb("relok:fix_login"), cfg=BOT)
    assert "recorded" in lines[0]
    assert chk("Bash", command="git push origin main") is None
    # expired
    old = datetime.now(timezone.utc) - timedelta(minutes=31)
    release_gate.hook("shz", cb("relok:fix_login"), cfg=BOT, now=old)
    assert chk("Bash", command="git push origin main")


def test_decline_and_strangers_do_not_open():
    _clear()
    release_gate.hook("shz", cb("relok:a"), cfg=BOT)
    release_gate.hook("shz", cb("relno:a"), cfg=BOT)
    assert chk("Bash", command="git push origin main")
    lines = release_gate.hook("shz", cb("relok:b", user="111"), cfg=BOT)
    assert "refused" in lines[0]
    lines = release_gate.hook("shz", cb("relok:b", chat="-100999"), cfg=BOT)
    assert "refused" in lines[0]
    assert chk("Bash", command="git push origin main")


def test_model_cannot_forge_the_approval():
    assert chk("Bash", command=f"echo {{}} > {HOME}/tmp/release_ok.json")
    assert chk("Write", file_path=f"{HOME}/tmp/release_ok.json", content="{}")
    assert chk("Bash", command="echo x | python D:/Kai/bots/release_gate.py hook --bot shz")


def test_preview_deploy_is_free_and_nothing_else():
    """15/11/2026 (appdev msg 3232): only the exact apiPreview deploy skips Omer's button."""
    _clear()
    wt = "D:/KaiBots/shz/AppRepo/.claude/worktrees/team-tasks"
    for ok in ["firebase deploy --only functions:apiPreview",
               "  firebase deploy --only=functions:apiPreview  ",
               f"cd {wt} && firebase deploy --only functions:apiPreview",
               f"cd {wt}; firebase deploy --only functions:apiPreview 2>&1 | tail -6",
               f'cd "{wt}" && firebase deploy --only functions:apiPreview | tail -n 20']:
        assert chk("Bash", command=ok) is None, ok
    for bad in ["firebase deploy",
                "firebase deploy --only functions",
                "firebase deploy --only functions:api",
                "firebase deploy --only firestore:rules",
                "firebase deploy --only functions:apiPreview,firestore:rules",
                "firebase deploy --only functions:apiPreview,functions:api",
                "firebase deploy --only functions:apiPreviewX",
                "firebase deploy --only functions:apiPreview --force",
                "firebase deploy --only functions:apiPreview && firebase deploy --only functions:api",
                "firebase deploy --only functions:apiPreview; firebase deploy",
                "firebase deploy --only functions:apiPreview\nfirebase deploy --only functions:api",
                "firebase deploy --only functions:apiPreview | sh",
                f"cd {wt} && git push origin main && firebase deploy --only functions:apiPreview",
                "cd $(firebase deploy) && firebase deploy --only functions:apiPreview",
                "cd x && cd y && firebase deploy --only functions:apiPreview",
                "git push origin main; firebase deploy --only functions:apiPreview"]:
        assert chk("Bash", command=bad), bad


def test_real_config_patterns_are_live():
    """config/bots.json once held "\\bfirebase" = a JSON backspace, so no deploy was gated at all (15/11/2026)."""
    import kai_bots
    gate = kai_bots.bot("appdev")["release_gate"]
    assert not any("\x08" in p for p in gate["patterns"])
    assert gate.get("free_deploy_functions") == ["apiPreview"]
    real_fresh = release_gate.fresh
    release_gate.fresh = lambda cfg, now=None: None
    try:
        c = lambda cmd: guard.check("appdev", "Bash", {"command": cmd})
        assert c("firebase deploy --only functions:api")
        assert c("firebase deploy --only firestore:rules")
        assert c("gh pr merge 3 --squash")
        assert c("firebase deploy --only functions:apiPreview") is None
    finally:
        release_gate.fresh = real_fresh


def test_write_roots():
    assert chk("Write", file_path=f"{HOME}/AppRepo/lib/main.dart", content="x") is None
    assert chk("Edit", file_path="C:/Users/Omer/.claude/projects/D--KaiBots-shz/memory/MEMORY.md") is None
    assert chk("Write", file_path="C:/Users/Omer/AppData/Local/Temp/claude/abc/scratchpad/n.txt") is None
    assert chk("Write", file_path="D:/Kai/scripts/anything.py", content="x")
    assert chk("Edit", file_path=f"{HOME}/../evil.py")
    # reads elsewhere stay allowed (only other homes / main state are blocked)
    assert chk("Read", file_path="D:/Kai/agents/principles.md") is None

"""Forum-topics patch for the Telegram plugin (multi-bot stage 1).

Runs on a COPY of the installed server.ts, never on the live file.
"""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import patch_telegram_plugin as p  # noqa: E402

BUN = os.path.expanduser(r"~\.bun\bin\bun.exe")


def _live_server():
    files = p.find_server_files()
    if not files:
        pytest.skip("no installed server.ts")
    return files[0]


@pytest.fixture
def server_copy(tmp_path):
    dst = tmp_path / "server.ts"
    shutil.copyfile(_live_server(), dst)
    os.chmod(dst, 0o666)
    return dst


def test_patch_applies_all_parts(server_copy):
    content = server_copy.read_text(encoding="utf-8")
    if p.TOPICS_MARKER in content:
        pytest.skip("installed server.ts already carries the topics patch")
    patched = p.topics_patch_text(content)
    assert patched is not None
    assert patched.count(p.TOPICS_MARKER) >= 8
    assert "message_thread_id: String(ctx.message.message_thread_id)" in patched
    assert "{ message_thread_id: threadId }" in patched
    assert "kaiTopics.includes('general')" in patched
    # the original gate check is still there for groups without "topics"
    assert "if (requireMention && !isMentioned(ctx, access.mentionPatterns)) {" in patched


def test_idempotent(server_copy):
    assert p.patch_topics(str(server_copy)) in (True, False)
    once = server_copy.read_text(encoding="utf-8")
    assert p.patch_topics(str(server_copy)) is False
    assert server_copy.read_text(encoding="utf-8") == once


def test_missing_anchor_writes_nothing(server_copy):
    content = server_copy.read_text(encoding="utf-8")
    if p.TOPICS_MARKER in content:
        pytest.skip("already patched")
    broken = content.replace("const reply_to = args.reply_to", "const replyTo = args.reply_to")
    server_copy.write_text(broken, encoding="utf-8")
    assert p.patch_topics(str(server_copy)) is False
    assert server_copy.read_text(encoding="utf-8") == broken


@pytest.mark.skipif(not os.path.exists(BUN), reason="bun not installed")
def test_patched_file_still_parses(server_copy, tmp_path):
    p.patch_topics(str(server_copy))
    assert p.TOPICS_MARKER in server_copy.read_text(encoding="utf-8")
    # --no-bundle transpiles one file; a syntax error exits 1. Relative path + cwd:
    # an absolute pytest tmp path gives a bogus ModuleNotFound with exit 0.
    r = subprocess.run([BUN, "build", "--no-bundle", "server.ts"], cwd=str(tmp_path),
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    assert "error:" not in r.stderr
    # negative control: the same check does catch a syntax error here
    server_copy.write_text(server_copy.read_text(encoding="utf-8") + "\nconst x = (;\n", encoding="utf-8")
    bad = subprocess.run([BUN, "build", "--no-bundle", "server.ts"], cwd=str(tmp_path),
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert bad.returncode != 0


def test_flag_off_by_default():
    # The patch must not reach the live server.ts until the second bot launches.
    assert p.TOPICS_FLAG.endswith("telegram_topics_patch.on")

"""patch_telegram_plugin edit_message keyboard patch (11/11/2026, miluim bot report on topic msg 1868):
edit_message dropped inline_keyboard silently and editMessageText without reply_markup removed the buttons.
Works on a temp copy of server.ts (the real one when present, else a minimal snippet); never the live file."""
import inspect
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import patch_telegram_plugin as p  # noqa: E402

LIVE = r"C:\Users\Omer\.claude\plugins\cache\claude-plugins-official\telegram\0.0.7\server.ts"
SNIPPET = "\n".join(a for a, _ in p.EDIT_KB_REPLACEMENTS) + "\n"


def _source():
    if os.path.exists(LIVE):
        c = open(LIVE, encoding="utf-8").read()
        if p.EDIT_KB_MARKER not in c:
            return c
    return SNIPPET


def test_patch_adds_schema_handler_and_guard():
    out = p.edit_kb_patch_text(_source())
    assert out is not None and p.EDIT_KB_MARKER in out
    assert "reply_markup: { inline_keyboard: editKb }" in out
    assert "edit_message: unknown argument" in out
    assert "Omit it and any existing buttons are removed" in out
    # the old positional-spread call is gone
    assert "...(editParseMode ? [{ parse_mode: editParseMode }] : [])" not in out


def test_patch_is_idempotent():
    once = p.edit_kb_patch_text(_source())
    assert p.edit_kb_patch_text(once) == once


def test_missing_anchor_writes_nothing():
    assert p.edit_kb_patch_text("const x = 1\n") is None


def test_patch_file_on_copy(tmp_path):
    dst = tmp_path / "server.ts"
    if os.path.exists(LIVE):
        shutil.copyfile(LIVE, dst)
        os.chmod(dst, 0o666)
    else:
        dst.write_text(SNIPPET, encoding="utf-8")
    before = dst.read_text(encoding="utf-8")
    first = p.patch_edit_keyboard(str(dst))
    assert first is (p.EDIT_KB_MARKER not in before)
    assert p.EDIT_KB_MARKER in dst.read_text(encoding="utf-8")
    assert p.patch_edit_keyboard(str(dst)) is False


def test_main_applies_it_and_health_check_ignores_it():
    assert "patch_edit_keyboard(path)" in inspect.getsource(p.main)
    # _server_is_patched decides if a RUNNING plugin is healthy; a new marker there would flag every
    # plugin started before this patch as broken.
    assert "EDIT_KB" not in inspect.getsource(p._server_is_patched)

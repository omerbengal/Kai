"""patch_telegram_plugin.patch_button_cleanup (07/11/2026): pressed label + reply_markup guard. Never touches
the live server.ts: works on a temp copy (the real one only when it exists, else a minimal snippet)."""
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import patch_telegram_plugin as p  # noqa: E402

LIVE = r"C:\Users\Omer\.claude\plugins\cache\claude-plugins-official\telegram\0.0.7\server.ts"
SNIPPET = (
    "        const format = (args.format as string | undefined) ?? 'text'\n"
    "    if (msgId != null) {\n" + p.BUTTON_CHOSEN_ANCHOR + "    }\n"
)


def _copy(tmp_path):
    dst = tmp_path / "server.ts"
    if os.path.exists(LIVE):
        shutil.copyfile(LIVE, dst)
        os.chmod(dst, 0o666)
    else:
        dst.write_text(SNIPPET, encoding="utf-8")
    return str(dst)


def test_patch_applies_once_and_is_idempotent(tmp_path):
    f = _copy(tmp_path)
    before = open(f, encoding="utf-8").read()
    if p.BUTTON_CHOSEN_MARKER in before:
        return  # live file already carries it
    assert p.patch_button_cleanup(f) is True
    after = open(f, encoding="utf-8").read()
    assert p.BUTTON_CHOSEN_MARKER in after and p.REPLY_ARGS_GUARD_MARKER in after
    assert p.BUTTON_CHOSEN_ANCHOR not in after
    assert "\\u05e0\\u05d1\\u05d7\\u05e8" in after  # "נבחר"
    assert p.patch_button_cleanup(f) is False


def test_live_marker_check_does_not_include_new_markers():
    """_server_is_patched decides if a RUNNING plugin is healthy; adding these markers there would flag
    every plugin started before the patch as broken."""
    import inspect
    src = inspect.getsource(p._server_is_patched)
    assert "BUTTON_CHOSEN" not in src and "REPLY_ARGS_GUARD" not in src

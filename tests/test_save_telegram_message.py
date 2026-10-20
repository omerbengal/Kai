"""Tests for the reply-tool save hook id parsing (multi-part replies, 20/10/2026)."""
import sys

sys.path.insert(0, 'D:/Kai/scripts')
import save_telegram_message as s  # noqa: E402


def test_single_id():
    assert s.outgoing_rows('hi', [], '{"content": "sent (id: 5)"}') == [(5, 'hi')]


def test_text_plus_photo():
    rows = s.outgoing_rows('layout done', ['C:/x/live_390.png'], 'sent 2 parts (ids: 17528, 17529)')
    assert rows == [(17528, 'layout done'), (17529, '[קובץ: live_390.png] (צורף להודעה 17528)')]


def test_long_text_chunks():
    rows = s.outgoing_rows('long', [], 'sent 2 parts (ids: 10, 11)')
    assert rows[0] == (10, 'long') and rows[1][0] == 11 and 'חלק 2' in rows[1][1]


def test_files_only():
    rows = s.outgoing_rows('', ['a.png', 'b.pdf'], 'sent 2 parts (ids: 3, 4)')
    assert rows == [(3, '[קובץ: a.png]'), (4, '[קובץ: b.pdf] (צורף להודעה 3)')]


def test_unparseable_keeps_text():
    assert s.outgoing_rows('x', [], 'error') == [(None, 'x')]

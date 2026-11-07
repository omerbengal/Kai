"""scripts/allow_bot_handoffs.py - only the bots group's allowFrom changes (07/11/2026)."""
import sys

sys.path.insert(0, "D:/Kai/scripts")
import allow_bot_handoffs as abh  # noqa: E402


def test_add_and_revert_touch_only_allowfrom():
    acc = {"dmPolicy": "allowlist", "allowFrom": ["1"],
           "groups": {"-9": {"requireMention": False, "allowFrom": ["1"], "topics": ["general"]}}}
    ids = abh.bot_ids({"a": {"bot_id": 5}, "b": {"bot_id": 6, "enabled": False}, "c": {}})
    assert ids == ["5"]
    abh.change(acc, "-9", ids)
    assert acc["groups"]["-9"] == {"requireMention": False, "allowFrom": ["1", "5"], "topics": ["general"]}
    assert acc["allowFrom"] == ["1"]
    abh.change(acc, "-9", ids)                 # idempotent
    assert acc["groups"]["-9"]["allowFrom"] == ["1", "5"]
    abh.change(acc, "-9", ids, revert=True)
    assert acc["groups"]["-9"]["allowFrom"] == ["1"]

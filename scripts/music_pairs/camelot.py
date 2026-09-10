"""Camelot wheel utilities — pure functions, no I/O."""
import re

_CAMELOT_RE = re.compile(r"^([1-9]|1[0-2])([AB])$")


def is_valid_camelot(key) -> bool:
    if not isinstance(key, str):
        return False
    return bool(_CAMELOT_RE.match(key))


def _parse(key: str):
    m = _CAMELOT_RE.match(key)
    if not m:
        raise ValueError(f"invalid Camelot key: {key!r}")
    return int(m.group(1)), m.group(2)


def camelot_distance(a: str, b: str) -> int:
    """Shortest 'mix distance' on the Camelot wheel.

    Rules:
      - same key → 0
      - same number, different letter (relative major/minor) → 1
      - ±1 on the wheel, same letter → 1
      - otherwise → min wheel hop count (with letter swap penalty 1 if letters differ)
    """
    num_a, let_a = _parse(a)
    num_b, let_b = _parse(b)
    hop = min((num_a - num_b) % 12, (num_b - num_a) % 12)
    if let_a == let_b:
        return hop
    # different letter: same number = relative = distance 1
    if num_a == num_b:
        return 1
    # otherwise: hop + 1 (letter swap)
    return hop + 1

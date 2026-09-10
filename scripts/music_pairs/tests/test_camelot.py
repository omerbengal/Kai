import pytest
from scripts.music_pairs.camelot import camelot_distance, is_valid_camelot


def test_same_key_distance_zero():
    assert camelot_distance("8A", "8A") == 0


def test_adjacent_number_same_letter_distance_one():
    assert camelot_distance("8A", "9A") == 1
    assert camelot_distance("8A", "7A") == 1


def test_relative_major_minor_distance_one():
    # same number, different letter = relative major/minor = perfect mix
    assert camelot_distance("8A", "8B") == 1


def test_wheel_wraps_12_to_1():
    assert camelot_distance("12A", "1A") == 1
    assert camelot_distance("1A", "12A") == 1


def test_two_apart():
    assert camelot_distance("8A", "10A") == 2


def test_far_apart_returns_large():
    # 8A vs 2A: min(6, 6) = 6
    assert camelot_distance("8A", "2A") == 6


def test_is_valid_camelot():
    assert is_valid_camelot("1A")
    assert is_valid_camelot("12B")
    assert not is_valid_camelot("13A")
    assert not is_valid_camelot("0A")
    assert not is_valid_camelot("8C")
    assert not is_valid_camelot("")
    assert not is_valid_camelot(None)

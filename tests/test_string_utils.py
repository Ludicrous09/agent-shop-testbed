"""Tests for string utility functions."""
from src.string_utils import is_palindrome, reverse, truncate, word_count


# Tests for reverse
def test_reverse_basic():
    assert reverse("hello") == "olleh"
    assert reverse("world") == "dlrow"


def test_reverse_empty():
    assert reverse("") == ""


def test_reverse_single_char():
    assert reverse("a") == "a"


def test_reverse_palindrome():
    assert reverse("racecar") == "racecar"


def test_reverse_with_spaces():
    assert reverse("hello world") == "dlrow olleh"


def test_reverse_numbers():
    assert reverse("12345") == "54321"


# Tests for is_palindrome
def test_is_palindrome_simple():
    assert is_palindrome("racecar") is True
    assert is_palindrome("hello") is False


def test_is_palindrome_case_insensitive():
    assert is_palindrome("Racecar") is True
    assert is_palindrome("RaceCar") is True
    assert is_palindrome("RACECAR") is True


def test_is_palindrome_ignores_spaces():
    assert is_palindrome("race car") is True
    assert is_palindrome("a man a plan a canal panama") is True


def test_is_palindrome_empty():
    assert is_palindrome("") is True


def test_is_palindrome_single_char():
    assert is_palindrome("a") is True
    assert is_palindrome("Z") is True


def test_is_palindrome_not_palindrome():
    assert is_palindrome("python") is False
    assert is_palindrome("hello world") is False


# Tests for word_count
def test_word_count_basic():
    assert word_count("hello world") == 2
    assert word_count("one two three") == 3


def test_word_count_empty():
    assert word_count("") == 0


def test_word_count_single_word():
    assert word_count("hello") == 1


def test_word_count_extra_spaces():
    assert word_count("  hello   world  ") == 2


def test_word_count_multiple_spaces():
    assert word_count("a  b  c") == 3


def test_word_count_newlines():
    assert word_count("hello\nworld") == 2


# Tests for truncate
def test_truncate_no_truncation_needed():
    assert truncate("hello", 10) == "hello"
    assert truncate("hello", 5) == "hello"


def test_truncate_basic():
    assert truncate("hello world", 8) == "hello..."


def test_truncate_custom_suffix():
    assert truncate("hello world", 8, suffix="--") == "hello --"


def test_truncate_empty_suffix():
    assert truncate("hello world", 5, suffix="") == "hello"


def test_truncate_empty_string():
    assert truncate("", 5) == ""


def test_truncate_exact_length():
    assert truncate("hello", 5) == "hello"


def test_truncate_suffix_longer_than_max():
    assert truncate("hello world", 3) == "..."


def test_truncate_default_suffix():
    result = truncate("a very long string indeed", 10)
    assert result == "a very ..."
    assert len(result) == 10

"""String utility functions."""


def reverse(s: str) -> str:
    """Reverse a string."""
    return s[::-1]


def is_palindrome(s: str) -> bool:
    """Check if a string is a palindrome (case-insensitive, ignoring spaces)."""
    cleaned = s.replace(" ", "").lower()
    return cleaned == cleaned[::-1]


def word_count(s: str) -> int:
    """Count words in a string."""
    return len(s.split())


def truncate(s: str, max_length: int, suffix: str = "...") -> str:
    """Truncate string to max_length, adding suffix if truncated."""
    if len(s) <= max_length:
        return s
    if max_length <= len(suffix):
        return suffix[:max_length]
    return s[: max_length - len(suffix)] + suffix

"""String utility functions."""
import re


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


def parse_config(text: str) -> dict[str, str]:
    """Parse key=value configuration text into a dict, ignoring '#' comments."""
    config: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        config[key.strip()] = value.strip()
    return config


def slugify(s: str) -> str:
    """Convert a string into a lowercase, URL-safe slug."""
    separated = re.sub(r"[\s_]+", "-", s.lower())
    cleaned = re.sub(r"[^a-z0-9-]", "", separated)
    return re.sub(r"-+", "-", cleaned).strip("-")


_TITLE_CASE_LOWERCASE_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or",
    "the", "to",
}


def title_case(s: str) -> str:
    """Capitalise each word, keeping short joining words lowercase unless first."""
    words = s.split()
    result = []
    for i, word in enumerate(words):
        if i > 0 and word.lower() in _TITLE_CASE_LOWERCASE_WORDS:
            result.append(word.lower())
        else:
            result.append(word[:1].upper() + word[1:].lower())
    return " ".join(result)

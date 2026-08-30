"""Utility functions for the testbed project."""


def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def multiply(a: int, b: int) -> int:
    """Multiply two numbers."""
    return a * b


def subtract(a: int, b: int) -> int:
    """Subtract two numbers."""
    return a - b


def divide(a: int, b: int) -> float:
    """Divide two numbers."""
    if b == 0:
        raise ZeroDivisionError("division by zero")
    return a / b


def power(base: int, exponent: int) -> int:
    """Raise base to the power of exponent."""
    return base**exponent


def clamp(value: float, low: float, high: float) -> float:
    """Clamp value to the inclusive range [low, high]."""
    if low > high:
        raise ValueError(f"low ({low}) must not be greater than high ({high})")
    if value < low:
        return low
    if value > high:
        return high
    return value


def read_lines(path: str) -> list[str]:
    """Read a text file and return its lines with trailing newlines stripped."""
    with open(path) as f:
        return [line.rstrip("\n") for line in f]


def write_lines(path: str, lines: list[str]) -> None:
    """Write lines to a text file, one per line."""
    with open(path, "w") as f:
        for line in lines:
            f.write(line + "\n")

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


def divide(a: float, b: float) -> float:
    """Divide two numbers."""
    if b == 0:
        raise ZeroDivisionError("division by zero")
    return a / b


def power(a: float, b: float) -> float:
    """Raise a to the power of b."""
    return a**b

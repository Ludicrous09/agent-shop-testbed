"""Tests for utility functions."""
from src.utils import add, multiply, subtract


def test_add():
    assert add(1, 2) == 3
    assert add(-1, 1) == 0
    assert add(0, 0) == 0


def test_multiply_positive_numbers():
    assert multiply(2, 3) == 6
    assert multiply(4, 5) == 20


def test_multiply_negative_numbers():
    assert multiply(-2, -3) == 6
    assert multiply(-4, 5) == -20


def test_multiply_zero():
    assert multiply(0, 5) == 0
    assert multiply(7, 0) == 0
    assert multiply(0, 0) == 0


def test_multiply_by_one():
    assert multiply(1, 5) == 5
    assert multiply(7, 1) == 7


def test_subtract():
    assert subtract(5, 3) == 2
    assert subtract(0, 0) == 0
    assert subtract(-1, -1) == 0


def test_subtract_negative_result():
    assert subtract(3, 5) == -2
    assert subtract(0, 5) == -5


def test_subtract_with_negatives():
    assert subtract(-1, -3) == 2
    assert subtract(-5, 3) == -8

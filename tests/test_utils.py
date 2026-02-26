"""Tests for utility functions."""
from src.utils import add, multiply


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

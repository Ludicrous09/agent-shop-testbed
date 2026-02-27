"""Tests for utility functions."""
import pytest

from src.utils import add, divide, multiply, power, subtract


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


def test_divide_positive_numbers():
    assert divide(10, 2) == 5.0
    assert divide(9, 3) == 3.0


def test_divide_negative_numbers():
    assert divide(-10, -2) == 5.0
    assert divide(-9, -3) == 3.0


def test_divide_mixed_signs():
    assert divide(-10, 2) == -5.0
    assert divide(10, -2) == -5.0


def test_divide_returns_float():
    assert divide(7, 2) == 3.5
    assert divide(1, 4) == 0.25
    assert isinstance(divide(4, 2), float)


def test_divide_by_zero():
    with pytest.raises(ZeroDivisionError):
        divide(5, 0)
    with pytest.raises(ZeroDivisionError):
        divide(0, 0)
    with pytest.raises(ZeroDivisionError):
        divide(-5, 0)


def test_power_positive_exponents():
    assert power(2, 3) == 8
    assert power(3, 4) == 81
    assert power(5, 2) == 25
    assert power(10, 6) == 1000000


def test_power_exponent_zero():
    assert power(2, 0) == 1
    assert power(0, 0) == 1
    assert power(-5, 0) == 1
    assert power(100, 0) == 1


def test_power_negative_base_even_exponent():
    assert power(-2, 2) == 4
    assert power(-3, 4) == 81
    assert power(-5, 2) == 25


def test_power_negative_base_odd_exponent():
    assert power(-2, 3) == -8
    assert power(-3, 1) == -3
    assert power(-5, 3) == -125

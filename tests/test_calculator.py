"""Tests for the Calculator class."""

import pytest

from src.calculator import Calculator


def test_add_returns_correct_result():
    calc = Calculator()
    assert calc.add(2, 3) == 5
    assert calc.add(-1, 1) == 0
    assert calc.add(0, 0) == 0


def test_subtract_returns_correct_result():
    calc = Calculator()
    assert calc.subtract(5, 3) == 2
    assert calc.subtract(0, 0) == 0
    assert calc.subtract(3, 5) == -2


def test_multiply_returns_correct_result():
    calc = Calculator()
    assert calc.multiply(2, 3) == 6
    assert calc.multiply(-2, 3) == -6
    assert calc.multiply(0, 5) == 0


def test_divide_returns_correct_result():
    calc = Calculator()
    assert calc.divide(10, 2) == 5.0
    assert calc.divide(7, 2) == 3.5
    assert calc.divide(-9, 3) == -3.0


def test_power_returns_correct_result():
    calc = Calculator()
    assert calc.power(2, 3) == 8.0
    assert calc.power(5, 0) == 1.0
    assert calc.power(4, 0.5) == 2.0


def test_divide_by_zero_raises():
    calc = Calculator()
    with pytest.raises(ZeroDivisionError):
        calc.divide(10, 0)


def test_history_records_add():
    calc = Calculator()
    calc.add(2, 3)
    assert calc.history == [{"operation": "add", "args": (2, 3), "result": 5}]


def test_history_records_subtract():
    calc = Calculator()
    calc.subtract(5, 3)
    assert calc.history == [{"operation": "subtract", "args": (5, 3), "result": 2}]


def test_history_records_multiply():
    calc = Calculator()
    calc.multiply(4, 5)
    assert calc.history == [{"operation": "multiply", "args": (4, 5), "result": 20}]


def test_history_records_divide():
    calc = Calculator()
    calc.divide(10, 2)
    assert calc.history == [{"operation": "divide", "args": (10, 2), "result": 5.0}]


def test_history_records_power():
    calc = Calculator()
    calc.power(2, 3)
    assert calc.history == [{"operation": "power", "args": (2, 3), "result": 8.0}]


def test_history_records_multiple_operations():
    calc = Calculator()
    calc.add(1, 2)
    calc.multiply(3, 4)
    calc.subtract(10, 5)
    assert len(calc.history) == 3
    assert calc.history[0] == {"operation": "add", "args": (1, 2), "result": 3}
    assert calc.history[1] == {"operation": "multiply", "args": (3, 4), "result": 12}
    assert calc.history[2] == {"operation": "subtract", "args": (10, 5), "result": 5}


def test_history_empty_on_init():
    calc = Calculator()
    assert calc.history == []


def test_divide_zero_does_not_record_history():
    calc = Calculator()
    with pytest.raises(ZeroDivisionError):
        calc.divide(10, 0)
    assert calc.history == []

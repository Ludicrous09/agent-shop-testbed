"""Tests for the Calculator class."""
import pytest

from src.calculator import Calculator


@pytest.fixture
def calc():
    return Calculator()


def test_add(calc):
    assert calc.add(2, 3) == 5
    assert calc.add(-1, 1) == 0
    assert calc.add(0, 0) == 0


def test_subtract(calc):
    assert calc.subtract(5, 3) == 2
    assert calc.subtract(0, 0) == 0
    assert calc.subtract(3, 5) == -2


def test_multiply(calc):
    assert calc.multiply(2, 3) == 6
    assert calc.multiply(-2, 3) == -6
    assert calc.multiply(0, 5) == 0


def test_divide(calc):
    assert calc.divide(10, 2) == 5.0
    assert calc.divide(7, 2) == 3.5
    assert calc.divide(-10, 2) == -5.0


def test_power(calc):
    assert calc.power(2, 3) == 8
    assert calc.power(5, 0) == 1
    assert calc.power(-2, 3) == -8


def test_divide_by_zero_raises(calc):
    with pytest.raises(ZeroDivisionError):
        calc.divide(5, 0)


def test_divide_by_zero_not_recorded_in_history(calc):
    with pytest.raises(ZeroDivisionError):
        calc.divide(5, 0)
    assert len(calc.history) == 0


def test_history_records_operations(calc):
    calc.add(1, 2)
    calc.subtract(5, 3)
    calc.multiply(4, 5)
    calc.divide(10, 2)
    calc.power(2, 8)

    assert len(calc.history) == 5
    assert calc.history[0] == {"operation": "add", "args": (1, 2), "result": 3}
    assert calc.history[1] == {"operation": "subtract", "args": (5, 3), "result": 2}
    assert calc.history[2] == {"operation": "multiply", "args": (4, 5), "result": 20}
    assert calc.history[3] == {"operation": "divide", "args": (10, 2), "result": 5.0}
    assert calc.history[4] == {"operation": "power", "args": (2, 8), "result": 256}


def test_history_starts_empty(calc):
    assert calc.history == []


def test_history_accumulates(calc):
    calc.add(1, 1)
    assert len(calc.history) == 1
    calc.add(2, 2)
    assert len(calc.history) == 2

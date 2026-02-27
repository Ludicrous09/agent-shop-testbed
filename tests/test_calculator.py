"""Tests for Calculator class."""
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


def test_divide_by_zero(calc):
    with pytest.raises(ZeroDivisionError):
        calc.divide(5, 0)


def test_power(calc):
    assert calc.power(2, 3) == 8
    assert calc.power(5, 0) == 1
    assert calc.power(3, 2) == 9


def test_history_records_operation(calc):
    calc.add(1, 2)
    assert len(calc.history) == 1
    assert calc.history[0] == {"operation": "add", "args": (1, 2), "result": 3}


def test_history_accumulates(calc):
    calc.add(1, 2)
    calc.subtract(5, 3)
    calc.multiply(4, 5)
    assert len(calc.history) == 3
    assert calc.history[0]["operation"] == "add"
    assert calc.history[1]["operation"] == "subtract"
    assert calc.history[2]["operation"] == "multiply"


def test_history_records_all_operations(calc):
    calc.add(1, 2)
    calc.subtract(5, 3)
    calc.multiply(4, 5)
    calc.divide(10, 2)
    calc.power(2, 3)

    ops = [entry["operation"] for entry in calc.history]
    assert ops == ["add", "subtract", "multiply", "divide", "power"]

    assert calc.history[0] == {"operation": "add", "args": (1, 2), "result": 3}
    assert calc.history[1] == {"operation": "subtract", "args": (5, 3), "result": 2}
    assert calc.history[2] == {"operation": "multiply", "args": (4, 5), "result": 20}
    assert calc.history[3] == {"operation": "divide", "args": (10, 2), "result": 5.0}
    assert calc.history[4] == {"operation": "power", "args": (2, 3), "result": 8}


def test_history_not_recorded_on_zero_division(calc):
    with pytest.raises(ZeroDivisionError):
        calc.divide(5, 0)
    assert len(calc.history) == 0

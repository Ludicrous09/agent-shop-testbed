"""Calculator class wrapping utility functions."""

from src.utils import add, divide, multiply, power, subtract


class Calculator:
    """A calculator that delegates to utility functions and tracks history."""

    def __init__(self):
        self.history = []

    def add(self, a, b):
        result = add(a, b)
        self.history.append({"operation": "add", "args": (a, b), "result": result})
        return result

    def subtract(self, a, b):
        result = subtract(a, b)
        self.history.append({"operation": "subtract", "args": (a, b), "result": result})
        return result

    def multiply(self, a, b):
        result = multiply(a, b)
        self.history.append({"operation": "multiply", "args": (a, b), "result": result})
        return result

    def divide(self, a, b):
        result = divide(a, b)
        self.history.append({"operation": "divide", "args": (a, b), "result": result})
        return result

    def power(self, a, b):
        result = power(a, b)
        self.history.append({"operation": "power", "args": (a, b), "result": result})
        return result

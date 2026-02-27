"""Conversion utility functions."""


def celsius_to_fahrenheit(c: float) -> float:
    return c * 9 / 5 + 32


def fahrenheit_to_celsius(f: float) -> float:
    return (f - 32) * 5 / 9


def km_to_miles(km: float) -> float:
    return km * 0.621371


def miles_to_km(miles: float) -> float:
    return miles / 0.621371


def kg_to_lbs(kg: float) -> float:
    return kg * 2.204623


def lbs_to_kg(lbs: float) -> float:
    return lbs / 2.204623

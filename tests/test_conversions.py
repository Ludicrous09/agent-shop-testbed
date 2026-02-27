"""Tests for the conversions module."""
import math

from src.conversions import (
    celsius_to_fahrenheit,
    fahrenheit_to_celsius,
    kg_to_lbs,
    km_to_miles,
    lbs_to_kg,
    miles_to_km,
)


# --- celsius_to_fahrenheit ---

def test_celsius_to_fahrenheit_freezing():
    assert celsius_to_fahrenheit(0) == 32.0


def test_celsius_to_fahrenheit_boiling():
    assert celsius_to_fahrenheit(100) == 212.0


def test_celsius_to_fahrenheit_body_temp():
    assert math.isclose(celsius_to_fahrenheit(37), 98.6, rel_tol=1e-5)


def test_celsius_to_fahrenheit_negative():
    assert celsius_to_fahrenheit(-40) == -40.0


# --- fahrenheit_to_celsius ---

def test_fahrenheit_to_celsius_freezing():
    assert fahrenheit_to_celsius(32) == 0.0


def test_fahrenheit_to_celsius_boiling():
    assert fahrenheit_to_celsius(212) == 100.0


def test_fahrenheit_to_celsius_negative():
    assert fahrenheit_to_celsius(-40) == -40.0


def test_fahrenheit_to_celsius_body_temp():
    assert math.isclose(fahrenheit_to_celsius(98.6), 37.0, rel_tol=1e-5)


# --- km_to_miles ---

def test_km_to_miles_basic():
    assert math.isclose(km_to_miles(1), 0.621371, rel_tol=1e-5)


def test_km_to_miles_zero():
    assert km_to_miles(0) == 0.0


def test_km_to_miles_negative():
    assert math.isclose(km_to_miles(-10), -6.21371, rel_tol=1e-5)


# --- miles_to_km ---

def test_miles_to_km_basic():
    assert math.isclose(miles_to_km(1), 1.609344, rel_tol=1e-5)


def test_miles_to_km_zero():
    assert miles_to_km(0) == 0.0


def test_miles_to_km_negative():
    assert math.isclose(miles_to_km(-5), -8.04672, rel_tol=1e-5)


def test_km_miles_roundtrip():
    assert math.isclose(miles_to_km(km_to_miles(100)), 100.0, rel_tol=1e-9)


# --- kg_to_lbs ---

def test_kg_to_lbs_basic():
    assert math.isclose(kg_to_lbs(1), 2.204623, rel_tol=1e-5)


def test_kg_to_lbs_zero():
    assert kg_to_lbs(0) == 0.0


def test_kg_to_lbs_negative():
    assert math.isclose(kg_to_lbs(-10), -22.04623, rel_tol=1e-5)


# --- lbs_to_kg ---

def test_lbs_to_kg_basic():
    assert math.isclose(lbs_to_kg(1), 0.453592, rel_tol=1e-5)


def test_lbs_to_kg_zero():
    assert lbs_to_kg(0) == 0.0


def test_lbs_to_kg_negative():
    assert math.isclose(lbs_to_kg(-10), -4.535924, rel_tol=1e-5)


def test_kg_lbs_roundtrip():
    assert math.isclose(lbs_to_kg(kg_to_lbs(75)), 75.0, rel_tol=1e-9)

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


def mps_to_kph(mps: float) -> float:
    return mps * 3.6


def kph_to_mps(kph: float) -> float:
    return kph / 3.6


def bytes_to_mb(b: float) -> float:
    """Convert bytes to megabytes using the binary convention (1 MB = 1024 * 1024 bytes),
    since the decimal convention (1 MB = 1,000,000 bytes) is equally common and the
    function name alone can't tell you which one is meant."""
    return b / (1024 * 1024)


def mb_to_bytes(mb: float) -> float:
    """Convert megabytes to bytes using the binary convention (1 MB = 1024 * 1024 bytes),
    since the decimal convention (1 MB = 1,000,000 bytes) is equally common and the
    function name alone can't tell you which one is meant."""
    return mb * 1024 * 1024

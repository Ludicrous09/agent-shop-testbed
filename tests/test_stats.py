"""Tests for the statistics module."""
import math

import pytest

from src.stats import mean, median, mode, std_dev


# --- mean ---

def test_mean_basic():
    assert mean([1, 2, 3, 4, 5]) == 3.0


def test_mean_single_element():
    assert mean([42.0]) == 42.0


def test_mean_floats():
    assert math.isclose(mean([1.5, 2.5, 3.0]), 7.0 / 3)


def test_mean_negative_numbers():
    assert mean([-1, -2, -3]) == -2.0


def test_mean_mixed_sign():
    assert mean([-5, 5]) == 0.0


def test_mean_empty_raises():
    with pytest.raises(ValueError):
        mean([])


# --- median ---

def test_median_odd_length():
    assert median([3, 1, 2]) == 2.0


def test_median_even_length():
    assert median([1, 2, 3, 4]) == 2.5


def test_median_single_element():
    assert median([7]) == 7.0


def test_median_already_sorted():
    assert median([10, 20, 30]) == 20.0


def test_median_unsorted():
    assert median([5, 1, 3]) == 3.0


def test_median_floats_even():
    assert math.isclose(median([1.0, 2.0, 3.0, 4.0]), 2.5)


def test_median_negative_numbers():
    assert median([-3, -1, -2]) == -2.0


def test_median_empty_raises():
    with pytest.raises(ValueError):
        median([])


# --- mode ---

def test_mode_basic():
    assert mode([1, 2, 2, 3]) == 2


def test_mode_single_element():
    assert mode([5]) == 5


def test_mode_all_same():
    assert mode([4, 4, 4]) == 4


def test_mode_first_position():
    assert mode([7, 7, 1, 2]) == 7


def test_mode_last_position():
    assert mode([1, 2, 3, 3]) == 3


def test_mode_no_unique_raises():
    with pytest.raises(ValueError):
        mode([1, 1, 2, 2])


def test_mode_all_unique_raises():
    with pytest.raises(ValueError):
        mode([1, 2, 3])


def test_mode_empty_raises():
    with pytest.raises(ValueError):
        mode([])


# --- std_dev ---

def test_std_dev_basic():
    # population std dev of [2, 4, 4, 4, 5, 5, 7, 9] == 2.0
    assert math.isclose(std_dev([2, 4, 4, 4, 5, 5, 7, 9]), 2.0)


def test_std_dev_single_element():
    assert std_dev([10.0]) == 0.0


def test_std_dev_identical_elements():
    assert std_dev([3, 3, 3, 3]) == 0.0


def test_std_dev_two_elements():
    # mean=1.5, variance=0.25, std=0.5
    assert math.isclose(std_dev([1.0, 2.0]), 0.5)


def test_std_dev_negative_numbers():
    assert math.isclose(std_dev([-2, -4, -4, -4, -5, -5, -7, -9]), 2.0)


def test_std_dev_empty_raises():
    with pytest.raises(ValueError):
        std_dev([])

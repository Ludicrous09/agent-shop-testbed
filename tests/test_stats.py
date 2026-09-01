"""Tests for the statistics module."""
import math

import pytest

from src.stats import (
    coefficient_of_variation,
    geometric_mean,
    harmonic_mean,
    mean,
    median,
    median_absolute_deviation,
    mode,
    moving_average,
    percentile,
    std_dev,
    variance,
)


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


# --- moving_average ---

def test_moving_average_basic():
    assert moving_average([1, 2, 3, 4], 2) == [1.5, 2.5, 3.5]


def test_moving_average_window_equals_length():
    assert moving_average([1, 2, 3, 4], 4) == [2.5]


def test_moving_average_window_one():
    assert moving_average([1, 2, 3], 1) == [1.0, 2.0, 3.0]


def test_moving_average_zero_window_raises():
    with pytest.raises(ValueError):
        moving_average([1, 2, 3], 0)


def test_moving_average_negative_window_raises():
    with pytest.raises(ValueError):
        moving_average([1, 2, 3], -1)


def test_moving_average_window_exceeds_length_raises():
    with pytest.raises(ValueError):
        moving_average([1, 2, 3], 4)


# --- percentile ---

def test_percentile_median():
    assert percentile([1, 2, 3, 4], 50) == 2.5


def test_percentile_zero():
    assert percentile([1, 2, 3, 4], 0) == 1


def test_percentile_hundred():
    assert percentile([1, 2, 3, 4], 100) == 4


def test_percentile_single_element():
    assert percentile([7], 50) == 7


def test_percentile_unsorted_input():
    assert percentile([4, 1, 3, 2], 50) == 2.5


def test_percentile_interpolation():
    assert math.isclose(percentile([1, 2, 3, 4, 5], 25), 2.0)


def test_percentile_does_not_mutate_input():
    values = [4, 1, 3, 2]
    percentile(values, 50)
    assert values == [4, 1, 3, 2]


def test_percentile_empty_raises():
    with pytest.raises(ValueError):
        percentile([], 50)


def test_percentile_below_range_raises():
    with pytest.raises(ValueError):
        percentile([1, 2, 3], -1)


def test_percentile_above_range_raises():
    with pytest.raises(ValueError):
        percentile([1, 2, 3], 101)


# --- variance ---

def test_variance_population_basic():
    assert variance([2, 4, 4, 4, 5, 5, 7, 9]) == 4.0


def test_variance_sample_basic():
    assert math.isclose(variance([2, 4, 4, 4, 5, 5, 7, 9], sample=True), 4.571, abs_tol=1e-3)


def test_variance_identical_elements():
    assert variance([3, 3, 3, 3]) == 0.0


def test_variance_single_element():
    assert variance([10.0]) == 0.0


def test_variance_empty_raises():
    with pytest.raises(ValueError):
        variance([])


def test_variance_sample_insufficient_values_raises():
    with pytest.raises(ValueError):
        variance([1.0], sample=True)


# --- geometric_mean ---

def test_geometric_mean_basic():
    assert math.isclose(geometric_mean([1, 3, 9, 27]), 5.196, abs_tol=1e-3)


def test_geometric_mean_identical_elements():
    assert geometric_mean([4, 4]) == 4.0


def test_geometric_mean_single_element():
    assert geometric_mean([9.0]) == 9.0


def test_geometric_mean_empty_raises():
    with pytest.raises(ValueError):
        geometric_mean([])


def test_geometric_mean_zero_raises():
    with pytest.raises(ValueError):
        geometric_mean([1, 0, 3])


def test_geometric_mean_negative_raises():
    with pytest.raises(ValueError):
        geometric_mean([1, -2, 3])


# --- harmonic_mean ---

def test_harmonic_mean_basic():
    assert math.isclose(harmonic_mean([1, 2, 4]), 1.714, abs_tol=1e-3)


def test_harmonic_mean_identical_elements():
    assert harmonic_mean([4, 4]) == 4.0


def test_harmonic_mean_single_element():
    assert harmonic_mean([9.0]) == 9.0


def test_harmonic_mean_empty_raises():
    with pytest.raises(ValueError):
        harmonic_mean([])


def test_harmonic_mean_zero_raises():
    with pytest.raises(ValueError):
        harmonic_mean([1, 0, 3])


def test_harmonic_mean_negative_raises():
    with pytest.raises(ValueError):
        harmonic_mean([1, -2, 3])


# --- coefficient_of_variation ---

def test_coefficient_of_variation_basic():
    assert math.isclose(coefficient_of_variation([2, 4, 4, 4, 5, 5, 7, 9]), 0.4, abs_tol=1e-3)


def test_coefficient_of_variation_identical_elements():
    assert coefficient_of_variation([3, 3, 3, 3]) == 0.0


def test_coefficient_of_variation_single_element():
    assert coefficient_of_variation([10.0]) == 0.0


def test_coefficient_of_variation_empty_raises():
    with pytest.raises(ValueError):
        coefficient_of_variation([])


def test_coefficient_of_variation_zero_mean_raises():
    with pytest.raises(ValueError):
        coefficient_of_variation([-5, 5])


# --- median_absolute_deviation ---


def test_median_absolute_deviation_basic():
    assert median_absolute_deviation([1, 2, 3, 4, 5]) == 1.0


def test_median_absolute_deviation_identical_elements():
    assert median_absolute_deviation([3, 3, 3, 3]) == 0.0


def test_median_absolute_deviation_single_element():
    assert median_absolute_deviation([10.0]) == 0.0


def test_median_absolute_deviation_does_not_mutate_input():
    values = [5, 1, 4, 2, 3]
    median_absolute_deviation(values)
    assert values == [5, 1, 4, 2, 3]


def test_median_absolute_deviation_empty_raises():
    with pytest.raises(ValueError):
        median_absolute_deviation([])

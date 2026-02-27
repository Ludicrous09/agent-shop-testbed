"""Statistics functions for numerical data."""


def mean(numbers: list[float]) -> float:
    """Calculate the arithmetic mean of a list of numbers."""
    if not numbers:
        raise ValueError("Cannot calculate mean of an empty list")
    return sum(numbers) / len(numbers)


def median(numbers: list[float]) -> float:
    """Calculate the median of a list of numbers."""
    if not numbers:
        raise ValueError("Cannot calculate median of an empty list")
    sorted_numbers = sorted(numbers)
    n = len(sorted_numbers)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_numbers[mid - 1] + sorted_numbers[mid]) / 2
    return sorted_numbers[mid]


def mode(numbers: list[float]) -> float:
    """Return the most common value in a list of numbers.

    Raises ValueError if there is no unique mode (tie between multiple values)
    or if the input list is empty.
    """
    if not numbers:
        raise ValueError("Cannot calculate mode of an empty list")
    counts: dict[float, int] = {}
    for n in numbers:
        counts[n] = counts.get(n, 0) + 1
    max_count = max(counts.values())
    modes = [k for k, v in counts.items() if v == max_count]
    if len(modes) > 1:
        raise ValueError("No unique mode: multiple values share the highest frequency")
    return modes[0]


def std_dev(numbers: list[float]) -> float:
    """Calculate the population standard deviation of a list of numbers."""
    if not numbers:
        raise ValueError("Cannot calculate standard deviation of an empty list")
    m = mean(numbers)
    variance = sum((x - m) ** 2 for x in numbers) / len(numbers)
    return variance ** 0.5

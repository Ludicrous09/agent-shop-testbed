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


def variance(values: list[float], sample: bool = False) -> float:
    """Calculate the variance of a list of numbers.

    Returns the population variance by default, or the sample variance
    (Bessel's correction) when sample=True.
    """
    if not values:
        raise ValueError("Cannot calculate variance of an empty list")
    if sample and len(values) < 2:
        raise ValueError("Sample variance requires at least 2 values")
    m = mean(values)
    total = sum((x - m) ** 2 for x in values)
    denominator = len(values) - 1 if sample else len(values)
    return total / denominator


def std_dev(numbers: list[float]) -> float:
    """Calculate the population standard deviation of a list of numbers."""
    if not numbers:
        raise ValueError("Cannot calculate standard deviation of an empty list")
    m = mean(numbers)
    variance = sum((x - m) ** 2 for x in numbers) / len(numbers)
    return variance ** 0.5


def percentile(values: list[float], p: float) -> float:
    """Calculate the p-th percentile (0-100) of a list of numbers.

    Uses linear interpolation between the closest ranks.
    """
    if not values:
        raise ValueError("Cannot calculate percentile of an empty list")
    if p < 0 or p > 100:
        raise ValueError("Percentile must be between 0 and 100")
    sorted_values = sorted(values)
    n = len(sorted_values)
    rank = (p / 100) * (n - 1)
    lower = int(rank)
    upper = min(lower + 1, n - 1)
    fraction = rank - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction


def moving_average(values: list[float], window: int) -> list[float]:
    """Calculate the simple moving average over a sliding window.

    For a list of length n and a window of size w, returns n - w + 1
    entries, each the mean of the corresponding w consecutive values.
    """
    if window <= 0:
        raise ValueError("Window size must be positive")
    if window > len(values):
        raise ValueError("Window size cannot exceed the length of values")
    return [mean(values[i:i + window]) for i in range(len(values) - window + 1)]

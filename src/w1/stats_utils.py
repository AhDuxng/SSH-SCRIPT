import math


def percentile(values, p):
    """Return percentile using linear interpolation."""
    if not values:
        return None
    if len(values) == 1:
        return values[0]

    sorted_values = sorted(values)
    k = (len(sorted_values) - 1) * (p / 100.0)
    lower = math.floor(k)
    upper = math.ceil(k)
    if lower == upper:
        return sorted_values[int(k)]
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (k - lower)

"""Exact and approximate confidence bounds for pass/fail rates, with no dependencies. Pure functions; no I/O.

A bound is reported with every rate in an evaluation report because a small sample cannot prove a small rate:
0 failures in n trials only shows the true rate is below about 3/n at 95% confidence."""
from __future__ import annotations

import math


def _cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def clopper_pearson_upper(failures: int, n: int, confidence: float = .95) -> float:
    """Exact one-sided upper confidence bound: the largest true rate for which `failures` or fewer is still at least (1-confidence) likely."""
    if n <= 0:
        return 1.0
    if failures >= n:
        return 1.0
    low, high = failures / n, 1.0
    for _ in range(45):
        mid = (low + high) / 2
        low, high = (mid, high) if _cdf(failures, n, mid) >= 1 - confidence else (low, mid)
    return low


def wilson_upper(failures: int, n: int, z: float = 1.959964) -> float:
    """Two-sided 95% Wilson upper bound (approximate; slightly looser than the exact bound for small n)."""
    if n <= 0:
        return 1.0
    p = failures / n
    return min(1.0, (p + z * z / (2 * n) + z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n))


def rate(failures: int, n: int) -> dict:
    return {'failures': failures, 'n': n, 'point': round(failures / n, 4) if n else None,
            'upper95_exact': round(clopper_pearson_upper(failures, n), 4), 'upper95_wilson': round(wilson_upper(failures, n), 4)}


def pass_probability(max_failures: int, n: int, true_rate: float) -> float:
    """Chance a gate of 'at most max_failures of n' passes when the true failure rate is `true_rate`."""
    return _cdf(max_failures, n, true_rate)


def trials_needed_for_zero_failures(bound: float, confidence: float = .95) -> int:
    return math.ceil(math.log(1 - confidence) / math.log(1 - bound))

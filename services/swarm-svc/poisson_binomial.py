"""
Poisson Binomial PMF — dynamic-programming convolution (exact, O(n^2)).

The DFT-based approach that was here previously produced bit-reversed PMF
indices, yielding badly wrong P(X>=k) estimates for any non-trivial input.
This DP implementation is the standard textbook algorithm and is numerically
exact for cohort sizes up to ~10,000 donors.
"""
import numpy as np
from typing import List


def poisson_binomial_pmf(probs: List[float]) -> np.ndarray:
    """
    Probability Mass Function of the Poisson Binomial distribution via
    dynamic-programming convolution.

    Args:
        probs: Individual success probabilities for each independent trial.

    Returns:
        np.ndarray of length len(probs)+1 where index k is P(exactly k successes).
    """
    if not probs:
        return np.array([1.0])

    n = len(probs)
    dp = np.zeros(n + 1)
    dp[0] = 1.0

    for p in probs:
        # Traverse backwards so each donor is counted at most once.
        for k in range(n, 0, -1):
            dp[k] = dp[k] * (1 - p) + dp[k - 1] * p
        dp[0] *= (1 - p)

    # Clip tiny floating-point negatives and renormalise.
    dp = np.clip(dp, 0, 1)
    total = dp.sum()
    if total > 0:
        dp /= total
    return dp


def calculate_cohort_size(probs: List[float], target_units: int, confidence: float = 0.95) -> int:
    """
    Given an ordered list of donor probabilities (highest to lowest), find the minimum
    number of donors to contact (cohort size) to achieve at least `confidence` probability
    of getting `target_units` successful donations.

    Returns:
        Minimum cohort size that meets the confidence threshold, or len(probs) if
        the threshold cannot be met with the available donor pool.
    """
    if not probs:
        return 0

    for k in range(target_units, len(probs) + 1):
        subset_probs = probs[:k]
        pmf = poisson_binomial_pmf(subset_probs)
        # Probability of getting AT LEAST target_units
        prob_success = float(pmf[target_units:].sum())
        if prob_success >= confidence:
            return k

    # If we exhaust all and still don't reach confidence, return all available
    return len(probs)

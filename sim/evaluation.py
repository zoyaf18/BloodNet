"""Reproducible five-arm policy experiment for the BloodNet digital twin."""

from __future__ import annotations

from dataclasses import dataclass
import random
from statistics import mean
from typing import Callable, Iterable


ARM_NAMES = ("nearest", "reliability", "broadcast", "random", "bloodnet")
METRICS = (
    "fulfillment_rate",
    "notifications_per_request",
    "donor_response_rate",
    "donor_completion_rate",
    "mean_donor_travel_km",
    "donor_fatigue_index",
)


@dataclass(frozen=True)
class RequestOutcome:
    fulfilled: bool
    notifications: int
    response_rate: float
    completion_rate: float
    travel_km: float
    fatigue: float


@dataclass(frozen=True)
class DonorCandidate:
    """One eligible, compatible donor in a synthetic request pool."""

    distance_km: float
    response_probability: float
    completion_probability: float

    @property
    def success_probability(self) -> float:
        return self.response_probability * self.completion_probability


@dataclass(frozen=True)
class SimulatedRequest:
    donors: tuple[DonorCandidate, ...]
    need: int


Policy = Callable[[list[DonorCandidate], int, random.Random], list[int]]


def _nearest(donors: list[DonorCandidate], need: int, rng: random.Random) -> list[int]:
    return sorted(range(len(donors)), key=lambda index: donors[index].distance_km)[:need]


def _reliability(donors: list[DonorCandidate], need: int, rng: random.Random) -> list[int]:
    return sorted(
        range(len(donors)),
        key=lambda index: donors[index].success_probability,
        reverse=True,
    )[:need]


def _broadcast(donors: list[DonorCandidate], need: int, rng: random.Random) -> list[int]:
    return list(range(len(donors)))


def _random(donors: list[DonorCandidate], need: int, rng: random.Random) -> list[int]:
    indexes = list(range(len(donors)))
    rng.shuffle(indexes)
    return indexes[:min(len(indexes), max(need, 1))]


def _bloodnet(donors: list[DonorCandidate], need: int, rng: random.Random) -> list[int]:
    """Choose the smallest reliability-ranked cohort meeting the target probability."""
    ranked = sorted(
        range(len(donors)),
        key=lambda index: donors[index].success_probability,
        reverse=True,
    )
    target = 0.98 if need > 1 else 0.95
    probability = 0.0
    for cohort_size, index in enumerate(ranked, start=1):
        probability = _probability_of_at_least(ranked[:cohort_size], donors, need)
        if probability >= target:
            return ranked[:cohort_size]
    return ranked

POLICIES: dict[str, Policy] = {
    "nearest": _nearest,
    "reliability": _reliability,
    "broadcast": _broadcast,
    "random": _random,
    "bloodnet": _bloodnet,
}


def _probability_of_at_least(indexes: Iterable[int], donors: list[DonorCandidate], need: int) -> float:
    probabilities = [donors[index].success_probability for index in indexes]
    distribution = [1.0]
    for probability in probabilities:
        next_distribution = [0.0] * (len(distribution) + 1)
        for completed, mass in enumerate(distribution):
            next_distribution[completed] += mass * (1 - probability)
            next_distribution[completed + 1] += mass * probability
        distribution = next_distribution
    return sum(distribution[need:]) if need < len(distribution) else 0.0


def _make_donor_pool(rng: random.Random, donor_count: int) -> tuple[DonorCandidate, ...]:
    return tuple(
        DonorCandidate(
            distance_km=rng.uniform(0.5, 50.0),
            response_probability=max(0.05, min(0.95, rng.gauss(0.65, 0.15))),
            completion_probability=max(0.5, min(0.98, rng.gauss(0.85, 0.08))),
        )
        for _ in range(donor_count)
    )


def _make_request(
    rng: random.Random,
    donor_pool: tuple[DonorCandidate, ...],
    need: int,
) -> SimulatedRequest:
    """Create a request against a persistent network donor pool."""
    return SimulatedRequest(donors=donor_pool, need=need)


def _run_request(policy: Policy, request: SimulatedRequest, rng: random.Random) -> RequestOutcome:
    donors = list(request.donors)
    selected = policy(donors, request.need, rng)
    responses = [rng.random() < donors[index].response_probability for index in selected]
    completions = [response and rng.random() < donors[index].completion_probability for response, index in zip(responses, selected)]
    return RequestOutcome(
        fulfilled=sum(completions) >= request.need,
        notifications=len(selected),
        response_rate=sum(responses) / len(selected) if selected else 0.0,
        completion_rate=sum(completions) / len(selected) if selected else 0.0,
        travel_km=mean(donors[index].distance_km for index in selected) if selected else 0.0,
        fatigue=len(selected) / len(donors),
    )


def evaluate(*, requests: int = 100, seeds: int = 5, donor_count: int = 50, need: int = 2,
             policies: tuple[str, ...] = tuple(POLICIES), bootstrap_resamples: int = 1000) -> dict:
    if requests <= 0 or seeds <= 0 or donor_count <= 0 or need <= 0 or bootstrap_resamples <= 0:
        raise ValueError("requests and seeds must be positive")
    for name in policies:
        if name not in POLICIES:
            raise ValueError(f"Unknown policy '{name}'")
    results: dict[str, dict] = {}
    outcomes_by_arm: dict[str, list[RequestOutcome]] = {name: [] for name in policies}
    arm_rngs = {
        name: random.Random((ARM_NAMES.index(name) + 1) * 97)
        for name in policies
    }
    for seed in range(seeds):
        donor_pool = _make_donor_pool(random.Random(seed * 1_000_003), donor_count)
        for request_number in range(requests):
            request = _make_request(
                random.Random(seed * 1_000_003 + request_number),
                donor_pool,
                need,
            )
            for name in policies:
                outcomes_by_arm[name].append(
                    _run_request(POLICIES[name], request, arm_rngs[name])
                )
    for name in policies:
        outcomes = outcomes_by_arm[name]
        results[name] = {
            "requests": len(outcomes),
            "fulfillment_rate": mean(outcome.fulfilled for outcome in outcomes),
            "notifications_per_request": mean(outcome.notifications for outcome in outcomes),
            "donor_response_rate": mean(outcome.response_rate for outcome in outcomes),
            "donor_completion_rate": mean(outcome.completion_rate for outcome in outcomes),
            "mean_donor_travel_km": mean(outcome.travel_km for outcome in outcomes),
            "donor_fatigue_index": mean(outcome.fatigue for outcome in outcomes),
        }
        for metric, values in _metric_values(outcomes).items():
            lower, upper = bootstrap_ci(values, resamples=bootstrap_resamples, seed=len(name))
            results[name].setdefault("confidence_intervals", {})[metric] = {
                "lower": lower,
                "upper": upper,
            }
    return {
        "requests_per_seed": requests,
        "total_requests": requests * seeds,
        "seeds": seeds,
        "bootstrap_resamples": bootstrap_resamples,
        "arms": results,
    }


def _metric_values(outcomes: list[RequestOutcome]) -> dict[str, list[float]]:
    return {
        "fulfillment_rate": [float(outcome.fulfilled) for outcome in outcomes],
        "notifications_per_request": [outcome.notifications for outcome in outcomes],
        "donor_response_rate": [outcome.response_rate for outcome in outcomes],
        "donor_completion_rate": [outcome.completion_rate for outcome in outcomes],
        "mean_donor_travel_km": [outcome.travel_km for outcome in outcomes],
        "donor_fatigue_index": [outcome.fatigue for outcome in outcomes],
    }


def bootstrap_ci(values: list[float], *, resamples: int = 1000, seed: int = 0) -> tuple[float, float]:
    if not values or resamples <= 0:
        raise ValueError("values and resamples must be non-empty/positive")
    rng = random.Random(seed)
    means = [mean(rng.choice(values) for _ in values) for _ in range(resamples)]
    means.sort()
    lower_index = max(0, min(resamples - 1, int((resamples - 1) * 0.025)))
    upper_index = max(0, min(resamples - 1, int((resamples - 1) * 0.975)))
    return means[lower_index], means[upper_index]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--donors", type=int, default=35000)
    parser.add_argument("--need", type=int, default=2)
    parser.add_argument("--output", type=str)
    args = parser.parse_args()
    import json
    report = evaluate(requests=args.requests, seeds=args.seeds, donor_count=args.donors, need=args.need)
    serialized = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as report_file:
            report_file.write(serialized + "\n")
    print(serialized)

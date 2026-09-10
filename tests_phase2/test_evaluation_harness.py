from sim.evaluation import bootstrap_ci, evaluate


def test_evaluation_runs_matched_seed_arms():
    report = evaluate(requests=4, seeds=2, donor_count=10, need=2)
    assert report["requests_per_seed"] == 4
    assert set(report["arms"]) == {"nearest", "reliability", "broadcast", "random", "bloodnet"}
    assert all(0 <= arm["fulfillment_rate"] <= 1 for arm in report["arms"].values())


def test_bootstrap_interval_is_reproducible():
    assert bootstrap_ci([0.0, 1.0, 1.0, 0.0], resamples=100, seed=7) == bootstrap_ci([0.0, 1.0, 1.0, 0.0], resamples=100, seed=7)

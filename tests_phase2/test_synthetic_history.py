from datetime import date

from synthetic_history import generate_synthetic_history, holdout_summary


def test_synthetic_history_is_deterministic_and_covers_required_dimensions():
    rows, metadata = generate_synthetic_history(end_date=date(2025, 12, 31))

    calendar_days = (date(2025, 12, 31) - date(2023, 1, 1)).days + 1
    assert len(rows) == calendar_days * 4 * 8
    assert metadata["dataset_label"] == "synthetic_mvp"
    assert len({row.region for row in rows}) == 4
    assert len({row.blood_group for row in rows}) == 8
    assert min(row.demand_date for row in rows) == date(2023, 1, 1)
    assert max(row.demand_date for row in rows) == date(2025, 12, 31)
    assert rows == generate_synthetic_history(end_date=date(2025, 12, 31))[0]


def test_synthetic_history_reports_holdout_and_contains_spikes():
    rows, metadata = generate_synthetic_history(end_date=date(2025, 12, 31))
    summary = holdout_summary(rows, metadata)
    regular = [row.requested_units for row in rows if row.demand_date.month not in (1, 10, 12)]

    assert summary["holdout_rows"] == 28 * 4 * 8
    assert summary["baseline_mae_units"] > 0
    assert max(row.requested_units for row in rows) > 2 * (sum(regular) / len(regular))
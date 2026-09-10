from datetime import date, datetime, timedelta

from eligibility import check_eligibility
from scoring import score_donors
from contracts.models import BloodGroup, Component, DeferralFlag, Donor, GeoPoint

NOW = datetime(2026, 8, 22, 12, 0, 0)


def make_donor(**overrides) -> Donor:
    defaults = dict(
        donor_id="d1",
        blood_group=BloodGroup.O_POS,
        geo=GeoPoint(lat=18.52, lng=73.85),
        last_donation_at=NOW - timedelta(days=200),
        deferral_flags=[],
        consent_scopes=["contactable"],
    )
    defaults.update(overrides)
    return Donor(**defaults)


def test_healthy_donor_is_eligible():
    donor = make_donor()
    result = check_eligibility(
        donor, Component.RBC, age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW
    )
    assert result.eligible
    assert result.reasons == []


def test_underage_donor_rejected():
    donor = make_donor()
    result = check_eligibility(
        donor, Component.RBC, age_years=16, weight_kg=65, hb_g_dl=13.5, as_of=NOW
    )
    assert not result.eligible
    assert any("age" in r for r in result.reasons)


def test_underweight_donor_rejected():
    donor = make_donor()
    result = check_eligibility(
        donor, Component.RBC, age_years=30, weight_kg=42, hb_g_dl=13.5, as_of=NOW
    )
    assert not result.eligible
    assert any("weight" in r for r in result.reasons)


def test_low_hemoglobin_rejected():
    donor = make_donor()
    result = check_eligibility(
        donor, Component.RBC, age_years=30, weight_kg=65, hb_g_dl=10.0, as_of=NOW
    )
    assert not result.eligible
    assert any("hemoglobin" in r for r in result.reasons)


def test_too_soon_since_last_whole_blood_donation_rejected():
    donor = make_donor(last_donation_at=NOW - timedelta(days=30))
    result = check_eligibility(
        donor, Component.RBC, age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW
    )
    assert not result.eligible
    assert any("since last donation" in r for r in result.reasons)


def test_platelet_interval_is_shorter_than_rbc_interval():
    # 30 days is enough for platelets (14d min) but not RBC (90d min)
    donor = make_donor(last_donation_at=NOW - timedelta(days=30))
    result = check_eligibility(
        donor, Component.PLATELETS_RDP, age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW
    )
    assert result.eligible


def test_permanent_deferral_rejected():
    donor = make_donor(deferral_flags=[DeferralFlag(reason="chronic condition", deferred_until=None)])
    result = check_eligibility(
        donor, Component.RBC, age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW
    )
    assert not result.eligible
    assert any("permanent deferral" in r for r in result.reasons)


def test_active_temporary_deferral_rejected():
    donor = make_donor(
        deferral_flags=[
            DeferralFlag(reason="recent travel", deferred_until=(NOW + timedelta(days=10)).date())
        ]
    )
    result = check_eligibility(
        donor, Component.RBC, age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW
    )
    assert not result.eligible


def test_expired_temporary_deferral_does_not_block():
    donor = make_donor(
        deferral_flags=[
            DeferralFlag(reason="recent travel", deferred_until=(NOW - timedelta(days=10)).date())
        ]
    )
    result = check_eligibility(
        donor, Component.RBC, age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW
    )
    assert result.eligible


def test_donor_without_contactable_consent_rejected():
    donor = make_donor(consent_scopes=[])
    result = check_eligibility(
        donor, Component.RBC, age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW
    )
    assert not result.eligible
    assert any("consent" in r for r in result.reasons)


def test_result_is_reproducible_and_carries_rule_version():
    donor = make_donor()
    r1 = check_eligibility(donor, Component.RBC, age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW)
    r2 = check_eligibility(donor, Component.RBC, age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW)
    assert r1 == r2
    assert r1.rule_version


def test_score_donors_defaults_missing_history_instead_of_failing():
    donor = Donor(
        donor_id="d_missing_history",
        blood_group=BloodGroup.A_POS,
        geo=GeoPoint(lat=18.52, lng=73.85),
        age_years=30,
        consent_scopes=["contactable"],
        reliability_features={
            "historical_response_rate": 0.85,
            "historical_completion_rate": 0.9,
            "distance_to_bank_km": 3.2,
        },
    )

    scores = score_donors([donor])

    assert len(scores) == 1
    assert 0.0 <= scores[0] <= 1.0

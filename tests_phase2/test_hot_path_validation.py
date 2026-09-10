from datetime import datetime, timedelta, timezone
from threading import Event
import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
MATCH_DIR = ROOT / "services" / "match-svc"
SWARM_DIR = ROOT / "services" / "swarm-svc"
for path in (ROOT, MATCH_DIR, SWARM_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from contracts.events import EventEnvelope
from contracts.models import (
    BloodBank,
    BloodGroup,
    Case,
    Component,
    Donor,
    GeoPoint,
    Hospital,
    InventoryStatus,
    InventoryUnit,
    Request,
    Urgency,
)
from event_handler import SwarmEventHandler
from eligibility import EligibilityScreening
from inventory_match import InventoryMatchResult
from request_flow import match_request


def make_request(qty: int = 2) -> Request:
    return Request(
        request_id="REQ-HOT-PATH",
        group=BloodGroup.A_POS,
        component=Component.RBC,
        qty=qty,
        hospital_id="H1",
        urgency=Urgency.CRITICAL,
        required_by=datetime.now(timezone.utc) + timedelta(hours=2),
        source_channel="test",
    )


def make_hospital() -> Hospital:
    return Hospital(
        hospital_id="H1",
        name="Hospital",
        geo=GeoPoint(lat=18.5204, lng=73.8567),
        tier="tier1",
    )


def make_donor(donor_id: str, group: BloodGroup = BloodGroup.A_POS) -> Donor:
    return Donor(
        donor_id=donor_id,
        blood_group=group,
        geo=GeoPoint(lat=18.52, lng=73.85),
        age_years=30,
        reliability_features={
            "historical_response_rate": 0.8,
            "historical_completion_rate": 0.8,
            "days_since_last_donation": 180,
            "is_repeat_donor": 1,
        },
    )


def make_unit(unit_id: str = "U1") -> InventoryUnit:
    now = datetime.now(timezone.utc)
    return InventoryUnit(
        unit_id=unit_id,
        bank_id="B1",
        group=BloodGroup.A_POS,
        component=Component.RBC,
        collected_at=now - timedelta(days=2),
        expires_at=now + timedelta(days=20),
        status=InventoryStatus.AVAILABLE,
    )


def test_inventory_and_donor_branches_overlap(monkeypatch):
    inventory_started = Event()
    donor_started = Event()
    both_started = Event()

    def inventory_branch(*args):
        inventory_started.set()
        assert donor_started.wait(1)
        both_started.set()
        return InventoryMatchResult(matches=[], units_from_inventory=0, units_remaining=2)

    def score_donors(*args):
        donor_started.set()
        assert inventory_started.wait(1)
        both_started.set()
        return [0.9]

    import request_flow
    monkeypatch.setattr(request_flow, "find_inventory_matches", inventory_branch)
    monkeypatch.setattr(request_flow, "score_donors", score_donors)

    result = match_request(
        make_request(), make_hospital(), [], [], [make_donor("D1")],
        eligible_donor_ids={"D1"},
    )

    assert both_started.is_set()
    assert result.case.units_from_donors_remaining == 2


def test_gates_and_shortfall_reach_swarm(monkeypatch):
    scored_ids = []

    def score(donors):
        scored_ids.extend(donor.donor_id for donor in donors)
        return [0.9] * len(donors)

    import request_flow
    monkeypatch.setattr(request_flow, "score_donors", score)
    unit = make_unit()
    result = match_request(
        make_request(2),
        make_hospital(),
        [BloodBank(bank_id="B1", name="Bank", geo=make_hospital().geo, licence_id="L1")],
        [unit],
        [make_donor("eligible"), make_donor("ineligible"), make_donor("wrong", BloodGroup.B_POS)],
        eligible_donor_ids={"eligible", "ineligible"},
    )

    assert scored_ids == ["eligible", "ineligible"]
    assert result.case.units_from_inventory == 1
    assert result.case.units_from_donors_remaining == 1

    event = EventEnvelope.case_ranked(
        result.case.request_id, result.case, result.ranked_donors,
        correlation_id="CORR-HOT-PATH",
    )
    notification_event = SwarmEventHandler().handle_case_ranked(event)
    assert notification_event is not None
    assert notification_event.correlation_id == "CORR-HOT-PATH"
    assert notification_event.payload.case.units_from_donors_remaining == 1


def test_screened_ineligible_donor_never_reaches_scorer(monkeypatch):
    scored_ids = []

    def score(donors):
        scored_ids.extend(donor.donor_id for donor in donors)
        return [0.9] * len(donors)

    import request_flow
    monkeypatch.setattr(request_flow, "score_donors", score)
    eligible = make_donor("eligible")
    eligible.consent_scopes = ["contactable"]
    ineligible = make_donor("ineligible")
    ineligible.consent_scopes = ["contactable"]
    screenings = {
        "eligible": EligibilityScreening(age_years=30, weight_kg=65, hb_g_dl=13.5),
        "ineligible": EligibilityScreening(age_years=17, weight_kg=65, hb_g_dl=13.5),
    }

    result = match_request(
        make_request(1), make_hospital(), [], [], [eligible, ineligible],
        eligible_donor_ids={"eligible", "ineligible"},
        eligibility_records=screenings,
    )

    assert scored_ids == ["eligible"]
    assert [item["donor_id"] for item in result.ranked_donors] == ["eligible"]


def test_scoring_receives_request_aware_context(monkeypatch):
    captured = {}

    def score(donors):
        captured.update(donors[0].reliability_features)
        return [0.9]

    import request_flow
    monkeypatch.setattr(request_flow, "score_donors", score)
    hospital = make_hospital()
    bank = BloodBank(
        bank_id="B1", name="Bank", geo=GeoPoint(lat=18.53, lng=73.86), licence_id="L1"
    )

    match_request(
        make_request(2), hospital, [bank], [], [make_donor("D1")],
        eligible_donor_ids={"D1"},
    )

    assert captured["distance_to_bank_km"] > 0
    assert captured["travel_time_min"] > 0
    assert captured["hour_of_day"] == make_request().required_by.hour
    assert captured["urgency_level"] == 2
    assert captured["group_scarcity_index"] == 1.0


def test_zero_shortfall_does_not_mobilize_donors(monkeypatch):
    scored = []
    import request_flow
    monkeypatch.setattr(request_flow, "score_donors", lambda donors: scored.extend(donors) or [0.9] * len(donors))
    hospital = make_hospital()
    result = match_request(
        make_request(1),
        hospital,
        [BloodBank(bank_id="B1", name="Bank", geo=hospital.geo, licence_id="L1")],
        [make_unit()],
        [make_donor("D1")],
        eligible_donor_ids={"D1"},
    )

    notification_event = SwarmEventHandler().handle_case_ranked(
        EventEnvelope.case_ranked(
            result.case.request_id,
            result.case,
            result.ranked_donors,
            correlation_id="CORR-0",
        )
    )
    assert result.case.units_from_donors_remaining == 0
    assert notification_event is None


def test_next_round_accounts_for_accepted_units():
    case = Case(
        case_id="CASE-ESCALATE",
        request_id="REQ-ESCALATE",
        units_from_inventory=0,
        units_from_donors_remaining=2,
    )
    ranked_donors = [
        {"donor_id": "D1", "success_probability": 0.99},
        {"donor_id": "D2", "success_probability": 0.99},
        {"donor_id": "D3", "success_probability": 0.99},
    ]
    handler = SwarmEventHandler()
    event = EventEnvelope.case_ranked(
        case.request_id, case, ranked_donors, correlation_id="CORR-ESCALATE"
    )

    notification_event = handler.next_round(event, accepted=1, pending_ids={"D1"})

    assert notification_event is not None
    assert notification_event.payload.donor_ids == ["D2"]


def test_invalid_donor_coordinates_are_excluded_before_scoring(monkeypatch):
    scored_ids = []
    import request_flow
    monkeypatch.setattr(
        request_flow,
        "score_donors",
        lambda donors: scored_ids.extend(d.donor_id for d in donors) or [0.9] * len(donors),
    )
    invalid = make_donor("INVALID")
    invalid.geo = GeoPoint(lat=float("nan"), lng=73.85)

    match_request(
        make_request(1), make_hospital(), [], [], [make_donor("VALID"), invalid],
        eligible_donor_ids={"VALID", "INVALID"},
    )

    assert scored_ids == ["VALID"]


def test_fifty_thousand_donor_ranking_under_target():
    donors = [make_donor(f"D{i}") for i in range(50_000)]
    eligible = {donor.donor_id for donor in donors}

    # Warm up the model & caches once so the benchmark measures the real hot path,
    # not the first-call cost while the LightGBM artifact is still being loaded.
    match_request(make_request(1), make_hospital(), [], [], donors, eligible_donor_ids=eligible)

    latencies_ms = []
    for _ in range(10):
        started = time.perf_counter()
        match_request(make_request(1), make_hospital(), [], [], donors, eligible_donor_ids=eligible)
        latencies_ms.append((time.perf_counter() - started) * 1000)

    p95_ms = sorted(latencies_ms)[int(len(latencies_ms) * 0.95) - 1]
    assert p95_ms < 800


def test_model_is_well_calibrated_and_explains_predictions():
    import joblib
    import shap
    from ml.donor_success.train import create_dummy_data
    from scoring import calibration_metrics

    df = create_dummy_data(20000)
    X = df.drop(columns=["target"])
    y = df["target"].astype(int).tolist()

    model = joblib.load(ROOT / "ml" / "donor_success" / "model.joblib")
    probabilities = model.predict_proba(X)[:, 1]
    ece = calibration_metrics(probabilities.tolist(), y)["ece"]
    assert ece <= 0.05

    explainer = shap.TreeExplainer(model)
    values = explainer.shap_values(X.iloc[:3])
    if isinstance(values, list):
        assert len(values) == 2
        arr = np.asarray(values[1])
    else:
        arr = np.asarray(values)
    assert arr.shape[1] == X.shape[1]

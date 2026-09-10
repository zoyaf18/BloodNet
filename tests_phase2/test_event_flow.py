from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "contracts", ROOT / "services" / "match-svc", ROOT / "services" / "swarm-svc", ROOT / "ml"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from contracts.event_bus import LocalEventBus
from contracts.events import EventType, EventEnvelope
from contracts.models import BloodGroup, Component, Donor, GeoPoint, Hospital, Request, Urgency
from request_flow import match_request


def _request():
    return Request(
        request_id="REQ-TEST-001",
        group=BloodGroup.A_POS,
        component=Component.RBC,
        qty=3,
        hospital_id="H1",
        urgency=Urgency.CRITICAL,
        required_by=datetime.now(timezone.utc) + timedelta(hours=2),
        source_channel="test",
    )


def test_request_created_produces_case_ranked():
    request = _request()
    hospital = Hospital(hospital_id="H1", name="H", geo=GeoPoint(lat=18.52, lng=73.85), tier="tier1")
    donor = Donor(donor_id="D1", blood_group=BloodGroup.A_POS, geo=hospital.geo,
                  reliability_features={"historical_response_rate": .9, "historical_completion_rate": .9,
                                        "days_since_last_donation": 100, "age": 30, "is_repeat_donor": 1,
                                        "distance_to_bank_km": 2})
    bus = LocalEventBus()
    def handler(event):
        result = match_request(request, hospital, [], [], [donor], eligible_donor_ids={"D1"})
        return EventEnvelope.case_ranked(request.request_id, result.case, result.ranked_donors)
    bus.subscribe(EventType.REQUEST_CREATED, handler)
    outputs = bus.publish(EventEnvelope.request_created(request))
    assert len(outputs) == 1
    assert outputs[0].event_type == EventType.CASE_RANKED
    assert outputs[0].payload.case.request_id == request.request_id
    assert outputs[0].payload.case.units_from_inventory == 0
    assert outputs[0].payload.case.units_from_donors_remaining == 3


def test_duplicate_event_is_ignored():
    bus = LocalEventBus()
    calls = []
    bus.subscribe(EventType.REQUEST_CREATED, lambda event: calls.append(event.event_id))
    event = EventEnvelope.request_created(_request())
    assert bus.publish(event) == []
    assert bus.publish(event) == []
    assert calls == [event.event_id]

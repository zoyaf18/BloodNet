import json
from datetime import datetime, timedelta, timezone

from contracts.event_bus import LocalEventBus, PubSubEventBus, create_event_bus
from contracts.events import (
    AuditEventPayload,
    CaseRankedPayload,
    EventEnvelope,
    EventType,
    NotificationRequestedPayload,
    RecommendationCreatedPayload,
    RequestCreatedPayload,
)
from contracts.models import AuditRecord, BloodGroup, Case, Component, Recommendation, Request, Urgency


class FakePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, topic, data, **kwargs):
        self.messages.append({"topic": topic, "data": data, **kwargs})
        return self

    def result(self):
        return None


class FakeSubscriber:
    def __init__(self):
        self.calls = []

    def subscribe(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def _request():
    return Request(
        request_id="REQ-PUBSUB-001",
        group=BloodGroup.A_POS,
        component=Component.RBC,
        qty=2,
        hospital_id="H1",
        urgency=Urgency.CRITICAL,
        required_by=datetime.now(timezone.utc) + timedelta(hours=2),
        source_channel="pubsub-test",
    )


def test_pubsub_adapter_keeps_local_bus_semantics():
    publisher = FakePublisher()
    bus = PubSubEventBus(publisher=publisher)

    def handler(event):
        return [
            EventEnvelope.request_created(_request()),
            EventEnvelope.request_created(_request()),
        ]

    bus.subscribe(EventType.REQUEST_CREATED, handler)
    event = EventEnvelope.request_created(_request())

    outputs = bus.publish(event)

    assert len(outputs) == 2
    assert outputs[0].event_type == EventType.REQUEST_CREATED
    assert outputs[1].event_type == EventType.REQUEST_CREATED
    assert len(publisher.messages) == 1
    payload = json.loads(publisher.messages[0]["data"].decode("utf-8"))
    assert payload["event_id"] == event.event_id
    assert payload["event_type"] == EventType.REQUEST_CREATED.value
    assert publisher.messages[0]["data"].startswith(b"{")
    assert publisher.messages[0]["event_id"] == event.event_id
    assert publisher.messages[0]["request_id"] == event.request_id


def test_publish_external_uses_json_bytes():
    publisher = FakePublisher()
    bus = PubSubEventBus(publisher=publisher)
    event = EventEnvelope.request_created(_request())

    bus.publish_external(event)

    payload = json.loads(publisher.messages[0]["data"].decode("utf-8"))
    assert payload["correlation_id"] == event.correlation_id
    assert publisher.messages[0]["data"].startswith(b"{")


def test_event_bus_factory_defaults_to_local(monkeypatch):
    monkeypatch.delenv("BLOODNET_EVENT_TRANSPORT", raising=False)

    bus = create_event_bus()

    assert isinstance(bus, LocalEventBus)
    assert not isinstance(bus, PubSubEventBus)


def test_pubsub_lifecycle_events_have_typed_envelopes():
    case = Case(case_id="CASE-PUBSUB-001", request_id="REQ-PUBSUB-001")
    recommendation = Recommendation(
        rec_id="REC-PUBSUB-001",
        type="MOBILIZE_DONORS",
        request_id=case.request_id,
        case_id=case.case_id,
        payload={},
    )
    audit = AuditRecord(
        audit_id="AUDIT-PUBSUB-001",
        action="test",
        request_id=case.request_id,
        case_id=case.case_id,
        event_id="EVT-PUBSUB-001",
        at=datetime.now(timezone.utc),
    )

    events = [
        (EventEnvelope.request_created(_request()), RequestCreatedPayload),
        (
            EventEnvelope.recommendation_created(recommendation),
            RecommendationCreatedPayload,
        ),
        (
            EventEnvelope.notification_requested(case, ["DONOR-1"], correlation_id=case.case_id),
            NotificationRequestedPayload,
        ),
        (EventEnvelope.audit_event(audit), AuditEventPayload),
    ]

    for event, payload_model in events:
        assert event.event_type in {
            EventType.REQUEST_CREATED,
            EventType.RECOMMENDATION_CREATED,
            EventType.NOTIFICATION_REQUESTED,
            EventType.AUDIT_EVENT,
        }
        assert payload_model.model_validate(event.payload.model_dump())

    assert CaseRankedPayload.model_validate(
        EventEnvelope.case_ranked(case.request_id, case, []).payload.model_dump()
    )

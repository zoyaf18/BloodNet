from datetime import datetime, timedelta, timezone

import pytest

from contracts.event_dispatcher import EventDispatcher
from contracts.events import (
    CaseRankedPayload,
    EventEnvelope,
    EventType,
    RequestCreatedPayload,
)
from contracts.models import BloodGroup, Case, Component, Request, Urgency


def _request() -> Request:
    return Request(
        request_id="REQ-DISPATCH-001",
        group=BloodGroup.A_POS,
        component=Component.RBC,
        qty=1,
        hospital_id="H1",
        urgency=Urgency.HIGH,
        required_by=datetime.now(timezone.utc) + timedelta(hours=2),
        source_channel="dispatcher-test",
    )


def test_dispatch_validates_payload_and_invokes_allowlisted_consumer_once():
    dispatcher = EventDispatcher()
    received = []
    dispatcher.register(
        EventType.REQUEST_CREATED,
        received.append,
        RequestCreatedPayload,
    )
    event = EventEnvelope.request_created(_request())

    assert dispatcher.dispatch(event) == []
    assert dispatcher.dispatch(event) == []
    assert len(received) == 1
    assert received[0].payload.request.request_id == "REQ-DISPATCH-001"


def test_dispatch_rejects_event_without_registered_consumer():
    dispatcher = EventDispatcher()

    with pytest.raises(ValueError, match="No consumer registered"):
        dispatcher.dispatch(EventEnvelope.request_created(_request()))


def test_dispatch_consumes_registered_downstream_events():
    dispatcher = EventDispatcher()
    received = []
    dispatcher.register(
        EventType.REQUEST_CREATED,
        lambda event: EventEnvelope.case_ranked(
            event.payload.request.request_id,
            Case(case_id="CASE-1", request_id=event.payload.request.request_id),
            [],
        ),
        RequestCreatedPayload,
    )
    dispatcher.register(EventType.CASE_RANKED, received.append, CaseRankedPayload)

    emitted = dispatcher.dispatch(EventEnvelope.request_created(_request()))

    assert [event.event_type for event in emitted] == [EventType.CASE_RANKED]
    assert received[0].payload.case.case_id == "CASE-1"


def test_dispatch_republishes_emitted_events_without_reinvoking_consumers():
    published = []
    dispatcher = EventDispatcher(publish_output=published.append)
    dispatcher.register(
        EventType.REQUEST_CREATED,
        lambda event: EventEnvelope.case_ranked(
            event.payload.request.request_id,
            Case(case_id="CASE-2", request_id=event.payload.request.request_id),
            [],
        ),
        RequestCreatedPayload,
    )

    emitted = dispatcher.dispatch(EventEnvelope.request_created(_request()))

    assert published == emitted
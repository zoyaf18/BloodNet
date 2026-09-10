"""Allowlisted dispatch for events arriving from an external transport."""
from __future__ import annotations

from typing import Any, Callable, TypeVar

from contracts.event_bus import Handler, LocalEventBus
from contracts.events import EventEnvelope, EventType
from pydantic import BaseModel

PayloadModel = TypeVar("PayloadModel", bound=BaseModel)


class UnsupportedEventError(ValueError):
    """Raised when an event has no configured business consumer."""


class EventDispatcher:
    """Route validated envelopes to registered business consumers."""

    def __init__(
        self,
        *,
        bus: LocalEventBus | None = None,
        publish_output: Callable[[EventEnvelope[Any]], None] | None = None,
    ) -> None:
        self.bus = bus or LocalEventBus()
        self.publish_output = publish_output
        self._payload_models: dict[EventType, type[BaseModel]] = {}

    def register(
        self,
        event_type: EventType,
        handler: Handler,
        payload_model: type[PayloadModel],
    ) -> None:
        self.bus.subscribe(event_type, handler)
        self._payload_models[event_type] = payload_model

    def dispatch(self, event: EventEnvelope[Any]) -> list[EventEnvelope[Any]]:
        if event.event_type not in self._payload_models:
            raise UnsupportedEventError(
                f"No consumer registered for event type: {event.event_type.value}"
            )

        emitted: list[EventEnvelope[Any]] = []
        pending = [event]
        while pending:
            current = pending.pop(0)
            payload_model = self._payload_models.get(current.event_type)
            if payload_model is None:
                continue
            typed_event = EventEnvelope[payload_model].model_validate(
                current.model_dump()
            )
            outputs = self.bus.publish(typed_event)
            emitted.extend(outputs)
            if self.publish_output is not None:
                for output in outputs:
                    self.publish_output(output)
            pending.extend(
                output
                for output in outputs
                if output.event_type in self._payload_models
            )
        return emitted

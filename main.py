"""
BloodNet unified API — container entrypoint for Cloud Run.

This is the thin composition layer for the single Cloud Run service:

    bloodnet-api

It loads the existing match-svc and swarm-svc FastAPI applications
under unique module aliases and mounts them into one FastAPI application.

No business logic lives here.

Mounted endpoints:

    GET  /health
    GET  /

    *    /match-svc/...
    *    /swarm-svc/...
    *    /intake-svc/...

OpenAPI:

    /match-svc/docs
    /swarm-svc/docs

Run locally from the repository root:

    uvicorn main:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from datetime import datetime
import importlib.util
import json
import logging
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID
import numpy  # noqa: F401
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
import psycopg
from psycopg.rows import dict_row
from contracts.events import (
    AuditEventPayload,
    CaseRankedPayload,
    DonorResponsePayload,
    EventEnvelope,
    EventType,
    NotificationRequestedPayload,
    NotificationsRequestedPayload,
    RecommendationCreatedPayload,
    RequestCreatedPayload,
)
from contracts.event_dispatcher import EventDispatcher, UnsupportedEventError
from contracts.event_bus import create_event_bus
from contracts.config import load_runtime_config
from contracts.bigquery_event_sink import BigQueryEventSink
from contracts.auth import Identity, get_identity, require_role
from contracts.capabilities import capability_report
from contracts.location import operational_region

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Repository paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
SERVICES = ROOT / "services"
runtime_config = load_runtime_config()

INTEGRATION_DIR = SERVICES / "integration-svc"
sys.path.insert(0, str(INTEGRATION_DIR))
from clinical_adapters import clinical_request_text
sys.path.remove(str(INTEGRATION_DIR))


# The repository root must remain importable because the services use:

#     from contracts.models import ...

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Service loader
# ---------------------------------------------------------------------------

def _load_service_app(
    service_dir_name: str,
    alias: str,
) -> ModuleType:
    """
    Load a service's main.py under a unique module name.

    The service directory is temporarily added to sys.path so existing
    bare imports inside the service continue to work, for example:

        from scoring import score_donors
        from escalation import EscalationEngine

    IMPORTANT:

    We deliberately do NOT remove modules from sys.modules after loading.

    Removing third-party modules such as numpy from sys.modules can cause
    compiled/native extensions to be loaded more than once in the same
    Python process, producing errors such as:

        ImportError: cannot load module more than once per process

    The two current services use different local module names, so keeping
    those modules loaded is safe for the current codebase.
    """

    service_dir = (SERVICES / service_dir_name).resolve()
    module_path = service_dir / "main.py"

    # Give a useful error instead of the confusing FileNotFoundError that
    # otherwise appears several stack frames later.
    if not service_dir.exists():
        raise FileNotFoundError(
            f"BloodNet service directory does not exist:\n"
            f"  {service_dir}\n\n"
            f"Expected repository structure:\n"
            f"  {ROOT / 'services' / service_dir_name}"
        )

    if not module_path.exists():
        raise FileNotFoundError(
            f"BloodNet service entrypoint does not exist:\n"
            f"  {module_path}\n\n"
            f"Service directory found at:\n"
            f"  {service_dir}\n\n"
            f"Expected a main.py inside that directory."
        )

    service_dir_string = str(service_dir)

    # The service's main.py relies on bare imports from its own directory.
    sys.path.insert(0, service_dir_string)

    try:
        spec = importlib.util.spec_from_file_location(
            alias,
            module_path,
        )

        if spec is None or spec.loader is None:
            raise ImportError(
                f"Could not create import specification for:\n"
                f"  {module_path}"
            )

        module = importlib.util.module_from_spec(spec)

        # Register the module under a unique name before executing it.
        #
        # This is important because both services have a file named main.py.
        sys.modules[alias] = module

        spec.loader.exec_module(module)

        # Every mounted service must expose a FastAPI app.
        if not hasattr(module, "app"):
            raise AttributeError(
                f"Service '{service_dir_name}' does not expose an "
                f"'app' object in:\n"
                f"  {module_path}"
            )

        return module

    finally:
        # Remove only the temporary service directory from sys.path.
        #
        # Do NOT manipulate sys.modules here.
        if service_dir_string in sys.path:
            sys.path.remove(service_dir_string)


# ---------------------------------------------------------------------------
# Load existing services
# ---------------------------------------------------------------------------

match_svc = _load_service_app(
    "match-svc",
    "bloodnet_match_svc_main",
)

swarm_svc = _load_service_app(
    "swarm-svc",
    "bloodnet_swarm_svc_main",
)

intake_svc = _load_service_app(
    "intake-svc",
    "bloodnet_intake_svc_main",
)

agent_svc = _load_service_app(
    "agent-svc",
    "bloodnet_agent_svc_main",
)

graph_svc = _load_service_app(
    "graph-svc",
    "bloodnet_graph_svc_main",
)

copilot_svc = _load_service_app(
    "copilot-svc",
    "bloodnet_copilot_svc_main",
)

swarm_handler_dir = str(SERVICES / "swarm-svc")
sys.path.insert(0, swarm_handler_dir)
from event_handler import SwarmEventHandler
sys.path.remove(swarm_handler_dir)

# Connect the local donor-response adapter to the same process-local case
# projection used by match-svc. Production transport will use the event bus.
swarm_svc.set_response_handler(match_svc.handle_donor_response)


# External Pub/Sub deliveries enter the same validated event path as local
# consumers. Only event types with an explicitly registered business handler
# are accepted by the ingress.
outbound_event_bus = (
    create_event_bus(
        transport="pubsub",
        project_id=runtime_config.project_id,
        topic=runtime_config.pubsub_topic,
    )
    if runtime_config.event_transport == "pubsub"
    else None
)
event_dispatcher = EventDispatcher(
    publish_output=(outbound_event_bus.publish_external if outbound_event_bus else None)
)
swarm_event_handler = SwarmEventHandler()
bq_event_sink = BigQueryEventSink(
    project_id=runtime_config.project_id,
    dataset_id=os.getenv("BLOODNET_BQ_DATASET", "bloodnet"),
) if os.getenv("BLOODNET_BQ_EVENTS_ENABLED", "false").lower() == "true" else None


def _dispatch_case_ranked(event: EventEnvelope):
    return swarm_event_handler.handle_case_ranked(event)


def _persist_request(event: EventEnvelope):
    if match_svc.workflow_store is not None:
        request = event.payload.request
        match_svc.workflow_store.requests[request.request_id] = request.model_dump(mode="json")
        match_svc.workflow_store.persist_request(
            request.request_id, request.model_dump(mode="json")
        )


def _persist_recommendation(event: EventEnvelope):
    if match_svc.workflow_store is not None:
        recommendation = event.payload.recommendation
        match_svc.workflow_store.register_generic_recommendation(recommendation)
        match_svc.workflow_store.persist_recommendation(recommendation)


def _persist_audit_event(event: EventEnvelope):
    if match_svc.workflow_store is not None:
        match_svc.workflow_store.audit.append(event.payload.record)


def _handle_notification_requested(event: EventEnvelope):
    if match_svc.workflow_store is not None:
        return match_svc.workflow_store.notifications.handle_request(event)
    return None


def _handle_donor_response(event: EventEnvelope):
    if match_svc.workflow_store is not None and event.payload.outreach_id:
        response_store = match_svc.workflow_store.donor_response_repository
        response_store.save_response(
            event.payload.outreach_id,
            event.payload.donor_id,
            event.payload.response.value,
            event.occurred_at,
        )
        match_svc.handle_donor_response(
            event.payload.outreach_id,
            event.payload.donor_id,
            event.payload.response.value,
        )
        if event.payload.response.value != "accept":
            response_store.mark_processed(
                event.payload.outreach_id, event.payload.donor_id
            )
    return None


event_dispatcher.register(EventType.REQUEST_CREATED, _persist_request, RequestCreatedPayload)
event_dispatcher.register(EventType.CASE_RANKED, _dispatch_case_ranked, CaseRankedPayload)
event_dispatcher.register(
    EventType.RECOMMENDATION_CREATED,
    _persist_recommendation,
    RecommendationCreatedPayload,
)
event_dispatcher.register(EventType.AUDIT_EVENT, _persist_audit_event, AuditEventPayload)
event_dispatcher.register(
    EventType.NOTIFICATIONS_REQUESTED,
    _handle_notification_requested,
    NotificationsRequestedPayload,
)
event_dispatcher.register(
    EventType.NOTIFICATION_REQUESTED,
    _handle_notification_requested,
    NotificationRequestedPayload,
)
event_dispatcher.register(
    EventType.DONOR_RESPONSE_RECEIVED,
    _handle_donor_response,
    DonorResponsePayload,
)


# ---------------------------------------------------------------------------
# Unified FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="BloodNet API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    description=(
        "Unified Cloud Run entrypoint for BloodNet. "
        "Mounts match-svc and swarm-svc under one API."
    ),
)

frontend_dir = ROOT / "web" / "app" / "dist"
if frontend_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dir / "assets")), name="frontend_assets")

app.add_middleware(
    CORSMiddleware,
        allow_origins=list(runtime_config.allowed_origins),
        allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PubSubMessage(BaseModel):
    data: str = Field(min_length=1)
    attributes: dict[str, str] = Field(default_factory=dict)


class PubSubPushRequest(BaseModel):
    message: PubSubMessage
    subscription: str | None = None


class InboundRequest(BaseModel):
    raw_text: str = Field(min_length=1, max_length=20_000)
    hospital_id: str = Field(min_length=1)
    source_channel: str = Field(default="inbound", min_length=1, max_length=64)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    required_by: datetime | None = None
    provider: str | None = Field(default=None, pattern="^(local|gemini|vertex_ai_gemini|vertexai_gemini)$")


InboundRequest.model_rebuild()


class ClinicalInboundRequest(BaseModel):
    format: str = Field(pattern="^(fhir|hl7v2)$")
    payload: dict[str, Any] | str
    hospital_id: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1, max_length=128)
    required_by: datetime | None = None
    provider: str | None = Field(default=None, pattern="^(local|gemini|vertex_ai_gemini|vertexai_gemini)$")


class MessagingInboundRequest(BaseModel):
    """Normalized payload accepted from the managed SMS/WhatsApp gateway."""

    provider: str = Field(pattern="^(sms|whatsapp)$")
    provider_event_id: str = Field(min_length=1, max_length=256)
    sender_id: str = Field(min_length=1, max_length=256)
    message: str = Field(min_length=1, max_length=20_000)
    hospital_id: str = Field(min_length=1, max_length=128)
    required_by: datetime | None = None


class SandboxNotificationRequest(BaseModel):
    notification_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=256)
    channel: str = Field(pattern="^(sms|whatsapp|fcm)$")
    recipient_id: str = Field(min_length=1, max_length=256)
    message: str = Field(min_length=1, max_length=20_000)


ClinicalInboundRequest.model_rebuild()
MessagingInboundRequest.model_rebuild()
SandboxNotificationRequest.model_rebuild()


@app.get("/api/v1/capabilities")
def get_capabilities(identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """Return non-secret extension readiness for the authenticated UI."""
    return capability_report()


@app.post("/api/v1/sandbox/notifications/send")
def sandbox_notification_provider(
    payload: SandboxNotificationRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    """Local-only HTTP provider adapter used to exercise durable delivery."""
    if os.getenv("BLOODNET_ENV", "local").lower() in {"production", "prod", "demo"}:
        raise HTTPException(status_code=404, detail="Not found")
    expected_token = os.getenv("BLOODNET_NOTIFICATION_PROVIDER_TOKEN")
    supplied_token = (authorization or "").removeprefix("Bearer ")
    if not expected_token or not hmac.compare_digest(expected_token, supplied_token):
        raise HTTPException(status_code=401, detail="Invalid sandbox provider token")
    provider_message_id = "LOCAL-" + hashlib.sha256(payload.idempotency_key.encode("utf-8")).hexdigest()[:24]
    logger.info(
        "sandbox_notification_delivered",
        extra={"notification_id": payload.notification_id, "channel": payload.channel},
    )
    return {"provider_message_id": provider_message_id, "status": "delivered"}


def _extract_request(
    raw_text: str,
    *,
    hospital_id: str,
    source_channel: str,
    request_id: str | None,
    required_by: datetime | None,
    provider: str,
    region: str | None = None,
):
    """Use intake-svc's parser contract from the unified ingress routes."""
    request, _confidence = intake_svc.extract_request_details(
        raw_text,
        hospital_id=hospital_id,
        source_channel=source_channel,
        request_id=request_id,
        required_by=required_by,
        provider=intake_svc.create_intake_provider(provider=provider),
        region=region,
    )
    return request


def _resolve_hospital_region(identity: Identity, hospital_id: str) -> str:
    """Resolve the hospital's configured operational region for request ingestion."""
    if identity.region_id and identity.region_id.strip():
        return identity.region_id.strip()

    if identity.organization_id:
        try:
            organization = match_svc.auth_repo.get_organization_by_id(UUID(identity.organization_id))
        except Exception:
            organization = None
        if organization:
            region = operational_region((organization.metadata or {}) or {})
            if isinstance(region, str) and region.strip():
                return region.strip()

    if hospital_id:
        try:
            organizations = match_svc.auth_repo.list_organizations(status="active")
        except Exception:
            organizations = []
        for organization in organizations:
            metadata = organization.metadata or {}
            configured_hospital_id = str(metadata.get("hospital_id") or organization.id)
            if configured_hospital_id != str(hospital_id):
                continue
            region = operational_region(metadata)
            if isinstance(region, str) and region.strip():
                return region.strip()

    raise HTTPException(
        status_code=422,
        detail="Hospital regional scope is not configured. Ask a regional administrator to configure the organization region.",
    )


@app.post("/pubsub/events")
def receive_pubsub_event(payload: PubSubPushRequest) -> dict[str, str]:
    """Validate and dispatch a Pub/Sub envelope to an allowlisted consumer."""
    try:
        logger.debug(
            "receive_pubsub_event: message.data_size=%d, data_start=%r",
            len(payload.message.data),
            payload.message.data[:50],
        )
        decoded = base64.b64decode(payload.message.data, validate=True).decode("utf-8")
        logger.debug(
            "receive_pubsub_event: decoded_size=%d, decoded_start=%r",
            len(decoded),
            decoded[:100],
        )
        event = EventEnvelope.model_validate(json.loads(decoded))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        decoded_prefix = locals().get("decoded", "")[:32]
        logger.warning(
            "Invalid Pub/Sub event envelope: %s; decoded_prefix=%r",
            exc,
            decoded_prefix,
        )
        logger.debug(
            "receive_pubsub_event: message.data=%r",
            payload.message.data[:100],
        )
        raise HTTPException(status_code=400, detail="Invalid BloodNet Pub/Sub event") from exc
    if bq_event_sink is not None:
        bq_event_sink.persist(event)
    try:
        outputs = event_dispatcher.dispatch(event)
    except UnsupportedEventError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "dispatched",
        "event_id": event.event_id,
        "event_type": event.event_type.value,
        "output_event_types": ",".join(output.event_type.value for output in outputs),
    }


@app.post("/api/v1/requests/ingest")
def ingest_request(payload: InboundRequest, identity: Identity = Depends(get_identity)) -> dict[str, str]:
    """Validate, persist, and dispatch an inbound hospital request."""
    require_role(identity, "hospital_coordinator")
    if identity.hospital_id != payload.hospital_id:
        raise HTTPException(status_code=403, detail="Request hospital is outside your resource scope")
    if match_svc.workflow_store is None:
        raise HTTPException(status_code=503, detail="Workflow persistence is unavailable")
    if payload.request_id and payload.request_id in match_svc.workflow_store.requests:
        return {"status": "already_ingested", "request_id": payload.request_id}
    try:
        region = _resolve_hospital_region(identity, payload.hospital_id)
        request = _extract_request(
            payload.raw_text,
            hospital_id=payload.hospital_id,
            source_channel=payload.source_channel,
            request_id=payload.request_id,
            required_by=payload.required_by,
            provider=payload.provider or "local",
            region=region,
        )
        event = EventEnvelope.request_created(request)
        if bq_event_sink is not None:
            bq_event_sink.persist(event)
        event_dispatcher.dispatch(event)
        return {"status": "ingested", "request_id": request.request_id, "event_id": event.event_id}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/clinical/requests/ingest")
def ingest_clinical_request(payload: ClinicalInboundRequest, identity: Identity = Depends(get_identity)) -> dict[str, str]:
    """Normalize a FHIR ServiceRequest or HL7v2 order into request ingestion."""
    require_role(identity, "hospital_coordinator")
    if identity.hospital_id != payload.hospital_id:
        raise HTTPException(status_code=403, detail="Request hospital is outside your resource scope")
    request_id = f"CLIN-{payload.source_event_id}"
    if match_svc.workflow_store is None:
        raise HTTPException(status_code=503, detail="Workflow persistence is unavailable")
    if request_id in match_svc.workflow_store.requests:
        return {"status": "already_ingested", "request_id": request_id}
    try:
        if len(json.dumps(payload.payload, separators=(",", ":")).encode("utf-8")) > 1 * 1024 * 1024:
            raise ValueError("Clinical payload exceeds the 1 MB intake limit")
        text = clinical_request_text(payload.format, payload.payload)
        region = _resolve_hospital_region(identity, payload.hospital_id)
        provider = payload.provider or "gemini"
        request = _extract_request(text, hospital_id=payload.hospital_id, source_channel=f"clinical-{payload.format}", request_id=request_id, required_by=payload.required_by, provider=provider, region=region)
        event = EventEnvelope.request_created(request)
        if bq_event_sink is not None:
            bq_event_sink.persist(event)
        event_dispatcher.dispatch(event)
        return {"status": "ingested", "request_id": request.request_id, "event_id": event.event_id}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/clinical/requests/preview")
def preview_clinical_request(payload: ClinicalInboundRequest, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """Normalize and extract a clinical request without persisting or dispatching it."""
    require_role(identity, "hospital_coordinator")
    if identity.hospital_id != payload.hospital_id:
        raise HTTPException(status_code=403, detail="Request hospital is outside your resource scope")
    try:
        if len(json.dumps(payload.payload, separators=(",", ":")).encode("utf-8")) > 1 * 1024 * 1024:
            raise ValueError("Clinical payload exceeds the 1 MB intake limit")
        text = clinical_request_text(payload.format, payload.payload)
        region = _resolve_hospital_region(identity, payload.hospital_id)
        request = _extract_request(
            text,
            hospital_id=payload.hospital_id,
            source_channel=f"clinical-{payload.format}",
            request_id=f"CLIN-{payload.source_event_id}",
            required_by=payload.required_by,
            provider=payload.provider or "gemini",
            region=region,
        )
        return {"request": request.model_dump(mode="json"), "confidence": 0.0}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/inbound/messaging")
def ingest_messaging_request(
    payload: MessagingInboundRequest,
    x_bloodnet_signature: str | None = Header(default=None),
) -> dict[str, str]:
    """Accept a provider-authenticated SMS/WhatsApp request exactly once."""
    secret = os.getenv("BLOODNET_MESSAGING_WEBHOOK_SECRET")
    if not secret or not x_bloodnet_signature:
        raise HTTPException(status_code=503, detail="Messaging webhook is not configured")
    signed_value = f"{payload.provider}:{payload.provider_event_id}:{payload.sender_id}:{payload.message}:{payload.hospital_id}"
    expected = hmac.new(secret.encode("utf-8"), signed_value.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_bloodnet_signature.removeprefix("sha256=")):
        raise HTTPException(status_code=401, detail="Invalid messaging webhook signature")
    if match_svc.workflow_store is None:
        raise HTTPException(status_code=503, detail="Workflow persistence is unavailable")
    request_id = f"MSG-{payload.provider}-{payload.provider_event_id}"
    try:
        database_url = match_svc.workflow_store.database_url
        event_key = f"{payload.provider}:{payload.provider_event_id}"
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            # A transaction-scoped advisory lock makes the provider identifier a
            # real idempotency boundary even when multiple Cloud Run instances
            # receive the same retry concurrently.
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (event_key,))
            existing = connection.execute(
                "SELECT status, request_id FROM messaging_inbound_events WHERE provider = %s AND provider_event_id = %s",
                (payload.provider, payload.provider_event_id),
            ).fetchone()
            if existing and (existing["status"] == "ingested" or request_id in match_svc.workflow_store.requests):
                return {"status": "already_ingested", "request_id": existing["request_id"]}
            connection.execute(
                """
                INSERT INTO messaging_inbound_events (
                    provider, provider_event_id, request_id, hospital_id,
                    sender_fingerprint, status, failure_reason, updated_at
                ) VALUES (%s, %s, %s, %s, %s, 'processing', NULL, NOW())
                ON CONFLICT (provider, provider_event_id) DO UPDATE
                SET status = 'processing', failure_reason = NULL, updated_at = NOW()
                """,
                (
                    payload.provider,
                    payload.provider_event_id,
                    request_id,
                    payload.hospital_id,
                    hashlib.sha256(payload.sender_id.encode("utf-8")).hexdigest(),
                ),
            )
            region = _resolve_hospital_region(
                Identity(
                    subject_id="messaging-webhook",
                    email="messaging-webhook@bloodnet.local",
                    role="hospital_coordinator",
                    hospital_id=payload.hospital_id,
                    organization_id=None,
                    region_id=None,
                ),
                payload.hospital_id,
            )
            request = _extract_request(
                payload.message,
                hospital_id=payload.hospital_id,
                source_channel=payload.provider,
                request_id=request_id,
                required_by=payload.required_by,
                provider="local",
                region=region,
            )
            event = EventEnvelope.request_created(request)
            if bq_event_sink is not None:
                bq_event_sink.persist(event)
            event_dispatcher.dispatch(event)
            connection.execute(
                """
                UPDATE messaging_inbound_events
                SET status = 'ingested', processed_at = NOW(), updated_at = NOW()
                WHERE provider = %s AND provider_event_id = %s
                """,
                (payload.provider, payload.provider_event_id),
            )
        return {"status": "ingested", "request_id": request.request_id, "event_id": event.event_id}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/inbound/messaging/events")
def list_messaging_events(
    limit: int = 50,
    identity: Identity = Depends(get_identity),
) -> dict[str, Any]:
    """Return a metadata-only, role-scoped provider ingress log."""
    require_role(identity, "hospital_coordinator", "regional_admin", "auditor")
    if match_svc.workflow_store is None:
        raise HTTPException(status_code=503, detail="Workflow persistence is unavailable")
    bounded_limit = max(1, min(limit, 100))
    where = ""
    params: list[Any] = []
    if identity.role == "hospital_coordinator":
        where = "WHERE hospital_id = %s"
        params.append(identity.hospital_id)
    elif identity.region_id:
        scoped_hospital_ids = {
            str(view.get("request", {}).get("hospital_id"))
            for view in match_svc._scoped_cases(identity)
            if view.get("request", {}).get("hospital_id")
        }
        if not scoped_hospital_ids:
            return {"events": []}
        where = "WHERE hospital_id = ANY(%s)"
        params.append(list(scoped_hospital_ids))
    params.append(bounded_limit)
    with psycopg.connect(match_svc.workflow_store.database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            f"""
            SELECT provider, provider_event_id, request_id, hospital_id, status,
                   failure_reason, received_at, processed_at
            FROM messaging_inbound_events
            {where}
            ORDER BY received_at DESC
            LIMIT %s
            """,
            params,
        ).fetchall()
    return {"events": [dict(row) for row in rows]}


# Mount the existing FastAPI applications.
app.mount(
    "/match-svc",
    match_svc.app,
)

app.mount(
    "/swarm-svc",
    swarm_svc.app,
)

app.mount(
    "/intake-svc",
    intake_svc.app,
)

app.mount(
    "/agent-svc",
    agent_svc.app,
)

app.mount(
    "/graph-svc",
    graph_svc.app,
)

app.mount(
    "/copilot-svc",
    copilot_svc.app,
)


# ---------------------------------------------------------------------------
# Top-level endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    """
    Top-level liveness endpoint.

    Used by local testing, the container health check, and Cloud Run.
    """
    return {
        "status": "ok",
        "service": "bloodnet-api",
    }


if frontend_dir.exists():
    @app.get("/{path:path}", include_in_schema=False)
    async def serve_frontend_fallback(path: str) -> FileResponse:
        candidate = (frontend_dir / path).resolve()
        if candidate.is_file() and frontend_dir in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(frontend_dir / "index.html")

"""Safe, server-derived readiness summary for optional BloodNet capabilities."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def capability_report(environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Describe operational readiness without exposing configuration secrets."""
    env = environment if environment is not None else os.environ
    database = bool(env.get("BLOODNET_DATABASE_URL"))
    local_environment = (env.get("BLOODNET_ENV") or "local").lower() not in {"production", "prod", "demo"}
    project = bool(env.get("GOOGLE_CLOUD_PROJECT") or env.get("GCP_PROJECT_ID"))
    travel_mode = (env.get("BLOODNET_TRAVEL_TIME_PROVIDER") or "mock").lower()
    routes_ready = travel_mode == "google_routes" and bool(env.get("GOOGLE_MAPS_API_KEY"))
    notifications_enabled = _enabled(env.get("BLOODNET_ENABLE_NOTIFICATIONS"))
    notification_provider = bool(
        env.get("BLOODNET_NOTIFICATION_PROVIDER_URL")
        and env.get("BLOODNET_NOTIFICATION_PROVIDER_TOKEN")
        and env.get("BLOODNET_NOTIFICATION_RECEIPT_SECRET")
    )
    cloud_tasks_ready = bool(
        env.get("BLOODNET_CLOUD_TASKS_QUEUE")
        and env.get("BLOODNET_NOTIFICATION_DELIVERY_URL")
        and env.get("BLOODNET_RUNTIME_SERVICE_ACCOUNT")
    )
    notification_outbox = database and (local_environment or cloud_tasks_ready)
    agent_runtime = (env.get("BLOODNET_AGENT_RUNTIME") or "vertex_agent_engine").lower()
    managed_agent = bool(env.get("BLOODNET_AGENT_ENGINE_ENDPOINT") or env.get("BLOODNET_AGENT_ENGINE_ID"))

    def item(*, operational: bool, mode: str, note: str) -> dict[str, Any]:
        return {
            "code_ready": True,
            "operational": operational,
            "state": "ready" if operational else "configuration_required",
            "mode": mode,
            "note": note,
        }

    capabilities = {
        "rag": item(
            operational=database and project and _enabled(env.get("BLOODNET_RAG_ENABLED")),
            mode=("pgvector + managed Agent Engine" if managed_agent and agent_runtime == "vertex_agent_engine" else "pgvector + bounded Vertex AI tool loop"),
            note="Requires an approved bootstrapped corpus; retrieval is read-only and citations are enforced.",
        ),
        "graph_analysis": item(
            operational=database,
            mode="PostgreSQL projection",
            note="Analysis uses role-scoped workflow, transfer, and donor projections.",
        ),
        "travel_estimation": item(
            operational=True,
            mode="Google Routes" if routes_ready else "haversine fallback",
            note=(
                "Traffic-aware routing is configured."
                if routes_ready
                else "Deterministic estimates are active; configure Google Routes for traffic-aware ETAs."
            ),
        ),
        "document_intake": item(
            operational=True,
            mode="in-memory PDF/text extraction",
            note="Uploads are size/page/text bounded and are not persisted by the intake service.",
        ),
        "inventory_copilot": item(
            operational=database,
            mode="constraint optimizer + approval gate",
            note="Copilot proposes transfers; inventory changes only through explicit approval/dispatch.",
        ),
        "clinical_adapters": item(
            operational=database,
            mode="FHIR ServiceRequest + HL7v2 normalization",
            note="Payloads normalize into the same deterministic request-ingestion boundary.",
        ),
        "messaging_ingress": item(
            operational=database and bool(env.get("BLOODNET_MESSAGING_WEBHOOK_SECRET")),
            mode="HMAC-signed SMS/WhatsApp webhook",
            note="Provider events are idempotent by provider event ID.",
        ),
        "messaging_delivery": item(
            operational=notifications_enabled and notification_provider and notification_outbox,
            mode=("durable outbox + local provider HTTP adapter" if local_environment else "durable outbox + Cloud Tasks + provider HTTP adapter"),
            note=("Local provider delivery and persisted retry leases are active." if local_environment and notifications_enabled and notification_provider else "Delivery receipts and retry leases are persisted when all provider settings are present."),
        ),
    }
    ready = sum(1 for capability in capabilities.values() if capability["operational"])
    return {
        "environment": (env.get("BLOODNET_ENV") or "local").lower(),
        "summary": {"ready": ready, "total": len(capabilities)},
        "capabilities": capabilities,
    }

"""FastAPI entrypoint for BloodNet match-svc.

The service is intentionally kept as a thin HTTP boundary around the
already-tested deterministic matching/scoring functions.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import os
from pathlib import Path
import sys
from typing import Any, List, Optional
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException
from fastapi import Header, Request as FastAPIRequest
from fastapi.responses import StreamingResponse
import json
import psycopg
from queue import Empty
from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from contracts.events import EventEnvelope, EventType
from contracts.auth import Identity, case_is_in_scope, get_identity, require_resource_scope, require_role
from contracts.location import operational_region
from contracts.workload_identity import require_service_identity
from auth_api import invitation_router, organization_router, router as auth_router, auth_repo, get_current_user
from contracts.realtime import case_realtime_hub
from contracts.audit_postgres import PostgreSQLAuditRepository
from contracts.models import (
    Approval,
    ApprovalDecision,
    AuditRecord,
    BloodBank,
    Case,
    Donor,
    GeoPoint,
    Hospital,
    InventoryUnit,
    Notification,
    Recommendation,
    Request,
    CaseOutcome,
)
from projections import active_case_notifications, DemoRole, is_case_open, project_audit_records, project_case, project_case_list, project_notifications
from inventory_mutation import InventoryRepository, InventoryMutationError, InventoryUnitNotFoundError, consume_reservation, receive_transfer, transfer_inventory, release_reservation
from postgres_inventory_repository import PostgreSQLInventoryRepository
from inventory_match import find_inventory_matches, haversine_km
from approval_flow import ReservationApprovalService, ApprovalFlowError
from reservation_orchestrator import ReservationOrchestrator
from scoring import score_donors
from request_flow import match_request
from forecast_service import build_forecast_repository, forecast_response
from public_dashboard import (
    build_public_dashboard,
    public_demo_scenarios,
    run_public_demo_scenario,
)
from eligibility import EligibilityScreening


def _resolve_request_region(identity: Identity, hospital_id: str, provided_region: str | None = None) -> str | None:
    """Return the canonical hospital region from identity, organization, or organization metadata.

    This prevents any hospital-originating match request from being persisted with
    a blank region, which would otherwise hide the case from regional-admin scope
    checks in the recommendation inbox.
    """
    if isinstance(provided_region, str) and provided_region.strip():
        return provided_region.strip()
    if identity.region_id and identity.region_id.strip():
        return identity.region_id.strip()
    if identity.organization_id:
        try:
            organization = auth_repo.get_organization_by_id(UUID(identity.organization_id))
        except Exception:
            organization = None
        if organization:
            region = operational_region((organization.metadata or {}) or {})
            if isinstance(region, str) and region.strip():
                return region.strip()
    if hospital_id:
        try:
            organizations = auth_repo.list_organizations(status="active")
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
    return None


APPROVAL_DIR = Path(__file__).resolve().parents[1] / "approval-svc"
EXECUTION_DIR = Path(__file__).resolve().parents[1] / "execution-svc"
sys.path.insert(0, str(APPROVAL_DIR))
from approval_service import (
    ApprovalService,
    ApprovalServiceError,
    PostgreSQLApprovalRepository,
)
sys.path.remove(str(APPROVAL_DIR))
sys.path.insert(0, str(EXECUTION_DIR))
from execution_service import ExecutionCoordinator
sys.path.remove(str(EXECUTION_DIR))


def _resolve_request_region(identity: Identity, hospital_id: str, provided_region: str | None = None) -> str | None:
    """Normalize a request region from identity metadata or organization records.

    A request that reaches the matching engine must carry a canonical region so
    regional-admin case filtering can evaluate the request consistently.
    """
    if isinstance(provided_region, str) and provided_region.strip():
        return provided_region.strip()
    if identity.region_id and identity.region_id.strip():
        return identity.region_id.strip()
    if identity.organization_id:
        try:
            organization = auth_repo.get_organization_by_id(UUID(identity.organization_id))
        except Exception:
            organization = None
        if organization:
            region = operational_region((organization.metadata or {}) or {})
            if isinstance(region, str) and region.strip():
                return region.strip()
    if hospital_id:
        try:
            organizations = auth_repo.list_organizations(status="active")
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
    return None

SWARM_DIR = Path(__file__).resolve().parents[1] / "swarm-svc"
NOTIFY_DIR = Path(__file__).resolve().parents[1] / "notify-svc"
sys.path.insert(0, str(SWARM_DIR))
from event_handler import SwarmEventHandler
from poisson_binomial import poisson_binomial_pmf
from donor_response_repository import DonorResponseRepository
sys.path.remove(str(SWARM_DIR))
sys.path.insert(0, str(NOTIFY_DIR))
from notification_service import NotificationService
from postgres_notification_repository import PostgreSQLNotificationRepository
from postgres_notification_outbox import PostgreSQLNotificationOutbox
sys.path.remove(str(NOTIFY_DIR))

app = FastAPI(
    title="BloodNet Match Service",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(auth_router)
app.include_router(organization_router)
app.include_router(invitation_router)

@app.get("/api/v1/me")
async def api_me(identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """Compatibility endpoint exposing the canonical authenticated profile."""
    return (await get_current_user(identity)).model_dump(mode="json")


class RankBanksRequest(BaseModel):
    request: Request
    banks: List[BloodBank]
    patient_geo: GeoPoint


class ScoreDonorsRequest(BaseModel):
    donors: List[Donor]


class InventoryMatchRequest(BaseModel):
    request: Request
    hospital: Hospital
    banks: List[BloodBank]
    units: List[InventoryUnit]


class MatchRequest(BaseModel):
    request: Request
    hospital: Hospital
    banks: List[BloodBank]
    units: List[InventoryUnit]
    donors: List[Donor]
    eligible_donor_ids: list[str] = []
    eligibility_records: dict[str, EligibilityScreening] | None = None


class CancelCaseRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class EscalateCaseRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class InventoryTransferRequest(BaseModel):
    from_bank: str
    to_bank: str
    unit_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=3, max_length=500)
    case_id: str | None = None


class BloodBankInventorySyncRequest(BaseModel):
    source_system: str = Field(min_length=1, max_length=100)
    source_event_id: str = Field(min_length=1, max_length=128)
    units: list[InventoryUnit] = Field(min_length=1, max_length=1000)


class ApprovalRequest(BaseModel):
    rationale: str = "Reviewed by operations administrator"


class NetworkRedistributionRecommendationRequest(BaseModel):
    from_bank: str = Field(min_length=1, max_length=128)
    to_bank: str = Field(min_length=1, max_length=128)
    blood_group: str = Field(min_length=1, max_length=16)
    component: str = Field(min_length=1, max_length=64)
    suggested_units: int = Field(ge=1, le=100)
    snapshot_id: str | None = Field(default=None, max_length=128)


class AuditSearchRequest(BaseModel):
    search_type: str = Field(min_length=1)
    query: str = ""


class WorkflowStore:
    """Process-local adapter used by the demo API until durable storage lands."""

    def __init__(self) -> None:
        database_url = os.environ["BLOODNET_DATABASE_URL"]
        self.database_url = database_url
        self.repository = self._build_repository()
        self.outbox = PostgreSQLNotificationOutbox(database_url)
        self.approval_service = ReservationApprovalService(
            self.repository,
            outbox=self.outbox,
        )
        self.orchestrator = ReservationOrchestrator(self.approval_service)
        self.swarm = SwarmEventHandler()
        self.audit = PostgreSQLAuditRepository(database_url)
        self.donor_response_repository = DonorResponseRepository(database_url)
        self.approval_service.audit = self.audit
        self.generic_approvals = ApprovalService(
            repository=PostgreSQLApprovalRepository(database_url)
        )
        self.notifications = NotificationService(
            PostgreSQLNotificationRepository(database_url),
            audit=self.audit,
        )
        self.execution = ExecutionCoordinator(
            repository=self.repository,
            audit=self.audit,
            mobilize_donors=self.execute_mobilize_donors,
            send_donor_notification=self.execute_donor_notification,
            create_donation_drive=self.execute_donation_drive,
        )
        self.requests: dict[str, dict[str, Any]] = {}
        self.donor_responses: set[tuple[str, str]] = set()
        self._load_workflow_state()

    def _build_repository(self):
        return PostgreSQLInventoryRepository(self.database_url)

    @contextmanager
    def _workflow_connection(self):
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            yield connection

    def _load_workflow_state(self) -> None:
        with self._workflow_connection() as connection:
            cases = connection.execute(
                "SELECT payload FROM workflow_cases ORDER BY updated_at ASC, case_id ASC"
            ).fetchall()
            recommendations = connection.execute("SELECT payload FROM workflow_recommendations").fetchall()
            requests = connection.execute("SELECT request_id, payload FROM workflow_requests").fetchall()
        for row in cases:
            case = Case.model_validate(row["payload"])
            self.approval_service.cases[case.case_id] = case
        for row in recommendations:
            recommendation = Recommendation.model_validate(row["payload"])
            if recommendation.reservation_proposal:
                recommendation.case_id = recommendation.case_id or recommendation.reservation_proposal.case_id
                recommendation.request_id = recommendation.request_id or recommendation.reservation_proposal.request_id
            self.approval_service.recommendations[recommendation.rec_id] = recommendation
        self.requests = {row["request_id"]: row["payload"] for row in requests}

    def get_ranked_donors(self, case_id: str) -> list[dict[str, Any]]:
        with self._workflow_connection() as connection:
            row = connection.execute(
                "SELECT ranked_donors FROM workflow_case_projections WHERE case_id = %s",
                (case_id,),
            ).fetchone()
        return row["ranked_donors"] if row else []

    def get_swarm_status(self, case_id: str) -> dict[str, Any]:
        with self._workflow_connection() as connection:
            row = connection.execute(
                "SELECT swarm_status FROM workflow_case_projections WHERE case_id = %s",
                (case_id,),
            ).fetchone()
        return row["swarm_status"] if row else {}

    def get_case_projection_snapshot(self, case_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch list-view projection data in one round trip."""
        if not case_ids:
            return {}
        with self._workflow_connection() as connection:
            rows = connection.execute(
                """
                SELECT case_id, ranked_donors, swarm_status
                FROM workflow_case_projections
                WHERE case_id = ANY(%s)
                """,
                (case_ids,),
            ).fetchall()
        return {
            row["case_id"]: {
                "ranked_donors": row["ranked_donors"] or [],
                "swarm_status": row["swarm_status"] or {},
            }
            for row in rows
        }

    def get_case_list_snapshot(self, case_ids: list[str]) -> dict[str, Any]:
        """Read all case-list dependencies on one database connection."""
        if not case_ids:
            return {
                "projections": {},
                "notifications_by_case": {},
                "activity_by_case": {},
                "inventory_units": [],
            }
        with self._workflow_connection() as connection:
            projection_rows = connection.execute(
                """
                SELECT case_id, ranked_donors, swarm_status
                FROM workflow_case_projections
                WHERE case_id = ANY(%s)
                """,
                (case_ids,),
            ).fetchall()
            notification_rows = connection.execute(
                """
                SELECT payload FROM notifications
                WHERE payload->>'case_id' = ANY(%s)
                ORDER BY created_at, notification_id
                """,
                (case_ids,),
            ).fetchall()
            audit_rows = connection.execute(
                """
                SELECT payload FROM audit_records
                WHERE payload->>'case_id' = ANY(%s)
                ORDER BY created_at DESC, audit_id DESC
                LIMIT 500
                """,
                (case_ids,),
            ).fetchall()

        projections = {
            row["case_id"]: {
                "ranked_donors": row["ranked_donors"] or [],
                "swarm_status": row["swarm_status"] or {},
            }
            for row in projection_rows
        }
        notifications_by_case: dict[str, list[Notification]] = {}
        for row in notification_rows:
            notification = Notification.model_validate(row["payload"])
            notifications_by_case.setdefault(notification.case_id, []).append(notification)
        activity_by_case: dict[str, list[AuditRecord]] = {}
        for row in audit_rows:
            record = AuditRecord.model_validate(row["payload"])
            if record.case_id:
                activity_by_case.setdefault(record.case_id, []).append(record)
        return {
            "projections": projections,
            "notifications_by_case": notifications_by_case,
            "activity_by_case": activity_by_case,
            # Inventory has a dedicated scoped endpoint. Avoid copying an
            # entire bank inventory into every dashboard case projection.
            "inventory_units": [],
        }

    def persist_case_projection(
        self,
        case_id: str,
        *,
        ranked_donors: list[dict[str, Any]] | None = None,
        swarm_status: dict[str, Any] | None = None,
    ) -> None:
        with self._workflow_connection() as connection:
            current = connection.execute(
                "SELECT ranked_donors, swarm_status FROM workflow_case_projections WHERE case_id = %s",
                (case_id,),
            ).fetchone()
            ranked_donors = ranked_donors if ranked_donors is not None else (current["ranked_donors"] if current else [])
            swarm_status = swarm_status if swarm_status is not None else (current["swarm_status"] if current else {})
            connection.execute(
                """
                INSERT INTO workflow_case_projections (case_id, ranked_donors, swarm_status, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (case_id) DO UPDATE SET
                    ranked_donors = EXCLUDED.ranked_donors,
                    swarm_status = EXCLUDED.swarm_status,
                    updated_at = NOW()
                """,
                (case_id, Jsonb(ranked_donors), Jsonb(swarm_status)),
            )

    def persist_case(self, case: Case) -> None:
        with self._workflow_connection() as connection:
            connection.execute(
                """
                INSERT INTO workflow_cases (case_id, payload, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (case_id) DO UPDATE SET
                    payload = EXCLUDED.payload, updated_at = NOW()
                """,
                (case.case_id, Jsonb(case.model_dump(mode="json"))),
            )

    def persist_recommendation(self, recommendation: Recommendation) -> None:
        region_id = _recommendation_region(recommendation)
        if not region_id:
            raise ValueError(
                f"Operational recommendation '{recommendation.rec_id}' requires a region_id."
            )
        recommendation.region_id = region_id
        recommendation.payload = {**(recommendation.payload or {}), "region_id": region_id}
        with self._workflow_connection() as connection:
            connection.execute(
                """
                INSERT INTO workflow_recommendations (rec_id, payload, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (rec_id) DO UPDATE SET
                    payload = EXCLUDED.payload, updated_at = NOW()
                """,
                (recommendation.rec_id, Jsonb(recommendation.model_dump(mode="json"))),
            )

    def register_generic_recommendation(self, recommendation: Recommendation) -> None:
        """Make an agent/copilot recommendation available to the generic API."""
        self.approval_service.recommendations[recommendation.rec_id] = recommendation
        approval_id = f"APRV-{recommendation.rec_id}"
        if approval_id not in self.generic_approvals.records:
            self.generic_approvals.create(
                recommendation_id=recommendation.rec_id,
                request_id=recommendation.request_id or "UNKNOWN",
                case_id=recommendation.case_id or "UNKNOWN",
                approval_id=approval_id,
            )

    def persist_request(self, request_id: str, request: dict[str, Any]) -> None:
        with self._workflow_connection() as connection:
            connection.execute(
                """
                INSERT INTO workflow_requests (request_id, payload, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (request_id) DO UPDATE SET
                    payload = EXCLUDED.payload, updated_at = NOW()
                """,
                (request_id, Jsonb(request)),
            )

    def execute_mobilize_donors(
        self, recommendation: Recommendation, case: Case
    ) -> dict[str, Any]:
        ranked_donors = self.get_ranked_donors(case.case_id)
        if not ranked_donors:
            raise ValueError("MOBILIZE_DONORS requires a ranked donor pool.")
        event = EventEnvelope.case_ranked(
            case.request_id,
            case,
            ranked_donors,
            correlation_id=recommendation.rec_id,
        )
        notification_request = self.swarm.handle_case_ranked(event)
        if notification_request is None:
            status = {
                "status": "unfulfilled",
                "target_units": case.units_from_donors_remaining,
                "cohort_size": 0,
                "donors_contacted": [],
            }
            self.persist_case_projection(case.case_id, swarm_status=status)
            return {"swarm": status, "notifications": []}

        sent_events = self.notifications.handle_request(notification_request)
        status = {
            "status": "initiated",
            "target_units": case.units_from_donors_remaining,
            "cohort_size": len(notification_request.payload.donor_ids),
            "donors_contacted": notification_request.payload.donor_ids,
        }
        self.persist_case_projection(case.case_id, swarm_status=status)
        return {
            "swarm": status,
            "notifications": [event.payload.notification.notification_id for event in sent_events],
        }

    def execute_donor_notification(
        self, recommendation: Recommendation, case: Case
    ) -> dict[str, Any]:
        donor_ids = recommendation.payload.get("donor_ids") or []
        if not donor_ids:
            raise ValueError("SEND_DONOR_NOTIFICATION requires donor_ids.")
        event = EventEnvelope.notifications_requested(
            case,
            [str(donor_id) for donor_id in donor_ids],
            correlation_id=recommendation.rec_id,
        )
        sent_events = self.notifications.handle_request(event)
        return {
            "notifications": [event.payload.notification.notification_id for event in sent_events],
            "donor_ids": [str(donor_id) for donor_id in donor_ids],
        }

    def execute_donation_drive(
        self, recommendation: Recommendation, case: Case
    ) -> dict[str, Any]:
        region = str((recommendation.payload or {}).get("region") or "").strip()
        if not region:
            raise ValueError("CREATE_DONATION_DRIVE requires a region.")
        with self._workflow_connection() as connection:
            organizations = connection.execute(
                """
                SELECT id::text AS organization_id, type, metadata
                FROM organizations
                WHERE status = 'active' AND type IN ('hospital', 'blood_bank')
                """
            ).fetchall()
            donor_rows = connection.execute(
                """
                SELECT donor_id
                FROM donor_pool
                WHERE region = %s AND eligible = TRUE AND consent_contactable = TRUE
                """,
                (region,),
            ).fetchall()

        recipients: list[tuple[str, str]] = []
        normalized_region = region.casefold()
        for organization in organizations:
            metadata = organization["metadata"] or {}
            organization_region = operational_region(metadata)
            if organization_region and organization_region.casefold() == normalized_region:
                recipients.append((str(organization["type"]), str(organization["organization_id"])))
        recipients.extend(("donor", str(row["donor_id"])) for row in donor_rows)

        drive_id = f"DRIVE-{region}"
        message = (
            f"BloodNet predicts a blood shortage in {region}. "
            "Please schedule a regional blood donation drive."
        )
        notifications = [
            self.notifications.create_in_app_notification(
                notification_id=f"NOTIFY-{recommendation.rec_id}-{recipient_type}-{recipient_id}",
                case_id=drive_id,
                request_id=recommendation.request_id or drive_id,
                recipient_type=recipient_type,
                recipient_id=recipient_id,
                message=message,
            )
            for recipient_type, recipient_id in recipients
        ]
        self.audit.append(AuditRecord(
            audit_id=f"AUDIT-{recommendation.rec_id}",
            action="donation_drive_notifications_sent",
            request_id=recommendation.request_id or drive_id,
            case_id=drive_id,
            details={"region": region, "recipients": len(recipients)},
            at=datetime.now(timezone.utc),
        ))
        return {
            "drive_status": "scheduled_for_coordination",
            "region": region,
            "notifications": [notification.notification_id for notification in notifications],
            "recipients": len(recipients),
        }


workflow_store = WorkflowStore() if os.getenv("BLOODNET_DATABASE_URL") else None
forecast_repository = build_forecast_repository()


@app.get("/api/v1/public/dashboard")
def api_public_dashboard(region: str = "all") -> dict[str, Any]:
    """Return aggregated public-safe regional data without authentication."""
    return build_public_dashboard(workflow_store, forecast_repository, auth_repo, region)


@app.get("/api/v1/public/demo-scenarios")
def api_public_demo_scenarios(region: str = "all") -> dict[str, Any]:
    return {"scenarios": public_demo_scenarios(region), "simulation": True}


@app.post("/api/v1/public/demo-scenarios/{scenario_id}/run")
def api_run_public_demo_scenario(scenario_id: str, region: str = "all") -> dict[str, Any]:
    try:
        return run_public_demo_scenario(scenario_id, region)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Demo scenario was not found") from exc


def handle_donor_response(outreach_id: str, donor_id: str, response: str) -> None:
    """Persist the response, case counts and audit together across workers."""
    from contracts.donor_outcome import record_donor_response
    case_id = record_donor_response(workflow_store, outreach_id, donor_id, response)
    case_realtime_hub.publish_case(case_id, _case_view(case_id))


def _scoped_cases(identity: Identity, limit: int | None = None) -> list[dict[str, Any]]:
    # Read from PostgreSQL on each request so separate Cloud Run instances and
    # browser refreshes observe committed cases without a process restart.
    if identity.role == "hospital_coordinator" and identity.hospital_id:
        predicate, args = "r.payload->>'hospital_id' = %s", [identity.hospital_id]
    elif identity.role == "bank_admin" and identity.bank_id:
        predicate, args = "c.payload->'inventory_matches' @> %s::jsonb", [json.dumps([{"bank_id": identity.bank_id}])]
    elif identity.role == "regional_admin" and identity.region_id:
        predicate, args = "lower(r.payload->>'region') = lower(%s)", [identity.region_id.strip()]
    elif identity.role == "auditor":
        predicate, args = "TRUE", []
    else:
        return []
    with workflow_store._workflow_connection() as connection:
        rows = connection.execute(
            "SELECT c.case_id, c.payload, r.payload AS request FROM workflow_cases c "
            "JOIN workflow_requests r ON r.request_id = c.payload->>'request_id' WHERE " + predicate +
            " ORDER BY c.updated_at DESC, c.case_id DESC LIMIT %s", [*args, limit or 1000],
        ).fetchall()
    case_ids = []
    for row in rows:
        case = Case.model_validate(row["payload"])
        if not case_is_in_scope(identity, case, row["request"]):
            continue
        workflow_store.approval_service.cases[case.case_id] = case
        workflow_store.requests[case.request_id] = row["request"]
        case_ids.append(case.case_id)
    snapshot = workflow_store.get_case_list_snapshot(case_ids)
    return project_case_list([_case_view(case_id, snapshot=snapshot) for case_id in case_ids], DemoRole(identity.role))


def _scoped_case_ids(identity: Identity) -> set[str]:
    """Return case IDs whose persisted request and inventory prove scope access."""
    scoped: set[str] = set()
    for case_id, case in list(workflow_store.approval_service.cases.items()):
        request_data = workflow_store.requests.get(case.request_id, {})
        if identity.role in {"regional_admin", "auditor"} and identity.region_id:
            if request_data.get("region") != identity.region_id:
                continue
        if identity.bank_id and not any(
            match.bank_id == identity.bank_id for match in case.inventory_matches
        ):
            continue
        if identity.role == "regional_admin" and not identity.region_id:
            continue
        if identity.role == "auditor" and not identity.region_id and not identity.bank_id:
            scoped.add(case_id)
            continue
        if case_is_in_scope(identity, case, request_data):
            scoped.add(case_id)
    return scoped


def _recommendation_region(recommendation: Recommendation) -> str | None:
    """Resolve the mandatory region boundary for an operational recommendation.

    Legacy case-linked rows are safely backfilled from their persisted request.
    A case-less legacy row with no explicit region remains unscoped and is not
    exposed or actionable. Role-access approvals use a separate auth queue and
    are intentionally unaffected by this operational rule.
    """
    explicit = str(
        recommendation.region_id
        or (recommendation.payload or {}).get("region_id")
        or (recommendation.payload or {}).get("region")
        or ""
    ).strip()
    if explicit:
        return explicit
    if recommendation.case_id:
        case = workflow_store.approval_service.cases.get(recommendation.case_id)
        if case is not None:
            request = workflow_store.requests.get(case.request_id, {})
            region = str(request.get("region_id") or request.get("region") or "").strip()
            if region:
                return region
    return None


def _recommendation_is_in_scope(recommendation: Recommendation, identity: Identity) -> bool:
    """Apply the same region boundary to recommendation reads and decisions."""
    region_id = _recommendation_region(recommendation)
    if not region_id:
        return False
    if identity.role in {"regional_admin", "auditor"} and identity.region_id:
        return region_id.casefold() == identity.region_id.strip().casefold()
    if identity.role == "regional_admin":
        return False
    return True


def _scoped_bank_ids(identity: Identity) -> set[str] | None:
    """Resolve bank scope from persisted organization metadata."""
    if identity.bank_id:
        return {identity.bank_id}
    if identity.role == "bank_admin":
        raise HTTPException(status_code=403, detail="Bank identity is missing resource scope")
    if identity.role in {"regional_admin", "auditor"}:
        if identity.role == "auditor" and not identity.region_id:
            return None
        return _bank_ids_for_region(identity.region_id)
    return None


def _bank_ids_for_region(region: str | None) -> set[str]:
    if not region:
        return set()
    normalized_region = str(region).strip().casefold()
    with workflow_store._workflow_connection() as connection:
        rows = connection.execute(
            "SELECT id::text, metadata FROM organizations "
            "WHERE status = 'active' AND type = 'blood_bank'"
        ).fetchall()
    result: set[str] = set()
    for row in rows:
        metadata = row["metadata"] or {}
        configured_regions = {operational_region(metadata)}
        if any(
            value is not None and str(value).strip().casefold() == normalized_region
            for value in configured_regions
        ):
            result.add(str(metadata.get("bank_id") or row["id"]))
            result.add(str(row["id"]))
    # Persisted case matches are also an authoritative bank/region relationship,
    # including older organizations that predate region metadata.
    for case_id in _scoped_case_ids(Identity("scope-resolution", "auditor", region_id=region)):
        case = workflow_store.approval_service.cases[case_id]
        result.update(match.bank_id for match in case.inventory_matches)
    return result


def _region_for_identity(identity: Identity) -> str | None:
    if identity.region_id:
        return identity.region_id
    if not identity.organization_id:
        return None
    with workflow_store._workflow_connection() as connection:
        row = connection.execute(
            "SELECT metadata FROM organizations WHERE id::text = %s AND status = 'active'",
            (identity.organization_id,),
        ).fetchone()
    metadata = (row or {}).get("metadata") or {}
    region = operational_region(metadata)
    return region.strip() if isinstance(region, str) and region.strip() else None


def _reload_case(case_id: str) -> None:
    """Refresh mutable workflow state before acting in a different worker."""
    with workflow_store._workflow_connection() as connection:
        row = connection.execute("SELECT payload FROM workflow_cases WHERE case_id = %s", (case_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Case was not found")
        case = Case.model_validate(row["payload"])
        request = connection.execute("SELECT payload FROM workflow_requests WHERE request_id = %s", (case.request_id,)).fetchone()
        recommendations = connection.execute("SELECT payload FROM workflow_recommendations WHERE payload->>'case_id' = %s OR payload->'reservation_proposal'->>'case_id' = %s", (case_id, case_id)).fetchall()
    workflow_store.approval_service.cases[case_id] = case
    if request:
        workflow_store.requests[case.request_id] = request["payload"]
    for row in recommendations:
        recommendation = Recommendation.model_validate(row["payload"])
        workflow_store.approval_service.recommendations[recommendation.rec_id] = recommendation


def _case_view(case_id: str, *, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    if snapshot is None:
        _reload_case(case_id)
    case = workflow_store.approval_service.cases.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' was not found.")

    recommendations = [
        workflow_store.approval_service.recommendations[rec_id]
        for rec_id in case.reservation_recommendation_ids
        if rec_id in workflow_store.approval_service.recommendations
    ]
    projection = (snapshot or {}).get("projections", {}).get(case_id, {})
    notifications = (
        snapshot["notifications_by_case"].get(case_id, [])
        if snapshot is not None
        else [item for item in workflow_store.notifications.repository.all() if item.case_id == case_id]
    )
    inventory_units = snapshot["inventory_units"] if snapshot is not None else workflow_store.repository.get_all_units()
    activity = (
        snapshot["activity_by_case"].get(case_id, [])
        if snapshot is not None
        else [item for item in workflow_store.audit.all() if item.case_id == case_id]
    )
    view = {
        "case": case.model_dump(mode="json"),
        "ranked_donors": projection.get("ranked_donors", []) if snapshot is not None else workflow_store.get_ranked_donors(case_id),
        "recommendations": [recommendation.model_dump(mode="json") for recommendation in recommendations],
        "inventory": [
            unit.model_dump(mode="json")
            for unit in inventory_units
            if unit.bank_id in {match.bank_id for match in case.inventory_matches}
        ],
        "swarm": (projection.get("swarm_status", {}) if snapshot is not None else workflow_store.get_swarm_status(case_id)) or {"status": "awaiting_reservation"},
        "notifications": [item.model_dump(mode="json") for item in notifications],
        "activity": [item.model_dump(mode="json") for item in activity],
    }
    if case.request_id in workflow_store.requests:
        view["request"] = workflow_store.requests[case.request_id]
    return view


def _refresh_fulfillment_probability(case_id: str) -> None:
    case = workflow_store.approval_service.cases.get(case_id)
    if case is None:
        return
    target_units = case.donor_target_units or case.units_from_donors_remaining
    if target_units <= 0 or case.units_from_donors_fulfilled >= target_units:
        case.fulfillment_probability = 1.0
        return
    ranked_by_id = {
        donor["donor_id"]: float(donor.get("success_probability", 0.0))
        for donor in workflow_store.get_ranked_donors(case_id)
    }
    contacted = workflow_store.get_swarm_status(case_id).get("donors_contacted", [])
    probabilities = [ranked_by_id[donor_id] for donor_id in contacted if donor_id in ranked_by_id]
    if not probabilities:
        case.fulfillment_probability = 0.0
        return
    required = target_units - case.units_from_donors_fulfilled
    case.fulfillment_probability = float(poisson_binomial_pmf(probabilities)[required:].sum())


def _run_swarm(case, ranked_donors: list[dict[str, Any]], correlation_id: str) -> None:
    if os.getenv("BLOODNET_ENV") == "production":
        from contracts.outreach_policy import current_recipient_allowed
        ranked_donors = [donor for donor in ranked_donors if current_recipient_allowed(
            os.environ["BLOODNET_DATABASE_URL"], donor["donor_id"], case.request_id)]
    event = EventEnvelope.case_ranked(
        case.request_id, case, ranked_donors, correlation_id=correlation_id
    )
    notification_request = workflow_store.swarm.handle_case_ranked(event)
    if notification_request is None:
        status = {
            "status": "inventory_covered" if case.units_from_donors_remaining == 0 else "unfulfilled",
            "target_units": case.units_from_donors_remaining,
            "cohort_size": 0,
            "donors_contacted": [],
        }
        workflow_store.persist_case_projection(case.case_id, swarm_status=status)
        _refresh_fulfillment_probability(case.case_id)
        workflow_store.persist_case(case)
        case_realtime_hub.publish_case(case.case_id, _case_view(case.case_id))
        return

    donor_ids = notification_request.payload.donor_ids
    status = {
        "status": "initiated",
        "target_units": case.units_from_donors_remaining,
        "cohort_size": len(donor_ids),
        "donors_contacted": donor_ids,
    }
    workflow_store.persist_case_projection(case.case_id, swarm_status=status)
    _refresh_fulfillment_probability(case.case_id)
    workflow_store.persist_case(case)
    case_realtime_hub.publish_case(case.case_id, _case_view(case.case_id))
    sent_events = workflow_store.notifications.handle_request(notification_request)


def _resolve_hospital_donor_pool(hospital: Hospital, request=None) -> tuple[list[Donor], set[str]]:
    """Build a consented donor pool using each donor's own current location."""
    donors: list[Donor] = []
    eligible_ids: set[str] = set()
    for profile in auth_repo.list_contactable_donor_profiles():
        if request is not None:
            from contracts.outreach_policy import within_outreach_scope
            if not within_outreach_scope(profile, group=request.group.value, region=request.region, lat=hospital.geo.lat, lng=hospital.geo.lng):
                continue
        donor_id = profile["user_id"]
        donors.append(
            Donor(
                donor_id=donor_id,
                blood_group=profile["blood_group"],
                geo=GeoPoint(lat=float(profile["lat"]), lng=float(profile["lng"])),
                contact_tokens=["email"],
                consent_scopes=["contactable"],
                last_donation_at=profile.get("last_donation_at"),
            )
        )
        eligible_ids.add(donor_id)
    return donors, eligible_ids


@app.post("/api/v1/rank-banks")
def api_rank_banks(payload: RankBanksRequest, identity: Identity = Depends(get_identity)):
    require_role(identity, "hospital_coordinator", "regional_admin", "auditor")
    try:
        ranked = sorted(
            payload.banks,
            key=lambda bank: haversine_km(payload.patient_geo, bank.geo),
        )
        return {"ranked_banks": [b.model_dump(mode="json") for b in ranked]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/v1/score-donors")
def api_score_donors(payload: ScoreDonorsRequest, identity: Identity = Depends(get_identity)):
    require_role(identity, "regional_admin", "auditor")
    try:
        scores = score_donors(payload.donors)
        return {"scores": scores}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/v1/inventory-match")
def api_inventory_match(payload: InventoryMatchRequest, identity: Identity = Depends(get_identity)):
    require_role(identity, "hospital_coordinator", "bank_admin", "regional_admin", "auditor")
    try:
        result = find_inventory_matches(
            payload.request, payload.hospital, payload.banks, payload.units
        )
        return result.model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _create_match(payload: MatchRequest, identity: Identity):
    require_role(identity, "hospital_coordinator", "regional_admin")
    if not 1 <= payload.request.qty <= 1000:
        raise HTTPException(status_code=422, detail="Requested units must be between 1 and 1000")
    if payload.request.required_by.tzinfo is None:
        raise HTTPException(status_code=422, detail="Required-by time must include a timezone")
    if payload.request.hospital_id != payload.hospital.hospital_id:
        raise HTTPException(status_code=422, detail="Request and hospital identifiers must match")
    if identity.role == "regional_admin":
        if not identity.region_id or not payload.request.region or payload.request.region.casefold() != identity.region_id.casefold():
            raise HTTPException(status_code=403, detail="Request region is outside your resource scope")
    try:
        production = os.getenv("BLOODNET_ENV", "local").lower() == "production"
        if identity.role == "hospital_coordinator" and payload.request.hospital_id != identity.hospital_id:
            raise HTTPException(status_code=403, detail="Request hospital is outside your scope")
        if identity.role == "hospital_coordinator":
            if not identity.organization_id and not identity.hospital_id:
                raise HTTPException(status_code=422, detail="Hospital organization scope is missing")
            resolved_region = _resolve_request_region(identity, payload.request.hospital_id, payload.request.region)
            if not isinstance(resolved_region, str) or not resolved_region.strip():
                raise HTTPException(status_code=422, detail="Hospital regional scope is not configured. Ask a regional administrator to configure the organization region.")
            if identity.organization_id:
                try:
                    organization = auth_repo.get_organization_by_id(UUID(identity.organization_id))
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail="Hospital organization scope is invalid") from exc
                metadata = (organization.metadata if organization else None) or {}
                organization_region = operational_region(metadata)
                if not isinstance(organization_region, str) or not organization_region.strip():
                    raise HTTPException(status_code=422, detail="Hospital regional scope is not configured. Ask a regional administrator to configure the organization region.")
                if payload.request.region and payload.request.region.casefold() != organization_region.casefold():
                    raise HTTPException(status_code=403, detail="Request region is outside your hospital scope")
                # The organization, not a browser-supplied value, is the source of
                # truth for a hospital request's region.
                payload.request.region = organization_region
            elif identity.hospital_id:
                if identity.region_id and resolved_region.casefold() != identity.region_id.casefold():
                    raise HTTPException(status_code=403, detail="Request region is outside your hospital scope")
                payload.request.region = resolved_region
            else:
                payload.request.region = resolved_region
        if production:
            # Stored eligibility, facility coordinates and stock define the
            # match. Browser-provided clinical claims cannot authorize supply.
            context = api_hospital_context(Identity(identity.subject_id, "hospital_coordinator", hospital_id=payload.request.hospital_id))
            canonical_region = context["hospital"]["region"]
            if payload.request.region and payload.request.region.casefold() != canonical_region.casefold():
                raise HTTPException(status_code=403, detail="Request region is outside the hospital's resource scope")
            payload.request.region = canonical_region
            payload.hospital = Hospital.model_validate(context["hospital"])
            payload.banks = [BloodBank.model_validate(bank) for bank in context["banks"]]
            payload.units = [InventoryUnit.model_validate(unit) for unit in context["units"]]
        donor_pool = payload.donors
        eligible_ids = set(payload.eligible_donor_ids) if payload.eligible_donor_ids else None
        screening_records = payload.eligibility_records
        if production or (identity.role == "hospital_coordinator" and not donor_pool):
            donor_pool, eligible_ids = _resolve_hospital_donor_pool(payload.hospital, payload.request)
            # This pool is selected by the server from active, consenting,
            # age-eligible, non-deferred profiles. An empty browser screening
            # object must not exclude the entire trusted server-selected pool.
            screening_records = None
        try:
            payload.units = workflow_store.repository.get_units([unit.unit_id for unit in payload.units])
        except InventoryUnitNotFoundError as exc:
            raise HTTPException(status_code=422, detail="Inventory must be imported by its bank before matching") from exc
        result = match_request(
            payload.request,
            payload.hospital,
            payload.banks,
            payload.units,
            donor_pool,
            eligible_donor_ids=eligible_ids,
            eligibility_records=screening_records,
        )
        # Matching is read-only for inventory. Imports belong to the bank sync API.
        response = {
            "case": result.case.model_dump(mode="json"),
            "ranked_donors": result.ranked_donors,
        }
        request_projection = payload.request.model_dump(mode="json")
        workflow_store.requests[payload.request.request_id] = request_projection
        workflow_store.persist_request(payload.request.request_id, request_projection)
        proposal_event = workflow_store.orchestrator.handle_case_ranked(
            EventEnvelope.case_ranked(
                payload.request.request_id,
                result.case,
                result.ranked_donors,
                correlation_id=result.case.case_id,
            )
        )
        workflow_store.approval_service.cases[result.case.case_id] = result.case
        workflow_store.persist_case_projection(result.case.case_id, ranked_donors=result.ranked_donors)
        workflow_store.persist_case(result.case)
        if proposal_event is not None and proposal_event.event_type == EventType.RESERVATION_PROPOSED:
            for rec_id in result.case.reservation_recommendation_ids:
                recommendation = workflow_store.approval_service.recommendations[rec_id]
                workflow_store.persist_recommendation(recommendation)
                bank_id = recommendation.payload.get("bank_id")
                if bank_id:
                    workflow_store.notifications.create_in_app_notification(
                        notification_id=f"BANK-NOTIFY-{result.case.case_id}-{bank_id}",
                        case_id=result.case.case_id,
                        request_id=payload.request.request_id,
                        recipient_type="bank",
                        recipient_id=str(bank_id),
                        message=(f"Blood request {payload.request.request_id} needs review: "
                                 f"{len(recommendation.payload.get('unit_ids') or [])} unit(s) available for reservation."),
                    )
        case_realtime_hub.publish_case(result.case.case_id, _case_view(result.case.case_id))
        if proposal_event is not None and proposal_event.event_type == EventType.RESERVATION_PROPOSED:
            response["recommendation"] = proposal_event.payload.recommendation.model_dump(mode="json")
        elif result.case.units_from_donors_remaining > 0:
            recommendation = Recommendation(
                rec_id=f"MOBILIZE-{result.case.case_id}", type="MOBILIZE_DONORS",
                case_id=result.case.case_id, request_id=result.case.request_id,
                region_id=payload.request.region,
                payload={"target_units": result.case.units_from_donors_remaining, "region_id": payload.request.region},
                rationale="Review donor mobilization for a request without available inventory.",
                state="AWAITING_APPROVAL", provenance={"source": "deterministic_match"},
            )
            workflow_store.register_generic_recommendation(recommendation)
            workflow_store.persist_recommendation(recommendation)
            workflow_store.persist_case_projection(result.case.case_id, swarm_status={"status": "awaiting_approval", "donors_contacted": []})
            response["recommendation"] = recommendation.model_dump(mode="json")
        response["case"] = result.case.model_dump(mode="json")
        return response
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/v1/match")
def api_match(payload: MatchRequest, identity: Identity = Depends(get_identity)):
    require_role(identity, "hospital_coordinator", "regional_admin")
    with workflow_store._workflow_connection() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"match:{payload.request.request_id}",))
        row = connection.execute("SELECT payload FROM workflow_cases WHERE payload->>'request_id' = %s", (payload.request.request_id,)).fetchone()
        if row:
            case = Case.model_validate(row["payload"])
            stored = connection.execute("SELECT payload FROM workflow_requests WHERE request_id = %s", (payload.request.request_id,)).fetchone()
            request_data = stored["payload"] if stored else {}
            if not case_is_in_scope(identity, case, request_data):
                raise HTTPException(status_code=403, detail="Request ID belongs to another resource scope")
            incoming = payload.request.model_dump(mode="json")
            if any(incoming[key] != request_data.get(key) for key in ("hospital_id", "group", "component", "qty", "required_by", "urgency")):
                raise HTTPException(status_code=409, detail="Request ID was already used with different details")
            response = {"case": case.model_dump(mode="json"), "ranked_donors": workflow_store.get_ranked_donors(case.case_id)}
            if case.reservation_recommendation_ids:
                rec = connection.execute("SELECT payload FROM workflow_recommendations WHERE rec_id = %s", (case.reservation_recommendation_ids[0],)).fetchone()
                if rec:
                    response["recommendation"] = rec["payload"]
            return response
        return _create_match(payload, identity)


@app.get("/api/v1/cases")
def api_list_cases(limit: int = 200, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    return {"cases": _scoped_cases(identity, limit=max(1, min(limit, 1000)))}


@app.get("/api/v1/cases/{case_id}")
def api_get_case(case_id: str, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    view = _case_view(case_id)
    case = workflow_store.approval_service.cases[case_id]
    if not case_is_in_scope(identity, case, view.get("request", {})):
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' was not found in your scope.")
    return project_case(view, DemoRole(identity.role))


@app.get("/api/v1/cases/{case_id}/stream")
def api_stream_case(case_id: str, identity: Identity = Depends(get_identity)) -> StreamingResponse:
    view = _case_view(case_id)
    case = workflow_store.approval_service.cases[case_id]
    if not case_is_in_scope(identity, case, view.get("request", {})):
        raise HTTPException(status_code=404, detail="Case is outside your scope")
    queue = case_realtime_hub.subscribe_to_case(case_id)

    def events():
        try:
            yield f"data: {json.dumps(project_case(view, DemoRole(identity.role)))}\n\n"
            while True:
                try:
                    snapshot = queue.get(timeout=15)
                    yield f"data: {json.dumps(project_case(snapshot, DemoRole(identity.role)))}\n\n"
                except Empty:
                    yield ": heartbeat\n\n"
        finally:
            case_realtime_hub.unsubscribe_from_case(case_id, queue)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/v1/cases/{case_id}/recommendations")
def api_case_recommendations(case_id: str, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    require_role(identity, "bank_admin", "regional_admin", "auditor")
    view = _case_view(case_id)
    if not case_is_in_scope(identity, workflow_store.approval_service.cases[case_id], view.get("request", {})):
        raise HTTPException(status_code=404, detail="Case is outside your scope")
    return {"recommendations": view["recommendations"]}


@app.get("/api/v1/inventory")
def api_inventory(
    bank_id: Optional[str] = None,
    group: Optional[str] = None,
    component: Optional[str] = None,
    status: Optional[str] = None,
    identity: Identity = Depends(get_identity),
) -> dict[str, Any]:
    """Return a read-only inventory projection for the bank workspace."""
    require_role(identity, "bank_admin", "regional_admin", "auditor")
    allowed_bank_ids = _scoped_bank_ids(identity)
    if bank_id and allowed_bank_ids is not None and bank_id not in allowed_bank_ids:
        raise HTTPException(status_code=403, detail="Bank is outside your resource scope")
    if identity.role == "bank_admin" and not identity.bank_id:
        raise HTTPException(status_code=403, detail="Bank identity is missing resource scope")
    # PostgreSQL is the source of truth. Its inherited in-memory cache is only
    # populated by process-local mutations and is empty after every restart.
    units = workflow_store.repository.get_all_units()
    if allowed_bank_ids is not None:
        units = [unit for unit in units if unit.bank_id in allowed_bank_ids]
    elif bank_id:
        units = [unit for unit in units if unit.bank_id == bank_id]
    if group:
        units = [unit for unit in units if unit.group.value == group]
    if component:
        units = [unit for unit in units if unit.component.value == component]
    if status:
        units = [unit for unit in units if unit.status.value == status]
    return {
        "units": [unit.model_dump(mode="json") for unit in units],
        "total": len(units),
    }


@app.get("/api/v1/inventory/expiry-risk")
def api_inventory_expiry_risk(
    bank_id: Optional[str] = None,
    region: str | None = None,
    identity: Identity = Depends(get_identity),
) -> dict[str, Any]:
    """Calculate expiry risk from stock depth, expiry window, and forecast demand."""
    require_role(identity, "bank_admin", "regional_admin", "auditor")
    allowed_bank_ids = _scoped_bank_ids(identity)
    if bank_id and allowed_bank_ids is not None and bank_id not in allowed_bank_ids:
        raise HTTPException(status_code=403, detail="Bank is outside your resource scope")
    units = workflow_store.repository.get_all_units()
    if allowed_bank_ids is not None:
        units = [unit for unit in units if unit.bank_id in allowed_bank_ids]
    if bank_id:
        units = [unit for unit in units if unit.bank_id == bank_id]
    own_region = _region_for_identity(identity)
    if region and identity.role != "auditor" and region != own_region:
        raise HTTPException(status_code=403, detail="Region is outside your resource scope")
    region = region or own_region
    forecast = forecast_repository.get_forecast(region, 7) if region else None
    demand_by_key: dict[tuple[str, str], float] = {}
    for point in forecast.points if forecast else []:
        key = (point.blood_group, point.component or "*")
        demand_by_key[key] = demand_by_key.get(key, 0.0) + float(point.predicted_demand)
    now = datetime.now(timezone.utc)
    grouped: dict[tuple[str, str], list[InventoryUnit]] = {}
    for unit in units:
        if unit.status.value == "available":
            grouped.setdefault((unit.group.value, unit.component.value), []).append(unit)
    risks = []
    for (group, component), grouped_units in grouped.items():
        days = min(max((unit.expires_at - now).total_seconds() / 86400, 0) for unit in grouped_units)
        available = len(grouped_units)
        forecast_demand = demand_by_key.get((group, component), demand_by_key.get((group, "*"), 0.0))
        stock_depth = available / max(forecast_demand, 1.0)
        risk = "high" if days <= 3 or (days <= 7 and stock_depth > 1.5) else "medium" if days <= 7 or stock_depth > 1.0 else "low"
        risks.append({
            "group": group,
            "component": component,
            "available_units": available,
            "days_to_expiry": round(days, 1),
            "forecast_demand": round(forecast_demand, 2),
            "demand_scope": "component" if (group, component) in demand_by_key else "blood_group",
            "stock_depth": round(stock_depth, 2),
            "risk": risk,
        })
    return {"region": region, "risks": sorted(risks, key=lambda item: (item["risk"] != "high", item["days_to_expiry"]))}


@app.get("/api/v1/hospital/inventory")
def api_hospital_inventory(identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """Return read-only inventory from banks affiliated with the current hospital."""
    context = api_hospital_context(identity)
    return {"units": context["units"], "total": len(context["units"])}


@app.get("/api/v1/hospital/context")
def api_hospital_context(identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """Return the persisted hospital, affiliated banks, and inventory context."""
    require_role(identity, "hospital_coordinator")
    if not identity.hospital_id:
        raise HTTPException(status_code=422, detail="Hospital identity is missing resource scope")
    with workflow_store._workflow_connection() as connection:
        organizations = connection.execute(
            "SELECT id::text, name, type, address, metadata FROM organizations WHERE status = 'active'"
        ).fetchall()
    hospital_row = next((row for row in organizations if row["type"] == "hospital" and
                         identity.hospital_id in {row["id"], str((row["metadata"] or {}).get("hospital_id") or "")}), None)
    if not hospital_row or hospital_row["type"] != "hospital":
        raise HTTPException(status_code=404, detail="Hospital organization was not found")
    metadata = hospital_row["metadata"] or {}
    hospital_geo = metadata.get("geo")
    # New hospital organizations collect an address during onboarding.  Until a
    # verified organization location is configured, a coordinator who has
    # consented to browser location sharing can still submit an urgent request.
    # This prevents the UI intake flow from dead-ending after sign-in, without
    # inventing a location or exposing it to other roles.
    if (
        (not isinstance(hospital_geo, dict) or not {"lat", "lng"}.issubset(hospital_geo))
        and identity.location is not None
    ):
        hospital_geo = {"lat": identity.location.lat, "lng": identity.location.lng}
    if not isinstance(hospital_geo, dict) or not {"lat", "lng"}.issubset(hospital_geo):
        raise HTTPException(status_code=422, detail="Hospital location is not configured. Allow location access or ask a regional administrator to configure the organization location.")
    region = operational_region(metadata)
    if not isinstance(region, str) or not region.strip():
        raise HTTPException(status_code=422, detail="Hospital regional scope is not configured. Ask a regional administrator to configure the organization region.")
    bank_ids = set(metadata.get("affiliated_bank_ids", []))
    banks = []
    inventory_bank_ids: set[str] = set()
    for row in organizations:
        if row["type"] != "blood_bank":
            continue
        bank_metadata = row["metadata"] or {}
        # Supply outside the hospital's operational region cannot silently be
        # used as a normal proximity match.  Cross-region escalation belongs
        # in an explicit future transfer workflow.
        if (operational_region(bank_metadata) or "").casefold() != region.casefold():
            continue
        bank_id = str(bank_metadata.get("bank_id") or row["id"])
        if bank_ids and bank_id not in bank_ids and row["id"] not in bank_ids:
            continue
        geo = bank_metadata.get("geo")
        if isinstance(geo, dict) and {"lat", "lng"}.issubset(geo):
            banks.append({"bank_id": bank_id, "name": row["name"], "geo": geo, "licence_id": str(bank_metadata.get("licence_id") or bank_id)})
            inventory_bank_ids.update({bank_id, str(row["id"])})
    units = [unit.model_dump(mode="json") for unit in workflow_store.repository.get_all_units() if unit.bank_id in inventory_bank_ids]
    return {
        "hospital": {"hospital_id": identity.hospital_id, "name": hospital_row["name"], "geo": hospital_geo, "region": region, "tier": metadata.get("tier", "unspecified"), "affiliated_banks": [bank["bank_id"] for bank in banks]},
        "banks": banks,
        "units": units,
    }


@app.get("/api/v1/graph/dataset")
def api_graph_dataset(identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """Build graph inputs from scoped persisted workflow and organization data."""
    require_role(identity, "regional_admin", "auditor")
    scoped_cases = _scoped_cases(identity)
    with workflow_store._workflow_connection() as connection:
        organizations = connection.execute("SELECT id::text, name, type, metadata FROM organizations WHERE status = 'active'").fetchall()
    organization_by_id = {row["id"]: row for row in organizations}
    supply_edges = []
    donor_edges = []
    demand_by_region: dict[str, float] = {}
    inventory = workflow_store.repository.get_all_units()
    for view in scoped_cases:
        request = view.get("request", {})
        hospital_id = str(request.get("hospital_id", ""))
        hospital = organization_by_id.get(hospital_id)
        hospital_region = (hospital["metadata"] or {}).get("region_id") if hospital else None
        region = str(request.get("region") or hospital_region or identity.region_id or "unassigned")
        demand_by_region[region] = demand_by_region.get(region, 0.0) + float(request.get("qty", 0) or 0)
        case = view["case"]
        matches = case.get("inventory_matches", [])
        for match in matches:
            supply_edges.append({"bank_id": match["bank_id"], "hospital_id": hospital_id, "volume_30d": float(match["units_available"]), "avg_lead_time_hours": float(match["eta_min"]) / 60.0})
        bank_ids = [match["bank_id"] for match in matches]
        for donor in view.get("ranked_donors", []):
            for bank_id in bank_ids or [""]:
                donor_edges.append({"donor_id": donor.get("donor_id", ""), "bank_id": bank_id, "region": region, "group": donor.get("blood_group", "")})
    scoped_bank_ids = {unit.bank_id for unit in inventory if not identity.region_id or any(edge["bank_id"] == unit.bank_id for edge in supply_edges)}
    return {"supply_edges": supply_edges, "donor_edges": donor_edges, "demand_by_region": demand_by_region, "inventory": [unit.model_dump(mode="json") for unit in inventory if unit.bank_id in scoped_bank_ids]}


@app.get("/api/v1/notifications")
def api_notifications(case_id: Optional[str] = None, donor_id: Optional[str] = None, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    require_role(identity, "donor", "hospital_coordinator", "bank_admin", "regional_admin", "auditor")
    if identity.role == "donor":
        donor_id = identity.subject_id
    elif identity.role == "bank_admin" and donor_id:
        raise HTTPException(status_code=403, detail="Bank administrators cannot access donor notifications")
    notifications = workflow_store.notifications.repository.all()
    if case_id:
        notifications = [item for item in notifications if item.case_id == case_id]
    if donor_id:
        notifications = [item for item in notifications if item.donor_id == donor_id or item.recipient_id == donor_id]
    if identity.role == "hospital_coordinator":
        organization_ids = {identity.organization_id, identity.hospital_id}
        notifications = [
            item for item in notifications
            if item.recipient_type == "hospital" and item.recipient_id in organization_ids
        ]
    if identity.role == "bank_admin":
        notifications = [
            item for item in notifications
            if item.recipient_type in {"bank", "blood_bank"}
            and item.recipient_id in {identity.bank_id, identity.organization_id}
        ]
        notifications = active_case_notifications(
            [item.model_dump(mode="json") for item in notifications],
            workflow_store.approval_service.cases,
        )
        return {"notifications": project_notifications(notifications)}
    return {"notifications": project_notifications([item.model_dump(mode="json") for item in notifications])}


@app.get("/api/v1/notifications/delivery-operations")
def api_notification_delivery_operations(
    limit: int = 100,
    identity: Identity = Depends(get_identity),
) -> dict[str, Any]:
    """Expose delivery, retry, and lease state to authorized operations roles."""
    require_role(identity, "regional_admin", "auditor")
    rows = workflow_store.outbox.delivery_operations(limit)
    if not (identity.role == "auditor" and not identity.region_id and not identity.bank_id):
        scoped_case_ids = _scoped_case_ids(identity)
        rows = [row for row in rows if row.get("case_id") in scoped_case_ids]
    # Provider message IDs are operational correlation data, but notification
    # recipients and message bodies never enter this projection.
    return {"deliveries": rows}


@app.post("/api/v1/notifications/provider-receipt")
async def api_notification_provider_receipt(
    request: FastAPIRequest,
    x_bloodnet_receipt_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    """Receive provider delivery receipts with HMAC verification."""
    secret = os.getenv("BLOODNET_NOTIFICATION_RECEIPT_SECRET")
    if not secret or not x_bloodnet_receipt_signature:
        raise HTTPException(status_code=503, detail="Notification receipt integration is not configured")
    body = await request.body()
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, x_bloodnet_receipt_signature):
        raise HTTPException(status_code=401, detail="Invalid receipt signature")
    try:
        payload = json.loads(body.decode("utf-8"))
        notification = workflow_store.notifications.record_delivery_receipt(
            provider_message_id=str(payload["provider_message_id"]),
            delivery_status=str(payload["status"]),
            failure_reason=payload.get("failure_reason"),
        )
    except (KeyError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"notification": notification.model_dump(mode="json")}


class NotificationDeliveryTask(BaseModel):
    event_id: str = Field(min_length=1, max_length=512)


@app.post("/internal/notifications/deliver")
def api_internal_notification_delivery(
    payload: NotificationDeliveryTask,
    _service: dict[str, Any] = Depends(require_service_identity()),
) -> dict[str, Any]:
    """Cloud Tasks target; IAM/workload identity protects the worker boundary."""
    if not workflow_store.notifications.delivery_enabled:
        raise HTTPException(status_code=503, detail="External notification delivery is disabled")
    workflow_store.outbox.recover_expired_leases(event_id=payload.event_id)
    sent = workflow_store.notifications.deliver_pending(limit=1, event_id=payload.event_id)
    if not sent:
        with workflow_store._workflow_connection() as connection:
            row = connection.execute("SELECT status, terminal_failure FROM notification_outbox WHERE event_id = %s", (payload.event_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Notification event was not found")
        if row["status"] != "delivered" and not row["terminal_failure"]:
            raise HTTPException(status_code=503, detail="Notification is awaiting its lease or retry time", headers={"Retry-After": "30"})
    return {"delivered": len(sent)}


@app.get("/api/v1/audit")
def api_audit(case_id: Optional[str] = None, request_id: Optional[str] = None, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    require_role(identity, "auditor", "regional_admin")
    if identity.role == "auditor" and not identity.region_id and not identity.bank_id:
        records = workflow_store.audit.all()
    else:
        scoped_case_ids = _scoped_case_ids(identity)
        records = [item for item in workflow_store.audit.all() if item.case_id in scoped_case_ids]
    if case_id:
        records = [item for item in records if item.case_id == case_id]
    if request_id:
        records = [item for item in records if item.request_id == request_id]
    return {"events": project_audit_records([item.model_dump(mode="json") for item in records])}


@app.post("/api/v1/audit/search")
def api_audit_search(payload: AuditSearchRequest, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    require_role(identity, "auditor", "regional_admin")
    if identity.role == "auditor" and not identity.region_id and not identity.bank_id:
        records = workflow_store.audit.all()
    else:
        scoped_case_ids = _scoped_case_ids(identity)
        records = [item for item in workflow_store.audit.all() if item.case_id in scoped_case_ids]
    field_map = {
        "case_id": "case_id",
        "request_id": "request_id",
        "actor": "actor",
        "event_type": "action",
        "recommendation_id": None,
        "delivery_status": None,
        "citation": None,
    }
    field = field_map.get(payload.search_type)
    query = payload.query.strip().lower()
    if payload.search_type == "recommendation_id" and query:
        records = [item for item in records if query in str(item.details.get("recommendation_id", item.details.get("rec_id", ""))).lower()]
    elif payload.search_type == "delivery_status" and query:
        records = [item for item in records if query in str(item.details.get("delivery_status", "")).lower()]
    elif payload.search_type == "citation" and query:
        records = [item for item in records if query in json.dumps(item.details, default=str).lower()]
    elif field and query:
        records = [item for item in records if query in str(getattr(item, field, "")).lower()]
    return {"events": project_audit_records([item.model_dump(mode="json") for item in records])}


@app.get("/api/v1/audit/activity")
def api_audit_activity(identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    require_role(identity, "hospital_coordinator", "bank_admin", "regional_admin", "auditor", "donor")
    if identity.role == "auditor" and not identity.region_id and not identity.bank_id:
        records = workflow_store.audit.all()
    elif identity.role in {"regional_admin", "auditor", "bank_admin"}:
        records = [item for item in workflow_store.audit.all() if item.case_id in _scoped_case_ids(identity)]
    else:
        records = workflow_store.audit.all()
    return {"events": project_audit_records([item.model_dump(mode="json") for item in records[-20:]])}


@app.get("/api/v1/me/session")
def api_session(identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    user = auth_repo.get_user_by_identity_subject(identity.subject_id)
    if user is None:
        try:
            user = auth_repo.get_user_by_id(UUID(identity.subject_id))
        except ValueError:
            user = None
    return {
        "session": {
            "mfa_enabled": user.mfa_enabled if user else None,
            "email_verified": user.email_verified if user else None,
            "recovery_email": user.email if user else identity.email,
            "last_signin": None,
        }
    }


@app.get("/api/v1/privacy/policies")
def api_privacy_policies(identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    require_role(identity, "hospital_coordinator", "bank_admin", "regional_admin", "auditor", "donor")
    return {"policies": [
        "No donor PII is exposed in hospital or regional workspaces.",
        "Only the minimum required fields are rendered for operational decisions.",
        "Investigation traces are redacted outside audit or admin scopes.",
        "Recommendation and case detail views mask unnecessary donor identifiers.",
    ]}


@app.get("/api/v1/regional/forecast")
def api_regional_forecast(region: str | None = None, horizon_days: int = 7, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    require_role(identity, "regional_admin", "auditor")
    region = region or identity.region_id
    if not region:
        raise HTTPException(status_code=422, detail="region is required when the identity has no regional scope")
    require_resource_scope(identity, region, scope_type="region", label="region")
    if identity.role == "regional_admin" and (not identity.region_id or region != identity.region_id):
        raise HTTPException(status_code=403, detail="Region is outside your resource scope")
    if not 1 <= horizon_days <= 14:
        raise HTTPException(status_code=422, detail="horizon_days must be between 1 and 14")
    result = forecast_repository.get_forecast(region, horizon_days)
    if result is not None:
        from forecast_service import apply_live_inventory_supply, forecast_is_stale
        # Keep a historical forecast internally consistent. Mixing today's
        # inventory into an older run would make its probabilities misleading.
        if not forecast_is_stale(result):
            result = apply_live_inventory_supply(
                result,
                workflow_store.repository.get_all_units(),
                _bank_ids_for_region(region),
            )
    response = forecast_response(result, region, horizon_days)
    _ensure_forecast_drive_recommendation(region, response)
    return response


def _ensure_forecast_drive_recommendation(region: str, forecast: dict[str, Any]) -> Recommendation | None:
    shortage_points = [
        point for point in forecast.get("forecast", [])
        if float(point.get("shortage_probability", 0) or 0) >= 0.5
    ]
    if not shortage_points:
        return None
    run_id = str(forecast.get("forecast_run_id") or "unknown")
    rec_id = "DRIVE-" + hashlib.sha256(f"{region}|{run_id}".encode("utf-8")).hexdigest()[:18]
    existing = workflow_store.approval_service.recommendations.get(rec_id)
    if existing is not None:
        return existing
    recommendation = Recommendation(
        rec_id=rec_id,
        type="CREATE_DONATION_DRIVE",
        region_id=region,
        request_id=f"FORECAST-{run_id}",
        payload={
            "region": region,
            "region_id": region,
            "forecast_run_id": run_id,
            "shortage_points": shortage_points,
            "notification_only": True,
        },
        rationale=f"Forecasted shortage pressure in {region} warrants a regional donation drive notification.",
        expected_impact={"notification_only": True, "shortage_points": len(shortage_points)},
        provenance={"source": "forecast_agent", "approval_required": True},
        state="AWAITING_APPROVAL",
    )
    workflow_store.register_generic_recommendation(recommendation)
    workflow_store.persist_recommendation(recommendation)
    return recommendation


@app.get("/api/v1/network/health")
def api_network_health(identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """Return server-derived network health metrics for operational roles."""
    require_role(identity, "regional_admin", "auditor")
    scoped_ids = _scoped_case_ids(identity)
    scoped_cases = [workflow_store.approval_service.cases[case_id] for case_id in scoped_ids]
    active = [
        case for case in scoped_cases
        if getattr(case.outcome, "value", case.outcome) not in {"fulfilled", "cancelled", "unfulfilled"}
    ]
    active_ids = [case.case_id for case in active]
    projections = workflow_store.get_case_projection_snapshot(active_ids)
    required_units = sum(
        int(workflow_store.requests.get(case.request_id, {}).get("qty", 0))
        for case in active
    )
    covered_units = sum(int(case.units_from_inventory) for case in active)
    swarms = [projections.get(case.case_id, {}).get("swarm_status", {}) for case in active]
    deliveries = [
        row for row in workflow_store.outbox.delivery_operations(250)
        if row.get("case_id") in scoped_ids
    ]
    return {
        "data_status": "available" if active else "no_active_cases",
        "active_cases": len(active),
        "fulfillment_probability": round(
            sum(float(case.fulfillment_probability) for case in active) / len(active),
            4,
        ) if active else None,
        "shortage_exposure": sum(int(case.units_from_donors_remaining) for case in active),
        "inventory_coverage": round(covered_units / required_units, 4) if required_units else None,
        "donor_activity": sum(len(swarm.get("donors_contacted", [])) for swarm in swarms),
        "escalated_cases": sum(1 for case in active if case.escalation_state == "required"),
        "notifications_pending": sum(1 for row in deliveries if row.get("status") in {"pending", "processing"}),
        "notifications_failed": sum(1 for row in deliveries if row.get("terminal_failure")),
        "notifications_delivered": sum(1 for row in deliveries if str(row.get("delivery_status", "")).lower() in {"delivered", "success", "completed"}),
        "status": "healthy" if not active or all(case.escalation_state != "required" for case in active) else "under_pressure",
    }


@app.get("/api/v1/forecast")
def api_forecast(region: str | None = None, horizon_days: int = 7, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    return api_regional_forecast(region, horizon_days, identity)


@app.get("/api/v1/regional/swarms")
def api_regional_swarms(identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    require_role(identity, "regional_admin")
    scoped_case_ids = _scoped_case_ids(identity)
    projections = workflow_store.get_case_projection_snapshot(list(scoped_case_ids))
    swarms = []
    for case_id in scoped_case_ids:
        case = workflow_store.approval_service.cases.get(case_id)
        if case is None or getattr(case.outcome, "value", case.outcome) in {"fulfilled", "cancelled", "unfulfilled"}:
            continue
        status = projections.get(case_id, {}).get("swarm_status", {})
        if status.get("status") not in {"initiated", "active", "pending"} or case.units_from_donors_remaining <= 0:
            continue
        swarms.append({"case_id": case_id, **status})
    return {"swarms": swarms}


@app.get("/api/v1/inventory/{unit_id}")
def api_inventory_unit(unit_id: str, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    require_role(identity, "bank_admin", "regional_admin", "auditor")
    try:
        unit = workflow_store.repository.get_unit(unit_id)
    except InventoryMutationError:
        raise HTTPException(status_code=404, detail=f"Inventory unit '{unit_id}' was not found.")
    allowed_bank_ids = _scoped_bank_ids(identity)
    if allowed_bank_ids is not None and unit.bank_id not in allowed_bank_ids:
        raise HTTPException(status_code=404, detail="Inventory unit is outside your scope")
    reservation = next(
        (item for item in workflow_store.repository.get_reservations() if unit_id in item.unit_ids),
        None,
    )
    return {
        **unit.model_dump(mode="json"),
        "reservation": reservation.model_dump(mode="json") if reservation else None,
    }


@app.post("/api/v1/integrations/blood-bank/inventory")
def api_sync_blood_bank_inventory(payload: BloodBankInventorySyncRequest, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """Idempotently synchronize canonical units from an external blood bank system."""
    require_role(identity, "bank_admin")
    if not identity.bank_id:
        raise HTTPException(status_code=403, detail="Bank identity is missing resource scope")
    if any(unit.bank_id != identity.bank_id for unit in payload.units):
        raise HTTPException(status_code=403, detail="Inventory unit is outside your bank scope")
    if len({unit.unit_id for unit in payload.units}) != len(payload.units):
        raise HTTPException(status_code=422, detail="Inventory update contains duplicate unit IDs")
    fingerprint = hashlib.sha256(json.dumps(
        sorted((unit.model_dump(mode="json") for unit in payload.units), key=lambda unit: unit["unit_id"]),
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    with workflow_store.repository.transaction() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                           (f"inventory-sync:{payload.source_system}:{payload.source_event_id}",))
        existing = connection.execute(
            "SELECT payload FROM integration_events WHERE source_system = %s AND source_event_id = %s",
            (payload.source_system, payload.source_event_id),
        ).fetchone()
        if existing:
            if existing["payload"].get("bank_id") != identity.bank_id or existing["payload"].get("fingerprint") != fingerprint:
                raise HTTPException(status_code=409, detail="Source event ID was already used with different inventory details")
            return {"status": "already_synchronized", "source_system": payload.source_system, "source_event_id": payload.source_event_id}
        for unit in sorted(payload.units, key=lambda item: item.unit_id):
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"inventory-unit:{unit.unit_id}",))
            try:
                current = workflow_store.repository.get_units_for_update([unit.unit_id])[0]
            except InventoryUnitNotFoundError:
                current = None
            if current is not None:
                if current.bank_id != identity.bank_id:
                    raise HTTPException(status_code=403, detail="Existing inventory unit belongs to another bank")
                if current.model_dump() == unit.model_dump():
                    continue
                if current.status.value != "available" or unit.status.value not in {"available", "discarded"}:
                    raise HTTPException(status_code=409, detail="Allocated inventory must use reservation or transfer workflows")
                if current.model_dump(exclude={"status"}) != unit.model_dump(exclude={"status"}):
                    raise HTTPException(status_code=409, detail="Existing unit identity and collection details cannot be overwritten")
            elif unit.status.value not in {"available", "discarded"}:
                raise HTTPException(status_code=409, detail="New inventory cannot start in an allocated state")
            if unit.expires_at.tzinfo is None or unit.collected_at.tzinfo is None:
                raise HTTPException(status_code=422, detail="Inventory timestamps must include a timezone")
            if unit.expires_at <= unit.collected_at:
                raise HTTPException(status_code=422, detail="Unit expiry must follow collection")
            if unit.collected_at > datetime.now(timezone.utc):
                raise HTTPException(status_code=422, detail="A blood unit cannot be recorded before it is collected")
            if unit.status.value == "available" and unit.expires_at <= datetime.now(timezone.utc):
                raise HTTPException(status_code=422, detail="Expired inventory cannot be made available")
            workflow_store.repository.save_unit(unit)
        connection.execute(
            "INSERT INTO integration_events (source_system, source_event_id, event_type, payload) VALUES (%s, %s, 'inventory.sync', %s)",
            (payload.source_system, payload.source_event_id, Jsonb({"unit_ids": [unit.unit_id for unit in payload.units], "bank_id": identity.bank_id, "fingerprint": fingerprint})),
        )
    workflow_store.audit.append(AuditRecord(
        audit_id=f"AUDIT-INTEGRATION-{payload.source_system}-{payload.source_event_id}",
        action="blood_bank_inventory_synchronized",
        request_id=payload.source_event_id,
        case_id=payload.source_event_id,
        actor=identity.subject_id,
        at=datetime.now(timezone.utc),
        details={"source_system": payload.source_system, "unit_count": len(payload.units), "bank_id": identity.bank_id},
    ))
    return {"status": "synchronized", "source_system": payload.source_system, "source_event_id": payload.source_event_id, "unit_count": len(payload.units)}


@app.post("/api/v1/inventory/transfers")
def api_transfer_inventory(payload: InventoryTransferRequest, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """Move available units into an idempotent, auditable in-transit transfer."""
    require_role(identity, "bank_admin", "regional_admin")
    require_resource_scope(identity, payload.from_bank, scope_type="bank", label="source bank")
    try:
        transfer = transfer_inventory(
            workflow_store.repository,
            transfer_id=f"TR-{uuid4().hex}",
            from_bank=payload.from_bank,
            to_bank=payload.to_bank,
            unit_ids=payload.unit_ids,
            reason=payload.reason,
            case_id=payload.case_id,
        )
    except InventoryMutationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    workflow_store.audit.append(AuditRecord(
        audit_id=f"AUDIT-TRANSFER-{transfer.transfer_id}",
        action="inventory_transfer_started",
        request_id=transfer.transfer_id,
        case_id=transfer.transfer_id,
        actor=identity.subject_id,
        at=datetime.now(timezone.utc),
        details=transfer.model_dump(mode="json"),
    ))
    return {"transfer": transfer.model_dump(mode="json")}


@app.post("/api/v1/inventory/reservations/{reservation_id}/consume")
def api_consume_inventory_reservation(reservation_id: str, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """Issue reserved units as the controlled allocation step."""
    require_role(identity, "bank_admin", "regional_admin")
    try:
        reservation = workflow_store.repository.get_reservation(reservation_id)
        require_resource_scope(identity, reservation.bank_id, scope_type="bank", label="bank")
        consumed = consume_reservation(workflow_store.repository, reservation_id)
    except InventoryMutationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    workflow_store.audit.append(AuditRecord(
        audit_id=f"AUDIT-ALLOCATE-{reservation_id}-{identity.subject_id}",
        action="inventory_allocation_completed",
        request_id=consumed.request_id,
        case_id=consumed.case_id,
        actor=identity.subject_id,
        at=datetime.now(timezone.utc),
        details={"reservation_id": reservation_id, "unit_ids": consumed.unit_ids},
    ))
    return {"reservation": consumed.model_dump(mode="json")}


@app.post("/api/v1/inventory/transfers/{transfer_id}/receive")
def api_receive_inventory_transfer(transfer_id: str, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """Confirm receipt and assign transferred units to the destination bank."""
    require_role(identity, "bank_admin", "regional_admin")
    try:
        transfer = workflow_store.repository.get_transfer(transfer_id)
        if transfer is None:
            raise HTTPException(status_code=404, detail=f"Transfer '{transfer_id}' was not found.")
        require_resource_scope(identity, transfer.to_bank, scope_type="bank", label="destination bank")
        received = receive_transfer(workflow_store.repository, transfer_id)
        # Receipt at another bank is not hospital fulfillment. Allocation and
        # hospital receipt must use the reservation and outcome workflows.
    except HTTPException:
        raise
    except InventoryMutationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    workflow_store.audit.append(AuditRecord(
        audit_id=f"AUDIT-TRANSFER-RECEIVE-{transfer_id}",
        action="inventory_transfer_received",
        request_id=transfer_id,
        case_id=transfer_id,
        actor=identity.subject_id,
        at=datetime.now(timezone.utc),
        details=received.model_dump(mode="json"),
    ))
    return {"transfer": received.model_dump(mode="json")}


@app.get("/api/v1/reservations/pending")
def api_pending_reservations(bank_id: Optional[str] = None, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    require_role(identity, "bank_admin", "regional_admin", "auditor")
    allowed_bank_ids = _scoped_bank_ids(identity)
    if bank_id and allowed_bank_ids is not None and bank_id not in allowed_bank_ids:
        raise HTTPException(status_code=403, detail="Bank is outside your resource scope")
    scoped_case_ids = _scoped_case_ids(identity)
    recommendations = []
    for recommendation in workflow_store.approval_service.recommendations.values():
        if recommendation.state != "AWAITING_APPROVAL":
            continue
        payload = recommendation.payload or {}
        if isinstance(payload, dict):
            payload_bank_id = payload.get("bank_id")
        else:
            payload_bank_id = getattr(payload, "bank_id", None)
        if allowed_bank_ids is not None and payload_bank_id not in allowed_bank_ids:
            continue
        if bank_id and payload_bank_id != bank_id:
            continue
        if recommendation.case_id and allowed_bank_ids is not None and recommendation.case_id not in scoped_case_ids:
            continue
        if recommendation.case_id:
            case = workflow_store.approval_service.cases.get(recommendation.case_id)
            if case is None or not is_case_open(case):
                continue
        recommendations.append(recommendation.model_dump(mode="json"))
    return {"recommendations": recommendations}


@app.get("/api/v1/reservations")
def api_reservations(bank_id: Optional[str] = None, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """List reservations visible to the authenticated bank or regional operator."""
    require_role(identity, "bank_admin", "regional_admin", "auditor")
    allowed_bank_ids = _scoped_bank_ids(identity)
    if bank_id and allowed_bank_ids is not None and bank_id not in allowed_bank_ids:
        raise HTTPException(status_code=403, detail="Bank is outside your resource scope")
    reservations = []
    for reservation in workflow_store.repository.get_reservations():
        if allowed_bank_ids is not None and reservation.bank_id not in allowed_bank_ids:
            continue
        if bank_id and reservation.bank_id != bank_id:
            continue
        reservations.append(reservation.model_dump(mode="json"))
    return {"reservations": reservations}


def _decide_reservation(case_id: str, rec_id: str, decision: ApprovalDecision, payload: ApprovalRequest, identity: Identity) -> dict[str, Any]:
    with workflow_store._workflow_connection() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"case:{case_id}",))
        return _decide_reservation_locked(case_id, rec_id, decision, payload, identity)


def _decide_reservation_locked(case_id: str, rec_id: str, decision: ApprovalDecision, payload: ApprovalRequest, identity: Identity) -> dict[str, Any]:
    require_role(identity, "bank_admin")
    view = _case_view(case_id)
    if not case_is_in_scope(identity, workflow_store.approval_service.cases[case_id], view.get("request", {})):
        raise HTTPException(status_code=404, detail="Case is outside your bank scope")
    recommendation = next(
        (Recommendation.model_validate(item) for item in view["recommendations"] if item["rec_id"] == rec_id),
        None,
    )
    if recommendation is None:
        raise HTTPException(status_code=404, detail=f"Recommendation '{rec_id}' was not found.")
    if recommendation.reservation_proposal is None or recommendation.reservation_proposal.bank_id != identity.bank_id:
        raise HTTPException(status_code=403, detail="Reservation proposal belongs to another bank")
    if not is_case_open(workflow_store.approval_service.cases[case_id]):
        raise HTTPException(status_code=409, detail="Case is already closed")

    approval = Approval(
        approval_id=f"APP-{uuid4().hex}",
        rec_id=rec_id,
        actor=identity.subject_id,
        decision=decision,
        rationale=payload.rationale,
        at=datetime.now(timezone.utc),
    )
    event = EventEnvelope.approval_decided(
        approval,
        recommendation,
        ranked_donors=view["ranked_donors"],
        correlation_id=case_id,
    )
    try:
        result = workflow_store.orchestrator.handle_approval(event)
    except InventoryMutationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, ApprovalFlowError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=409, detail="Approval has already been processed.")
    if result.event_type == EventType.CASE_RANKED:
        _run_swarm(result.payload.case, result.payload.ranked_donors, case_id)
    workflow_store.persist_case(result.payload.case)
    workflow_store.persist_recommendation(workflow_store.approval_service.recommendations[rec_id])
    updated_view = _case_view(case_id)
    if result.event_type != EventType.CASE_RANKED:
        case_realtime_hub.publish_case(case_id, updated_view)
    return {"event_type": result.event_type.value, **updated_view}


@app.post("/api/v1/cases/{case_id}/reservations/{rec_id}/approve")
def api_approve_reservation(case_id: str, rec_id: str, payload: ApprovalRequest, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    return _decide_reservation(case_id, rec_id, ApprovalDecision.APPROVE, payload, identity)


@app.post("/api/v1/cases/{case_id}/reservations/{rec_id}/reject")
def api_reject_reservation(case_id: str, rec_id: str, payload: ApprovalRequest, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    return _decide_reservation(case_id, rec_id, ApprovalDecision.REJECT, payload, identity)


@app.get("/api/v1/recommendations")
def api_recommendations(state: str | None = None, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    require_role(identity, "bank_admin", "regional_admin", "auditor")
    recommendations = []
    for recommendation in workflow_store.approval_service.recommendations.values():
        if state and recommendation.state != state:
            continue
        if recommendation.type == "INVENTORY_RESERVATION":
            continue
        if identity.role == "bank_admin" and recommendation.type != "TRANSFER_INVENTORY":
            continue
        # Every operational approval is region-bound. Region-less legacy rows
        # are quarantined; role-access approvals live in the auth approval queue.
        if not _recommendation_is_in_scope(recommendation, identity):
            continue
        if recommendation.case_id:
            case = workflow_store.approval_service.cases.get(recommendation.case_id)
            # A recommendation can only be approved when its case is still in
            # the active workflow. Do not expose stale records that would
            # inevitably fail the approve endpoint with a 404.
            if case is None:
                continue
            if not is_case_open(case):
                continue
            if not case_is_in_scope(identity, case, workflow_store.requests.get(case.request_id, {})):
                continue
        recommendations.append(recommendation.model_dump(mode="json"))
        workflow_store.register_generic_recommendation(recommendation)
    return {"recommendations": recommendations}


@app.post("/api/v1/network/redistribution-recommendations")
def api_create_redistribution_recommendation(
    payload: NetworkRedistributionRecommendationRequest,
    identity: Identity = Depends(get_identity),
) -> dict[str, Any]:
    """Turn a graph finding into a reviewable action without moving inventory."""
    require_role(identity, "regional_admin")
    allowed_banks = _scoped_bank_ids(identity) or set()
    if payload.from_bank not in allowed_banks or payload.to_bank not in allowed_banks:
        raise HTTPException(status_code=403, detail="Redistribution route is outside your regional scope")
    if payload.from_bank == payload.to_bank:
        raise HTTPException(status_code=422, detail="Source and destination banks must differ")

    with workflow_store._workflow_connection() as connection:
        rows = connection.execute(
            """
            SELECT unit_id
            FROM inventory_units
            WHERE payload->>'bank_id' = %s
              AND payload->>'group' = %s
              AND payload->>'component' = %s
              AND payload->>'status' = 'available'
            ORDER BY payload->>'expires_at', unit_id
            LIMIT %s
            """,
            (payload.from_bank, payload.blood_group, payload.component, payload.suggested_units),
        ).fetchall()
    unit_ids = [str(row["unit_id"]) for row in rows]
    if len(unit_ids) < payload.suggested_units:
        raise HTTPException(status_code=409, detail="The suggested source no longer has enough available inventory")

    case: Case | None = None
    for candidate in reversed(list(workflow_store.approval_service.cases.values())):
        request = workflow_store.requests.get(candidate.request_id, {})
        if not case_is_in_scope(identity, candidate, request):
            continue
        if str(request.get("group") or "") != payload.blood_group or str(request.get("component") or "") != payload.component:
            continue
        case = candidate
        break
    if case is None:
        raise HTTPException(status_code=409, detail="No active regional case can receive this redistribution recommendation")

    fingerprint = "|".join([
        payload.from_bank, payload.to_bank, payload.blood_group,
        payload.component, *unit_ids,
    ])
    rec_id = "NET-TRANSFER-" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:18]
    existing = workflow_store.approval_service.recommendations.get(rec_id)
    if existing is not None:
        return {"recommendation": existing.model_dump(mode="json"), "created": False}
    recommendation = Recommendation(
        rec_id=rec_id,
        type="TRANSFER_INVENTORY",
        region_id=identity.region_id,
        request_id=case.request_id,
        case_id=case.case_id,
        payload={
            "from_bank": payload.from_bank,
            "to_bank": payload.to_bank,
            "unit_ids": unit_ids,
            "transfer_id": f"TRANSFER-{rec_id}",
            "reason": "Approved pre-shortage balancing from regional network intelligence",
            "region_id": identity.region_id,
        },
        rationale=(
            f"Move {len(unit_ids)} {payload.blood_group} {payload.component} unit(s) "
            f"from {payload.from_bank} to {payload.to_bank} before projected pressure."
        ),
        expected_impact={"units_rebalanced": len(unit_ids), "destination_bank": payload.to_bank},
        provenance={
            "source": "regional_network_intelligence",
            "analysis_snapshot_id": payload.snapshot_id,
            "approval_required": True,
        },
        state="AWAITING_APPROVAL",
    )
    workflow_store.register_generic_recommendation(recommendation)
    workflow_store.persist_recommendation(recommendation)
    workflow_store.audit.append(AuditRecord(
        audit_id=f"AUDIT-{rec_id}",
        action="network_redistribution_recommended",
        request_id=case.request_id,
        case_id=case.case_id,
        actor=identity.subject_id,
        at=datetime.now(timezone.utc),
        details={
            "recommendation_id": rec_id,
            "from_bank": payload.from_bank,
            "to_bank": payload.to_bank,
            "blood_group": payload.blood_group,
            "component": payload.component,
            "suggested_units": len(unit_ids),
            "analysis_snapshot_id": payload.snapshot_id,
        },
    ))
    return {"recommendation": recommendation.model_dump(mode="json"), "created": True}


def _decide_generic_recommendation(
    rec_id: str, decision: ApprovalDecision, payload: ApprovalRequest, identity: Identity
) -> dict[str, Any]:
    require_role(identity, "bank_admin", "regional_admin")
    recommendation = workflow_store.approval_service.recommendations.get(rec_id)
    if recommendation is None or recommendation.type == "INVENTORY_RESERVATION":
        raise HTTPException(status_code=404, detail=f"Recommendation '{rec_id}' was not found.")
    if identity.role == "bank_admin" and recommendation.type != "TRANSFER_INVENTORY":
        raise HTTPException(status_code=404, detail=f"Recommendation '{rec_id}' was not found.")
    if not _recommendation_is_in_scope(recommendation, identity):
        raise HTTPException(status_code=404, detail="Recommendation is outside your regional scope")
    case_exists = bool(recommendation.case_id)
    if recommendation.case_id:
        case = workflow_store.approval_service.cases.get(recommendation.case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="Recommendation case was not found.")
        if not case_is_in_scope(identity, case, workflow_store.requests.get(case.request_id, {})):
            raise HTTPException(status_code=404, detail="Recommendation is outside your resource scope")
    workflow_store.register_generic_recommendation(recommendation)
    record_id = f"APRV-{rec_id}"
    try:
        if decision == ApprovalDecision.REJECT:
            record = workflow_store.generic_approvals.reject(record_id, actor=identity.subject_id, reason=payload.rationale)
            recommendation.state = "REJECTED"
            workflow_store.persist_recommendation(recommendation)
            return {"recommendation": recommendation.model_dump(mode="json"), "approval": record.model_dump(mode="json"), "execution": None}
        record = workflow_store.generic_approvals.approve(record_id, actor=identity.subject_id)
        recommendation.state = "APPROVED"
        workflow_store.generic_approvals.mark_executing(record_id)
        recommendation.state = "EXECUTING"
        case = workflow_store.approval_service.cases.get(
            recommendation.case_id or "",
            Case(case_id=recommendation.case_id or "UNKNOWN", request_id=recommendation.request_id or "UNKNOWN"),
        )
        approval = Approval(
            approval_id=record.approval_id, rec_id=rec_id, actor=identity.subject_id,
            decision=ApprovalDecision.APPROVE, rationale=payload.rationale,
            at=datetime.now(timezone.utc),
        )
        try:
            execution = workflow_store.execution.execute_approved(recommendation, approval=approval, case=case)
        except Exception:
            workflow_store.generic_approvals.mark_failed(record_id)
            recommendation.state = "FAILED"
            workflow_store.persist_recommendation(recommendation)
            raise
        if execution.get("escalation_required"):
            workflow_store.generic_approvals.mark_partial(record_id)
            recommendation.state = "PARTIAL"
            escalation = Recommendation.model_validate(execution["escalation_recommendation"])
            workflow_store.register_generic_recommendation(escalation)
            workflow_store.persist_recommendation(escalation)
        else:
            workflow_store.generic_approvals.mark_executed(record_id)
            recommendation.state = "EXECUTED"
        if case_exists:
            workflow_store.persist_case(case)
        workflow_store.persist_recommendation(recommendation)
        updated_case = _case_view(case.case_id) if case_exists else None
        if updated_case is not None:
            case_realtime_hub.publish_case(case.case_id, updated_case)
        return {
            "recommendation": recommendation.model_dump(mode="json"),
            "approval": workflow_store.generic_approvals.get(record_id).model_dump(mode="json"),
            "execution": execution,
            "case": updated_case,
        }
    except (ApprovalServiceError, ValueError, InventoryMutationError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v1/recommendations/{rec_id}/approve")
def api_approve_recommendation(rec_id: str, payload: ApprovalRequest, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    return _decide_generic_recommendation(rec_id, ApprovalDecision.APPROVE, payload, identity)


@app.post("/api/v1/recommendations/{rec_id}/reject")
def api_reject_recommendation(rec_id: str, payload: ApprovalRequest, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    return _decide_generic_recommendation(rec_id, ApprovalDecision.REJECT, payload, identity)


class HospitalOutcomeRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=100)
    inventory_units_received: int = Field(ge=0, le=1000)
    donor_units_received: int = Field(ge=0, le=1000)
    reference: str = Field(min_length=3, max_length=200)


@app.post("/api/v1/cases/{case_id}/outcomes")
def api_record_hospital_outcome(case_id: str, payload: HospitalOutcomeRequest, identity: Identity = Depends(get_identity)):
    """Record cumulative physically received units, independently of donor commitments."""
    require_role(identity, "hospital_coordinator")
    with workflow_store._workflow_connection() as connection:
        row = connection.execute("SELECT payload FROM workflow_cases WHERE case_id = %s FOR UPDATE", (case_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Case was not found")
        case = Case.model_validate(row["payload"])
        request_row = connection.execute("SELECT payload FROM workflow_requests WHERE request_id = %s", (case.request_id,)).fetchone()
        request = request_row["payload"] if request_row else {}
        if not case_is_in_scope(identity, case, request):
            raise HTTPException(status_code=404, detail="Case is outside your hospital scope")
        details = payload.model_dump()
        previous = case.outcome_confirmations.get(payload.event_id)
        if previous is not None:
            if previous != details:
                raise HTTPException(status_code=409, detail="Outcome event ID was already used with different details")
            return {"case": case.model_dump(mode="json"), "status": "already_recorded"}
        if case.outcome in {CaseOutcome.FULFILLED, CaseOutcome.CANCELLED}:
            raise HTTPException(status_code=409, detail="Case is already closed")
        total = payload.inventory_units_received + payload.donor_units_received
        if total > int(request.get("qty", 0)):
            raise HTTPException(status_code=422, detail="Received units exceed requested quantity")
        if payload.inventory_units_received < case.confirmed_inventory_units or payload.donor_units_received < case.confirmed_donor_units:
            raise HTTPException(status_code=409, detail="Cumulative receipt counts cannot decrease")
        if payload.donor_units_received > case.units_from_donors_fulfilled:
            raise HTTPException(status_code=409, detail="Donor receipt exceeds recorded donor commitments")
        issued = 0
        for reservation_id in case.reservation_ids:
            reservation = workflow_store.repository.get_reservation(reservation_id)
            if reservation.status.value == "consumed":
                issued += len(reservation.unit_ids)
        if payload.inventory_units_received > issued:
            raise HTTPException(status_code=409, detail="Inventory must be issued by its bank before hospital receipt")
        case.confirmed_inventory_units = payload.inventory_units_received
        case.confirmed_donor_units = payload.donor_units_received
        case.outcome_confirmations[payload.event_id] = details
        case.outcome = CaseOutcome.PARTIALLY_FULFILLED if total else CaseOutcome.PENDING
        case.escalation_state = "ready_for_closure" if total == int(request.get("qty", 0)) else "required"
        connection.execute("UPDATE workflow_cases SET payload = %s, updated_at = NOW() WHERE case_id = %s", (Jsonb(case.model_dump(mode="json")), case_id))
        with connection.cursor() as cursor:
            workflow_store.audit.append(AuditRecord(
                audit_id=f"AUDIT-RECEIPT-{case_id}-{payload.event_id}", action="hospital_receipt_confirmed",
                request_id=case.request_id, case_id=case_id, actor=identity.subject_id,
                at=datetime.now(timezone.utc), details=details,
            ), cursor=cursor)
    workflow_store.approval_service.cases[case_id] = case
    view = _case_view(case_id)
    case_realtime_hub.publish_case(case_id, view)
    return project_case(view, DemoRole(identity.role))


@app.post("/api/v1/cases/{case_id}/fulfill")
def api_fulfill_case(case_id: str, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """Record hospital fulfillment only after all required units are covered."""
    require_role(identity, "hospital_coordinator")
    view = _case_view(case_id)
    case = workflow_store.approval_service.cases[case_id]
    if not case_is_in_scope(identity, case, view.get("request", {})):
        raise HTTPException(status_code=404, detail="Case is outside your hospital scope")
    covered = case.confirmed_inventory_units + case.confirmed_donor_units
    required = int(view.get("request", {}).get("qty") or (case.units_from_inventory + case.donor_target_units))
    if case.outcome == CaseOutcome.CANCELLED:
        raise HTTPException(status_code=409, detail="Cancelled cases cannot be fulfilled")
    if case.units_from_inventory and case.reservation_state != "reserved":
        raise HTTPException(status_code=409, detail="Inventory must be reserved before fulfillment")
    if covered < required:
        raise HTTPException(status_code=409, detail="Hospital-confirmed receipts remain short of required units")
    case.outcome = CaseOutcome.FULFILLED
    workflow_store.persist_case(case)
    workflow_store.audit.append(
        AuditRecord(
            audit_id=f"AUDIT-FULFILL-{case.case_id}-{identity.subject_id}",
            action="hospital_fulfillment_confirmed",
            request_id=case.request_id,
            case_id=case.case_id,
            actor=identity.subject_id,
            at=datetime.now(timezone.utc),
        )
    )
    updated_view = _case_view(case_id)
    case_realtime_hub.publish_case(case_id, updated_view)
    return project_case(updated_view, DemoRole(identity.role))


@app.post("/api/v1/cases/{case_id}/cancel")
def api_cancel_case(case_id: str, payload: CancelCaseRequest, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """Cancel an open hospital case with an auditable reason."""
    require_role(identity, "hospital_coordinator")
    view = _case_view(case_id)
    case = workflow_store.approval_service.cases[case_id]
    if not case_is_in_scope(identity, case, view.get("request", {})):
        raise HTTPException(status_code=404, detail="Case is outside your hospital scope")
    if case.outcome in (CaseOutcome.FULFILLED, CaseOutcome.CANCELLED):
        raise HTTPException(status_code=409, detail="Case is already closed")
    with workflow_store.repository.transaction():
        for reservation_id in case.reservation_ids:
            reservation = workflow_store.repository.get_reservation(reservation_id)
            if reservation.status.value == "reserved":
                release_reservation(workflow_store.repository, reservation_id)
    case.outcome = CaseOutcome.CANCELLED
    case.escalation_state = "cancelled"
    workflow_store.persist_case(case)
    workflow_store.audit.append(
        AuditRecord(
            audit_id=f"AUDIT-CANCEL-{case.case_id}-{identity.subject_id}",
            action="case_cancelled",
            request_id=case.request_id,
            case_id=case.case_id,
            actor=identity.subject_id,
            at=datetime.now(timezone.utc),
            details={"reason": payload.reason},
        )
    )
    updated_view = _case_view(case_id)
    case_realtime_hub.publish_case(case_id, updated_view)
    return project_case(updated_view, DemoRole(identity.role))


@app.post("/api/v1/cases/{case_id}/escalate")
def api_escalate_case(case_id: str, payload: EscalateCaseRequest, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """Record a manual escalation for an open case and expose the next action."""
    require_role(identity, "hospital_coordinator", "bank_admin", "regional_admin")
    case = workflow_store.approval_service.cases.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case was not found")
    view = _case_view(case_id)
    if not case_is_in_scope(identity, case, view.get("request", {})):
        raise HTTPException(status_code=404, detail="Case is outside your resource scope")
    if case.outcome in (CaseOutcome.FULFILLED, CaseOutcome.CANCELLED):
        raise HTTPException(status_code=409, detail="Case is already closed")
    case.escalation_state = "required"
    remaining_shortfall = max(
        int(case.units_from_donors_remaining or 0),
        int(case.donor_target_units or 0) - int(case.units_from_donors_fulfilled or 0),
    )
    recommendation_id = f"ESC-{case.case_id}-{remaining_shortfall}"
    recommendation = workflow_store.approval_service.recommendations.get(recommendation_id)
    if recommendation is None:
        recommendation = Recommendation(
            rec_id=recommendation_id,
            type="MOBILIZE_DONORS",
            region_id=str(view.get("request", {}).get("region") or "").strip() or None,
            request_id=case.request_id,
            case_id=case.case_id,
            payload={
                "target_units": remaining_shortfall,
                "trigger": "manual_escalation",
                "parent_case_id": case.case_id,
                "region_id": str(view.get("request", {}).get("region") or "").strip(),
            },
            rationale=payload.reason.strip(),
            expected_impact={"units_needed": remaining_shortfall},
            provenance={
                "source": "manual_case_escalation",
                "approval_required": True,
            },
            state="AWAITING_APPROVAL",
        )
        workflow_store.register_generic_recommendation(recommendation)
        workflow_store.persist_recommendation(recommendation)
    workflow_store.persist_case(case)
    workflow_store.audit.append(AuditRecord(
        audit_id=f"AUDIT-ESCALATE-{case_id}-{identity.subject_id}",
        action="case_escalated",
        request_id=case.request_id,
        case_id=case_id,
        actor=identity.subject_id,
        at=datetime.now(timezone.utc),
        details={
            "reason": payload.reason,
            "remaining_shortfall": remaining_shortfall,
            "recommendation_id": recommendation_id,
        },
    ))
    updated_view = _case_view(case_id)
    case_realtime_hub.publish_case(case_id, updated_view)
    return project_case(updated_view, DemoRole(identity.role))


@app.get("/health")
def health_check():
    return {"status": "ok"}

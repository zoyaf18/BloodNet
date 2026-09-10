"""Execution coordinator for approved BloodNet recommendations.

This is the approval-gated bridge between Gemini-generated recommendations and the
persistent deterministic services that are allowed to mutate state. Approved
recommendations are executed here, but only through a narrow allowlist of
supported actions.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable

from contracts.audit import AuditRepository
from contracts.models import (
    Approval,
    ApprovalDecision,
    AuditRecord,
    Case,
    CaseOutcome,
    ExecutionRecord,
    ExecutionStatus,
    Recommendation,
)
from inventory_mutation import InventoryRepository, release_reservation, reserve_inventory, transfer_inventory


ALLOWED_ACTIONS = {
    "RESERVE_INVENTORY",
    "INVENTORY_RESERVATION",
    "SEND_DONOR_NOTIFICATION",
    "MOBILIZE_DONORS",
    "CREATE_DONATION_DRIVE",
    "TRANSFER_INVENTORY",
}


class InMemoryExecutionRepository:
    def __init__(self) -> None:
        self.records: dict[str, ExecutionRecord] = {}

    def get(self, execution_id: str) -> ExecutionRecord | None:
        return self.records.get(execution_id)

    def save(self, record: ExecutionRecord) -> None:
        self.records[record.execution_id] = record


class PostgreSQLExecutionRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def get(self, execution_id: str) -> ExecutionRecord | None:
        import psycopg

        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                "SELECT payload FROM execution_records WHERE execution_id = %s",
                (execution_id,),
            ).fetchone()
        return ExecutionRecord.model_validate(row[0]) if row else None

    def save(self, record: ExecutionRecord) -> None:
        import psycopg
        from psycopg.types.json import Jsonb

        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """
                INSERT INTO execution_records (
                    execution_id, recommendation_id, approval_id, request_id,
                    case_id, status, payload, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (execution_id) DO NOTHING
                """,
                (
                    record.execution_id,
                    record.recommendation_id,
                    record.approval_id,
                    record.request_id,
                    record.case_id,
                    record.status,
                    Jsonb(record.model_dump(mode="json")),
                    record.created_at,
                    record.updated_at,
                ),
            )


class ExecutionCoordinator:
    """Execute only allowlisted, approval-gated actions."""

    def __init__(
        self,
        repository: InventoryRepository | None = None,
        execution_repository: InMemoryExecutionRepository | PostgreSQLExecutionRepository | None = None,
        audit: AuditRepository | None = None,
        mobilize_donors: Callable[[Recommendation, Case], dict[str, Any]] | None = None,
        send_donor_notification: Callable[[Recommendation, Case], dict[str, Any]] | None = None,
        create_donation_drive: Callable[[Recommendation, Case], dict[str, Any]] | None = None,
    ) -> None:
        repository_was_supplied = repository is not None
        self.repository = repository or InventoryRepository()
        database_url = getattr(self.repository, "_database_url", None)
        if not repository_was_supplied:
            database_url = database_url or os.getenv("BLOODNET_DATABASE_URL")
        self.execution_repository = execution_repository or (
            PostgreSQLExecutionRepository(database_url) if database_url else InMemoryExecutionRepository()
        )
        self.audit = audit
        self.mobilize_donors = mobilize_donors
        self.send_donor_notification = send_donor_notification
        self.create_donation_drive = create_donation_drive

    def _finalize_result(
        self,
        result: dict[str, Any],
        *,
        case: Case,
        recommendation: Recommendation,
        approval: Approval,
    ) -> dict[str, Any]:
        execution_id = f"EXEC-{approval.approval_id}"
        remaining_shortfall = int(getattr(case, "units_from_donors_remaining", 0) or 0)
        outcome_pending = bool(result.get("outcome_pending"))
        result["execution_id"] = execution_id
        result["shortfall_after_execution"] = remaining_shortfall
        result["remaining_shortfall"] = remaining_shortfall
        result["escalation_required"] = remaining_shortfall > 0 and not outcome_pending
        result["escalation_next_step"] = (
            "new_recommendation" if remaining_shortfall > 0 else None
        )

        if outcome_pending:
            case.outcome = CaseOutcome.PENDING
            case.escalation_state = "awaiting_outcome"
            result["escalation_next_step"] = None
        elif remaining_shortfall > 0:
            case.outcome = CaseOutcome.PARTIALLY_FULFILLED
            case.escalation_state = "required"
            result["escalation_recommendation"] = {
                "rec_id": f"ESC-{recommendation.rec_id}-{remaining_shortfall}",
                "type": "MOBILIZE_DONORS",
                "request_id": case.request_id,
                "case_id": case.case_id,
                "payload": {
                    "target_units": remaining_shortfall,
                    "trigger": "partial_execution",
                    "parent_recommendation_id": recommendation.rec_id,
                },
                "state": "AWAITING_APPROVAL",
            }
        else:
            case.outcome = CaseOutcome.PARTIALLY_FULFILLED
            case.escalation_state = "awaiting_hospital_confirmation"

        now = datetime.now(timezone.utc)
        record = ExecutionRecord(
            execution_id=execution_id,
            recommendation_id=recommendation.rec_id,
            approval_id=approval.approval_id,
            request_id=case.request_id,
            case_id=case.case_id,
            status=result["status"],
            result=dict(result),
            created_at=now,
            updated_at=now,
        )
        self.execution_repository.save(record)
        if self.audit is not None:
            self.audit.append(AuditRecord(
                audit_id=f"AUDIT-{execution_id}",
                action="execution_completed",
                request_id=case.request_id,
                case_id=case.case_id,
                actor=approval.actor,
                details={
                    "execution_id": execution_id,
                    "recommendation_id": recommendation.rec_id,
                    "approval_id": approval.approval_id,
                    "remaining_shortfall": remaining_shortfall,
                },
                at=now,
            ))

        return result

    def _record_failure(
        self,
        error: Exception,
        *,
        case: Case,
        recommendation: Recommendation,
        approval: Approval,
        reservation_id: str | None,
    ) -> dict[str, Any]:
        execution_id = f"EXEC-{approval.approval_id}"
        rollback_status = "not_required"
        rollback_error = None
        if reservation_id is not None:
            try:
                release_reservation(self.repository, reservation_id)
                rollback_status = "completed"
            except Exception as rollback_exception:
                rollback_status = "failed"
                rollback_error = str(rollback_exception)

        case.outcome = CaseOutcome.UNFULFILLED
        case.escalation_state = "execution_failed"
        result: dict[str, Any] = {
            "status": ExecutionStatus.FAILED.value,
            "execution_id": execution_id,
            "recommendation_id": recommendation.rec_id,
            "approval_id": approval.approval_id,
            "case_id": case.case_id,
            "failure_reason": str(error),
            "rollback_status": rollback_status,
            "escalation_required": True,
            "escalation_next_step": "new_recommendation",
            "escalation_recommendation": {
                "rec_id": f"ESC-{recommendation.rec_id}-failure",
                "type": "MOBILIZE_DONORS",
                "request_id": case.request_id,
                "case_id": case.case_id,
                "payload": {
                    "target_units": case.units_from_donors_remaining,
                    "trigger": "execution_failure",
                    "parent_recommendation_id": recommendation.rec_id,
                },
                "state": "AWAITING_APPROVAL",
            },
        }
        if rollback_error is not None:
            result["rollback_error"] = rollback_error

        now = datetime.now(timezone.utc)
        self.execution_repository.save(ExecutionRecord(
            execution_id=execution_id,
            recommendation_id=recommendation.rec_id,
            approval_id=approval.approval_id,
            request_id=case.request_id,
            case_id=case.case_id,
            status=ExecutionStatus.FAILED.value,
            result=result,
            failure_reason=str(error),
            rollback_status=rollback_status,
            created_at=now,
            updated_at=now,
        ))
        if self.audit is not None:
            self.audit.append(AuditRecord(
                audit_id=f"AUDIT-{execution_id}",
                action="execution_failed",
                request_id=case.request_id,
                case_id=case.case_id,
                actor=approval.actor,
                details=result,
                at=now,
            ))
        return result

    def execute_approved(
        self,
        recommendation: Recommendation,
        *,
        approval: Approval | None = None,
        case: Case | None = None,
    ) -> dict[str, Any]:
        if recommendation.state != "APPROVED":
            raise ValueError(
                f"Recommendation '{recommendation.rec_id}' is not approved and cannot be executed."
            )

        if approval is None:
            raise ValueError("An approval record is required before execution.")

        if approval.rec_id != recommendation.rec_id:
            raise ValueError("Approval does not match the recommendation being executed.")

        if approval.decision != ApprovalDecision.APPROVE:
            raise ValueError("Only an approved decision can be executed.")

        action_type = recommendation.type
        if action_type not in ALLOWED_ACTIONS:
            raise ValueError(
                f"Recommendation '{recommendation.rec_id}' action '{action_type}' is not allowlisted."
            )

        if case is None:
            case = Case(case_id=recommendation.case_id or "UNKNOWN", request_id=recommendation.request_id or "UNKNOWN")

        execution_id = f"EXEC-{approval.approval_id}"
        previous = self.execution_repository.get(execution_id)
        if previous is not None:
            return dict(previous.result)

        executed_actions: list[str] = []
        reservation_id: str | None = None
        try:
            if action_type == "CREATE_DONATION_DRIVE":
                executed_actions.append(action_type)
                drive_result = (
                    self.create_donation_drive(recommendation, case)
                    if self.create_donation_drive is not None
                    else {"notification_plan": recommendation.payload}
                )
                result = {
                    "status": "executed",
                    "recommendation_id": recommendation.rec_id,
                    "approval_id": approval.approval_id,
                    "case_id": case.case_id,
                    "executed_actions": executed_actions,
                    "outcome_pending": True,
                    "result": drive_result,
                    "execution_id": execution_id,
                    "shortfall_after_execution": 0,
                    "remaining_shortfall": 0,
                    "escalation_required": False,
                    "escalation_next_step": None,
                }
                now = datetime.now(timezone.utc)
                self.execution_repository.save(ExecutionRecord(
                    execution_id=execution_id,
                    recommendation_id=recommendation.rec_id,
                    approval_id=approval.approval_id,
                    request_id=case.request_id,
                    case_id=case.case_id,
                    status=result["status"],
                    result=dict(result),
                    created_at=now,
                    updated_at=now,
                ))
                return result

            if action_type in {"RESERVE_INVENTORY", "INVENTORY_RESERVATION"}:
                payload = recommendation.payload or {}
                bank_id = payload.get("bank_id")
                unit_ids = payload.get("unit_ids") or []
                if not bank_id or not unit_ids:
                    raise ValueError("RESERVE_INVENTORY requires bank_id and unit_ids.")
                requested_reservation_id = str(payload.get("reservation_id") or f"RES-{case.case_id}-{bank_id}")
                reservation = reserve_inventory(
                    self.repository,
                    case_id=case.case_id,
                    request_id=case.request_id,
                    bank_id=str(bank_id),
                    unit_ids=[str(unit_id) for unit_id in unit_ids],
                    reservation_id=requested_reservation_id,
                )
                reservation_id = reservation.reservation_id
                executed_actions.append(action_type)
                case.units_from_inventory = len(reservation.unit_ids)
                case.units_from_donors_remaining = max(case.units_from_donors_remaining - len(reservation.unit_ids), 0)
                return self._finalize_result({
                    "status": "executed",
                    "recommendation_id": recommendation.rec_id,
                    "approval_id": approval.approval_id,
                    "case_id": case.case_id,
                    "reservation_id": reservation.reservation_id,
                    "executed_actions": executed_actions,
                    "result": {"reservation": reservation.model_dump(mode="json")},
                }, case=case, recommendation=recommendation, approval=approval)

            if action_type == "SEND_DONOR_NOTIFICATION":
                executed_actions.append(action_type)
                notification_result = (
                    self.send_donor_notification(recommendation, case)
                    if self.send_donor_notification is not None
                    else {"notification_plan": recommendation.payload}
                )
                return self._finalize_result({
                    "status": "executed",
                    "recommendation_id": recommendation.rec_id,
                    "approval_id": approval.approval_id,
                    "case_id": case.case_id,
                    "executed_actions": executed_actions,
                    "outcome_pending": self.send_donor_notification is not None,
                    "result": notification_result,
                }, case=case, recommendation=recommendation, approval=approval)

            if action_type == "MOBILIZE_DONORS":
                executed_actions.append(action_type)
                swarm_result = (
                    self.mobilize_donors(recommendation, case)
                    if self.mobilize_donors is not None
                    else {"swarm_plan": recommendation.payload}
                )
                return self._finalize_result({
                    "status": "executed",
                    "recommendation_id": recommendation.rec_id,
                    "approval_id": approval.approval_id,
                    "case_id": case.case_id,
                    "executed_actions": executed_actions,
                    "outcome_pending": self.mobilize_donors is not None,
                    "result": swarm_result,
                }, case=case, recommendation=recommendation, approval=approval)

            if action_type == "TRANSFER_INVENTORY":
                payload = recommendation.payload or {}
                from_bank = str(payload.get("from_bank") or "")
                to_bank = str(payload.get("to_bank") or "")
                unit_ids = [str(unit_id) for unit_id in (payload.get("unit_ids") or [])]
                if not from_bank or not to_bank or not unit_ids:
                    raise ValueError("TRANSFER_INVENTORY requires from_bank, to_bank, and unit_ids.")
                transfer = transfer_inventory(
                    self.repository,
                    transfer_id=str(payload.get("transfer_id") or f"TRANSFER-{approval.approval_id}"),
                    from_bank=from_bank,
                    to_bank=to_bank,
                    unit_ids=unit_ids,
                    reason=str(payload.get("reason") or recommendation.rationale or "Approved inventory transfer"),
                    case_id=case.case_id,
                )
                executed_actions.append(action_type)
                return self._finalize_result({
                    "status": "executed",
                    "recommendation_id": recommendation.rec_id,
                    "approval_id": approval.approval_id,
                    "case_id": case.case_id,
                    "executed_actions": executed_actions,
                    "outcome_pending": True,
                    "result": {"transfer": transfer.model_dump(mode="json"), "status": "in_transit"},
                }, case=case, recommendation=recommendation, approval=approval)

            raise ValueError(
                f"Recommendation '{recommendation.rec_id}' action '{action_type}' is not allowlisted."
            )
        except Exception as error:
            return self._record_failure(
                error,
                case=case,
                recommendation=recommendation,
                approval=approval,
                reservation_id=reservation_id,
            )


class ExecutionService(ExecutionCoordinator):
    """Backward-compatible alias for the coordinator."""

    pass

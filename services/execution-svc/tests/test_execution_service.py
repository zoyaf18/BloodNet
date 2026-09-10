from datetime import datetime, timedelta, timezone

import pytest

from contracts.models import (
    Approval,
    ApprovalDecision,
    BloodGroup,
    Case,
    CaseOutcome,
    Component,
    InventoryStatus,
    InventoryUnit,
    Recommendation,
)
from contracts.audit import AuditRepository
from execution_service import ExecutionCoordinator, ExecutionService
from execution_service import InMemoryExecutionRepository
from inventory_mutation import InventoryRepository


NOW = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)


def make_unit(unit_id: str = "UNIT-EXEC-1") -> InventoryUnit:
    return InventoryUnit(
        unit_id=unit_id,
        bank_id="BANK-1",
        group=BloodGroup.A_POS,
        component=Component.RBC,
        collected_at=NOW - timedelta(days=2),
        expires_at=NOW + timedelta(days=30),
    )


def test_execution_coordinator_reserves_inventory_for_approved_recommendation():
    repository = InventoryRepository(units=[make_unit()])
    coordinator = ExecutionCoordinator(repository=repository)
    case = Case(case_id="CASE-EXEC", request_id="REQ-EXEC", units_from_inventory=0, units_from_donors_remaining=1)
    recommendation = Recommendation(
        rec_id="REC-EXEC-1",
        type="RESERVE_INVENTORY",
        request_id="REQ-EXEC",
        case_id="CASE-EXEC",
        payload={"bank_id": "BANK-1", "unit_ids": ["UNIT-EXEC-1"], "reservation_id": "RES-EXEC-1"},
        rationale="Inventory check confirms a valid reservation is needed.",
        state="APPROVED",
    )
    approval = Approval(
        approval_id="APP-EXEC-1",
        rec_id="REC-EXEC-1",
        actor="admin-1",
        decision=ApprovalDecision.APPROVE,
        rationale="Approved by ops",
        at=NOW,
    )

    result = coordinator.execute_approved(recommendation, approval=approval, case=case)

    assert result["status"] == "executed"
    assert result["executed_actions"] == ["RESERVE_INVENTORY"]
    assert repository.get_unit("UNIT-EXEC-1").status == InventoryStatus.RESERVED


def test_execution_coordinator_schedules_notification_only_donation_drive():
    observed = []
    coordinator = ExecutionCoordinator(
        execution_repository=InMemoryExecutionRepository(),
        create_donation_drive=lambda recommendation, case: observed.append(
            (recommendation.payload["region"], case.case_id)
        ) or {"recipients": 3},
    )
    recommendation = Recommendation(
        rec_id="REC-DRIVE-1",
        type="CREATE_DONATION_DRIVE",
        request_id="FORECAST-RUN-1",
        payload={"region": "Pune", "notification_only": True},
        state="APPROVED",
    )
    approval = Approval(
        approval_id="APPROVAL-DRIVE-1",
        rec_id=recommendation.rec_id,
        actor="regional-admin-1",
        decision=ApprovalDecision.APPROVE,
        rationale="Approve regional shortage outreach",
        at=datetime.now(timezone.utc),
    )

    result = coordinator.execute_approved(recommendation, approval=approval)

    assert observed == [("Pune", "UNKNOWN")]
    assert result["executed_actions"] == ["CREATE_DONATION_DRIVE"]
    assert result["result"] == {"recipients": 3}
    assert result["escalation_required"] is False


def test_execution_coordinator_recalculates_shortfall_after_execution():
    repository = InventoryRepository(units=[make_unit("UNIT-EXEC-2"), make_unit("UNIT-EXEC-3")])
    coordinator = ExecutionCoordinator(repository=repository)
    case = Case(case_id="CASE-EXEC-2", request_id="REQ-EXEC-2", units_from_inventory=0, units_from_donors_remaining=3)
    recommendation = Recommendation(
        rec_id="REC-EXEC-2",
        type="RESERVE_INVENTORY",
        request_id="REQ-EXEC-2",
        case_id="CASE-EXEC-2",
        payload={"bank_id": "BANK-1", "unit_ids": ["UNIT-EXEC-2", "UNIT-EXEC-3"], "reservation_id": "RES-EXEC-2"},
        rationale="Reserve the available inventory and reduce the donor shortfall.",
        state="APPROVED",
    )
    approval = Approval(
        approval_id="APP-EXEC-2",
        rec_id="REC-EXEC-2",
        actor="admin-1",
        decision=ApprovalDecision.APPROVE,
        rationale="Approved",
        at=NOW,
    )

    result = coordinator.execute_approved(recommendation, approval=approval, case=case)

    assert result["shortfall_after_execution"] == 1
    assert case.units_from_inventory == 2
    assert case.units_from_donors_remaining == 1


def test_execution_is_idempotent_and_audited():
    repository = InventoryRepository(units=[make_unit("UNIT-EXEC-IDEMPOTENT")])
    execution_repository = InMemoryExecutionRepository()
    audit = AuditRepository()
    coordinator = ExecutionCoordinator(
        repository=repository,
        execution_repository=execution_repository,
        audit=audit,
    )
    case = Case(case_id="CASE-EXEC-IDEMPOTENT", request_id="REQ-EXEC-IDEMPOTENT", units_from_donors_remaining=1)
    recommendation = Recommendation(
        rec_id="REC-EXEC-IDEMPOTENT",
        type="RESERVE_INVENTORY",
        request_id=case.request_id,
        case_id=case.case_id,
        payload={"bank_id": "BANK-1", "unit_ids": ["UNIT-EXEC-IDEMPOTENT"], "reservation_id": "RES-EXEC-IDEMPOTENT"},
        state="APPROVED",
    )
    approval = Approval(
        approval_id="APP-EXEC-IDEMPOTENT",
        rec_id=recommendation.rec_id,
        actor="admin-1",
        decision=ApprovalDecision.APPROVE,
        rationale="Approved",
        at=NOW,
    )

    first = coordinator.execute_approved(recommendation, approval=approval, case=case)
    second = coordinator.execute_approved(recommendation, approval=approval, case=case)

    assert second == first
    assert len(execution_repository.records) == 1
    assert [record.action for record in audit.all()] == ["execution_completed"]


def test_execution_coordinator_escalates_when_shortfall_remains_after_execution():
    repository = InventoryRepository(units=[make_unit("UNIT-EXEC-4"), make_unit("UNIT-EXEC-5")])
    coordinator = ExecutionCoordinator(repository=repository)
    case = Case(case_id="CASE-EXEC-4", request_id="REQ-EXEC-4", units_from_inventory=0, units_from_donors_remaining=3)
    recommendation = Recommendation(
        rec_id="REC-EXEC-4",
        type="RESERVE_INVENTORY",
        request_id="REQ-EXEC-4",
        case_id="CASE-EXEC-4",
        payload={"bank_id": "BANK-1", "unit_ids": ["UNIT-EXEC-4"], "reservation_id": "RES-EXEC-4"},
        rationale="Partially cover the request and keep the remainder open for escalation.",
        state="APPROVED",
    )
    approval = Approval(
        approval_id="APP-EXEC-4",
        rec_id="REC-EXEC-4",
        actor="admin-1",
        decision=ApprovalDecision.APPROVE,
        rationale="Approved",
        at=NOW,
    )

    result = coordinator.execute_approved(recommendation, approval=approval, case=case)

    assert result["escalation_required"] is True
    assert result["escalation_next_step"] == "new_recommendation"
    assert result["remaining_shortfall"] == 2
    assert result["escalation_recommendation"] == {
        "rec_id": "ESC-REC-EXEC-4-2",
        "type": "MOBILIZE_DONORS",
        "request_id": "REQ-EXEC-4",
        "case_id": "CASE-EXEC-4",
        "payload": {
            "target_units": 2,
            "trigger": "partial_execution",
            "parent_recommendation_id": "REC-EXEC-4",
        },
        "state": "AWAITING_APPROVAL",
    }


def test_execution_coordinator_rejects_non_allowlisted_actions():
    repository = InventoryRepository(units=[make_unit("UNIT-EXEC-6")])
    coordinator = ExecutionCoordinator(repository=repository)
    case = Case(case_id="CASE-EXEC-6", request_id="REQ-EXEC-6", outcome=CaseOutcome.PENDING)
    recommendation = Recommendation(
        rec_id="REC-EXEC-6",
        type="DELETE_DATA",
        request_id="REQ-EXEC-6",
        case_id="CASE-EXEC-6",
        payload={"reason": "malicious"},
        rationale="This should never be executed.",
        state="APPROVED",
    )
    approval = Approval(
        approval_id="APP-EXEC-6",
        rec_id="REC-EXEC-6",
        actor="admin-1",
        decision=ApprovalDecision.APPROVE,
        rationale="Approved",
        at=NOW,
    )

    with pytest.raises(ValueError, match="allowlisted"):
        coordinator.execute_approved(recommendation, approval=approval, case=case)


def test_approved_mobilization_invokes_handler_and_waits_for_outcome():
    repository = InventoryRepository()
    calls = []

    def mobilize(recommendation, case):
        calls.append((recommendation.rec_id, case.case_id))
        return {"notifications": ["NOTIFY-1"]}

    coordinator = ExecutionCoordinator(repository=repository, mobilize_donors=mobilize)
    case = Case(case_id="CASE-MOBILIZE", request_id="REQ-MOBILIZE", units_from_donors_remaining=2)
    recommendation = Recommendation(
        rec_id="REC-MOBILIZE",
        type="MOBILIZE_DONORS",
        request_id=case.request_id,
        case_id=case.case_id,
        payload={"target_units": 2},
        state="APPROVED",
    )
    approval = Approval(
        approval_id="APP-MOBILIZE",
        rec_id=recommendation.rec_id,
        actor="admin-1",
        decision=ApprovalDecision.APPROVE,
        rationale="Approved",
        at=NOW,
    )

    result = coordinator.execute_approved(recommendation, approval=approval, case=case)

    assert calls == [(recommendation.rec_id, case.case_id)]
    assert result["result"] == {"notifications": ["NOTIFY-1"]}
    assert result["outcome_pending"] is True
    assert result["escalation_required"] is False
    assert case.outcome is CaseOutcome.PENDING
    assert case.escalation_state == "awaiting_outcome"


def test_approved_transfer_waits_for_receipt_before_recalculation():
    from contracts.models import BloodBank, GeoPoint

    repository = InventoryRepository(units=[make_unit("UNIT-TRANSFER")])
    coordinator = ExecutionCoordinator(repository=repository)
    case = Case(case_id="CASE-TRANSFER", request_id="REQ-TRANSFER", units_from_donors_remaining=1)
    recommendation = Recommendation(
        rec_id="REC-TRANSFER",
        type="TRANSFER_INVENTORY",
        request_id=case.request_id,
        case_id=case.case_id,
        payload={
            "from_bank": "BANK-1",
            "to_bank": "BANK-2",
            "unit_ids": ["UNIT-TRANSFER"],
        },
        state="APPROVED",
    )
    approval = Approval(
        approval_id="APP-TRANSFER",
        rec_id=recommendation.rec_id,
        actor="admin-1",
        decision=ApprovalDecision.APPROVE,
        rationale="Approved",
        at=NOW,
    )

    result = coordinator.execute_approved(recommendation, approval=approval, case=case)

    assert result["outcome_pending"] is True
    assert result["escalation_required"] is False
    assert case.outcome is CaseOutcome.PENDING
    assert case.escalation_state == "awaiting_outcome"
    assert repository.get_unit("UNIT-TRANSFER").status == InventoryStatus.IN_TRANSIT


def test_execution_failure_is_persisted_and_escalated():
    audit = AuditRepository()

    def fail_mobilization(recommendation, case):
        raise RuntimeError("donor provider unavailable")

    execution_repository = InMemoryExecutionRepository()
    coordinator = ExecutionCoordinator(
        repository=InventoryRepository(),
        execution_repository=execution_repository,
        audit=audit,
        mobilize_donors=fail_mobilization,
    )
    case = Case(case_id="CASE-FAIL", request_id="REQ-FAIL", units_from_donors_remaining=2)
    recommendation = Recommendation(
        rec_id="REC-FAIL",
        type="MOBILIZE_DONORS",
        request_id=case.request_id,
        case_id=case.case_id,
        payload={"target_units": 2},
        state="APPROVED",
    )
    approval = Approval(
        approval_id="APP-FAIL",
        rec_id=recommendation.rec_id,
        actor="admin-1",
        decision=ApprovalDecision.APPROVE,
        rationale="Approved",
        at=NOW,
    )

    result = coordinator.execute_approved(recommendation, approval=approval, case=case)

    assert result["status"] == "failed"
    assert result["failure_reason"] == "donor provider unavailable"
    assert result["rollback_status"] == "not_required"
    assert result["escalation_required"] is True
    assert case.escalation_state == "execution_failed"
    assert execution_repository.records["EXEC-APP-FAIL"].failure_reason == "donor provider unavailable"
    assert [record.action for record in audit.all()] == ["execution_failed"]


def test_execution_failure_rolls_back_inventory_reservation():
    repository = InventoryRepository(units=[make_unit("UNIT-EXEC-ROLLBACK")])
    coordinator = ExecutionCoordinator(repository=repository)
    coordinator._finalize_result = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("result persistence unavailable")
    )
    case = Case(case_id="CASE-ROLLBACK", request_id="REQ-ROLLBACK", units_from_donors_remaining=1)
    recommendation = Recommendation(
        rec_id="REC-ROLLBACK",
        type="RESERVE_INVENTORY",
        request_id=case.request_id,
        case_id=case.case_id,
        payload={
            "bank_id": "BANK-1",
            "unit_ids": ["UNIT-EXEC-ROLLBACK"],
            "reservation_id": "RES-EXEC-ROLLBACK",
        },
        state="APPROVED",
    )
    approval = Approval(
        approval_id="APP-ROLLBACK",
        rec_id=recommendation.rec_id,
        actor="admin-1",
        decision=ApprovalDecision.APPROVE,
        rationale="Approved",
        at=NOW,
    )

    result = coordinator.execute_approved(recommendation, approval=approval, case=case)

    assert result["status"] == "failed"
    assert result["rollback_status"] == "completed"
    assert repository.get_unit("UNIT-EXEC-ROLLBACK").status == InventoryStatus.AVAILABLE
    assert repository.get_reservation("RES-EXEC-ROLLBACK").status.value == "released"


def test_execution_coordinator_rejects_mismatched_approval():
    coordinator = ExecutionCoordinator(repository=InventoryRepository())
    recommendation = Recommendation(
        rec_id="REC-EXEC-MISMATCH",
        type="MOBILIZE_DONORS",
        payload={"donor_ids": ["D1"]},
        state="APPROVED",
    )
    approval = Approval(
        approval_id="APP-EXEC-MISMATCH",
        rec_id="REC-OTHER",
        actor="admin-1",
        decision=ApprovalDecision.APPROVE,
        rationale="Approved",
        at=NOW,
    )

    with pytest.raises(ValueError, match="does not match"):
        coordinator.execute_approved(recommendation, approval=approval)


def test_execution_coordinator_rejects_rejected_approval():
    coordinator = ExecutionCoordinator(repository=InventoryRepository())
    recommendation = Recommendation(
        rec_id="REC-EXEC-REJECTED",
        type="MOBILIZE_DONORS",
        payload={"donor_ids": ["D1"]},
        state="APPROVED",
    )
    approval = Approval(
        approval_id="APP-EXEC-REJECTED",
        rec_id=recommendation.rec_id,
        actor="admin-1",
        decision=ApprovalDecision.REJECT,
        rationale="Rejected",
        at=NOW,
    )

    with pytest.raises(ValueError, match="Only an approved decision"):
        coordinator.execute_approved(recommendation, approval=approval)


def test_execution_service_aliases_coordinator_behavior():
    repository = InventoryRepository(units=[make_unit("UNIT-EXEC-3")])
    service = ExecutionService(repository=repository)
    case = Case(case_id="CASE-EXEC-3", request_id="REQ-EXEC-3")
    recommendation = Recommendation(
        rec_id="REC-EXEC-3",
        type="RESERVE_INVENTORY",
        request_id="REQ-EXEC-3",
        case_id="CASE-EXEC-3",
        payload={"bank_id": "BANK-1", "unit_ids": ["UNIT-EXEC-3"], "reservation_id": "RES-EXEC-3"},
        rationale="Approve to reserve stock.",
        state="APPROVED",
    )
    approval = Approval(
        approval_id="APP-EXEC-3",
        rec_id="REC-EXEC-3",
        actor="admin-1",
        decision=ApprovalDecision.APPROVE,
        rationale="Approved",
        at=NOW,
    )

    result = service.execute_approved(recommendation, approval=approval, case=case)

    assert result["status"] == "executed"
    assert repository.get_unit("UNIT-EXEC-3").status == InventoryStatus.RESERVED

import pytest

from approval_service import ApprovalService, ApprovalServiceError, ApprovalState


def test_approval_requires_explicit_decision_and_is_single_use():
    service = ApprovalService()
    record = service.create(
        recommendation_id="REC-1", request_id="REQ-1", case_id="CASE-1"
    )

    assert record.status == ApprovalState.PENDING
    approved = service.approve(record.approval_id, actor="admin-1")
    assert approved.status == ApprovalState.APPROVED
    assert approved.approved_by == "admin-1"

    with pytest.raises(ApprovalServiceError, match="expected PENDING"):
        service.reject(record.approval_id, actor="admin-2", reason="Not required")


def test_rejection_records_reason_and_cannot_enter_execution():
    service = ApprovalService()
    record = service.create(
        recommendation_id="REC-2", request_id="REQ-2", case_id="CASE-2"
    )

    rejected = service.reject(record.approval_id, actor="admin-1", reason="Insufficient evidence")

    assert rejected.status == ApprovalState.REJECTED
    assert rejected.rejection_reason == "Insufficient evidence"
    with pytest.raises(ApprovalServiceError):
        service.mark_executing(record.approval_id)


def test_execution_outcomes_support_partial_failure_and_escalation():
    service = ApprovalService()
    record = service.create(
        recommendation_id="REC-3", request_id="REQ-3", case_id="CASE-3"
    )

    service.approve(record.approval_id, actor="admin-1")
    service.mark_executing(record.approval_id)
    partial = service.mark_partial(record.approval_id)
    partial_status = partial.status
    escalated = service.mark_escalated(record.approval_id)

    assert partial_status == ApprovalState.PARTIAL
    assert escalated.status == ApprovalState.ESCALATED
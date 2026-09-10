from approval_service import ApprovalService, ApprovalState


def test_approved_recommendation_lifecycle_supports_execution_and_partial_escalation():
    service = ApprovalService()
    record = service.create(
        recommendation_id="REC-LIFECYCLE",
        request_id="REQ-LIFECYCLE",
        case_id="CASE-LIFECYCLE",
    )

    service.approve(record.approval_id, actor="admin-1")
    service.mark_executing(record.approval_id)
    service.mark_partial(record.approval_id)

    assert record.status == ApprovalState.PARTIAL

    service.mark_escalated(record.approval_id)

    assert record.status == ApprovalState.ESCALATED
from fastapi.testclient import TestClient

from contracts.models import Case, CaseOutcome

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATCH_DIR = ROOT / "services" / "match-svc"
sys.path.insert(0, str(MATCH_DIR))
from projections import active_case_notifications, DemoRole, project_audit_records, project_case, project_notifications
import main as match_main
sys.path.remove(str(MATCH_DIR))


def test_donor_projection_removes_internal_operational_fields():
    view = {
        "case": Case(case_id="CASE-1", request_id="REQ-1").model_dump(mode="json"),
        "ranked_donors": [{"donor_id": "D-1", "success_probability": 0.9}],
        "recommendations": [{"rec_id": "REC-1", "payload": {"unit_ids": ["U-1"]}}],
        "inventory": [{"unit_id": "U-1", "bank_id": "BANK-1"}],
        "notifications": [{"notification_id": "N-1", "donor_id": "D-1"}],
        "swarm": {"status": "initiated", "target_units": 2, "donors_contacted": ["D-1"]},
    }

    projected = project_case(view, DemoRole.DONOR)

    assert "ranked_donors" not in projected
    assert "recommendations" not in projected
    assert "inventory" not in projected
    assert "notifications" not in projected
    assert projected["swarm"] == {"status": "initiated", "target_units": 2}


def test_bank_notifications_exclude_closed_and_orphaned_cases():
    cases = {
        "CASE-OPEN": Case(case_id="CASE-OPEN", request_id="REQ-OPEN"),
        "CASE-FULFILLED": Case(case_id="CASE-FULFILLED", request_id="REQ-FULFILLED", outcome=CaseOutcome.FULFILLED),
        "CASE-CANCELLED": Case(case_id="CASE-CANCELLED", request_id="REQ-CANCELLED", outcome=CaseOutcome.CANCELLED),
        "CASE-UNFULFILLED": Case(case_id="CASE-UNFULFILLED", request_id="REQ-UNFULFILLED", outcome=CaseOutcome.UNFULFILLED),
    }
    notifications = [
        {"notification_id": "N-OPEN", "case_id": "CASE-OPEN"},
        {"notification_id": "N-FULFILLED", "case_id": "CASE-FULFILLED"},
        {"notification_id": "N-CANCELLED", "case_id": "CASE-CANCELLED"},
        {"notification_id": "N-UNFULFILLED", "case_id": "CASE-UNFULFILLED"},
        {"notification_id": "N-ORPHAN", "case_id": "CASE-DELETED"},
    ]

    assert [item["notification_id"] for item in active_case_notifications(notifications, cases)] == ["N-OPEN"]


def test_bank_and_regional_projections_keep_metrics_without_donor_identity():
    view = {
        "case": Case(case_id="CASE-OPS", request_id="REQ-OPS").model_dump(mode="json"),
        "ranked_donors": [
            {"donor_id": "D-1", "blood_group": "O+", "success_probability": 0.91},
            {"donor_id": "D-2", "blood_group": "O+", "success_probability": 0.82},
        ],
        "notifications": [{"notification_id": "N-1", "donor_id": "D-1"}],
        "swarm": {"status": "initiated", "donors_contacted": ["D-1"]},
        "activity": [{"action": "notification_sent", "audit_id": "AUDIT-NOTIFY-CASE-D-1", "details": {"donor_id": "D-1"}}],
    }

    for role in (DemoRole.BANK_ADMIN, DemoRole.REGIONAL_ADMIN):
        projected = project_case(view, role)
        assert "ranked_donors" not in projected
        assert "notifications" not in projected
        assert projected["donor_summary"][0]["rank"] == 1
        assert "donor_id" not in str(projected)


def test_case_projection_exposes_only_aggregate_delivery_state():
    view = {
        "case": Case(case_id="CASE-DELIVERY", request_id="REQ-DELIVERY").model_dump(mode="json"),
        "notifications": [
            {"notification_id": "N-1", "donor_id": "D-1", "delivery_status": "delivered"},
            {"notification_id": "N-2", "donor_id": "D-2", "delivery_status": "pending"},
            {"notification_id": "N-3", "donor_id": "D-3", "delivery_status": "failed"},
        ],
    }

    projected = project_case(view, DemoRole.HOSPITAL_COORDINATOR)

    summary = projected["delivery_summary"]
    assert {key: summary[key] for key in ("total", "delivered", "pending", "failed")} == {
        "total": 3,
        "delivered": 1,
        "pending": 1,
        "failed": 1,
    }
    assert summary["statuses"] == {"delivered": 1, "pending": 1, "failed": 1}
    assert "notifications" not in projected
    assert "D-1" not in str(projected)


def test_hospital_projection_exposes_aggregate_supply_dependency_only():
    case = Case(case_id="CASE-RESILIENCE", request_id="REQ-RESILIENCE").model_dump(mode="json")
    case["inventory_matches"] = [
        {"bank_id": "BANK-A", "units_available": 3},
        {"bank_id": "BANK-B", "units_available": 0},
    ]

    projected = project_case({"case": case}, DemoRole.HOSPITAL_COORDINATOR)

    assert projected["supply_resilience"] == {
        "supplier_count": 2,
        "stocked_supplier_count": 1,
        "single_supplier_dependency": True,
        "alternative_suppliers": 0,
    }


def test_notification_and_audit_projections_remove_donor_identifiers():
    notifications = project_notifications([{
        "notification_id": "NOTIFY-CASE-D-1",
        "donor_id": "D-1",
        "provider_message_id": "NOTIFY-CASE-D-1",
        "message": "Compatible request",
    }])
    audit = project_audit_records([{
        "audit_id": "AUDIT-NOTIFY-CASE-D-1",
        "action": "notification_sent",
        "details": {"donor_id": "D-1", "donor_ids": ["D-1"]},
    }])

    assert "D-1" not in str(notifications)
    assert "D-1" not in str(audit)
    assert "donor_id" not in str(notifications)
    assert notifications[0]["notification_id"] == "NOTIFICATION-REDACTED"
    assert "provider_message_id" not in notifications[0]
    assert "donor_id" not in str(audit)


def test_unauthenticated_role_claim_cannot_decide_reservation(monkeypatch):
    monkeypatch.setenv("BLOODNET_AUTH_MODE", "production")
    response = TestClient(match_main.app).post(
        "/api/v1/cases/CASE-1/reservations/REC-1/approve",
        json={"actor": "untrusted-user", "role": "donor"},
    )

    assert response.status_code == 401
    assert "Authentication required" in response.json()["detail"]

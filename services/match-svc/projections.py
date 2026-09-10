"""Role-safe case projections for the demo API."""

from __future__ import annotations

from copy import deepcopy
from enum import Enum
from typing import Any


class DemoRole(str, Enum):
    HOSPITAL_COORDINATOR = "hospital_coordinator"
    BANK_ADMIN = "bank_admin"
    DONOR = "donor"
    REGIONAL_ADMIN = "regional_admin"
    AUDITOR = "auditor"


def active_case_notifications(
    notifications: list[dict[str, Any]],
    cases: dict[str, Any],
) -> list[dict[str, Any]]:
    """Keep bank requirement alerts aligned with open case lifecycle state."""
    active_case_ids = {
        case_id
        for case_id, case in cases.items()
        if is_case_open(case)
    }
    return [item for item in notifications if item.get("case_id") in active_case_ids]


def is_case_open(case: Any) -> bool:
    return str(getattr(case.outcome, "value", case.outcome)) not in {
        "fulfilled",
        "cancelled",
        "unfulfilled",
    }


def _without_donor_identity(notifications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            key: ("NOTIFICATION-REDACTED" if key in {"notification_id", "provider_message_id"} else value)
            for key, value in notification.items()
            if key not in {"donor_id", "provider_message_id"}
        }
        for notification in notifications
    ]


def _without_donor_activity_identity(activity: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized = [event for event in deepcopy(activity) if event.get("action") not in {"notification_sent", "donor_response_recorded"}]
    for event in sanitized:
        if "audit_id" in event:
            event["audit_id"] = "AUDIT-REDACTED"
        if "event_id" in event:
            event["event_id"] = "EVENT-REDACTED"
        details = event.get("details")
        if isinstance(details, dict):
            details.pop("donor_id", None)
            details.pop("donor_ids", None)
    return sanitized


def project_notifications(notifications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return notification metadata without donor or donor-derived identifiers."""
    return _without_donor_identity(notifications)


def project_audit_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return audit records without donor identifiers embedded in audit data."""
    projected = deepcopy(records)
    for record in projected:
        record["audit_id"] = "AUDIT-REDACTED"
        if "event_id" in record:
            record["event_id"] = "EVENT-REDACTED"
        record.pop("donor_id", None)
        details = record.get("details")
        if isinstance(details, dict):
            details.pop("donor_id", None)
            details.pop("donor_ids", None)
    return projected


def _operational_donor_summary(ranked_donors: Any) -> list[dict[str, Any]]:
    """Expose ranking signals without exposing donor identities.

    Accept the recognizable legacy synthetic shape while stored records are
    being normalized. Unknown entries are ignored instead of taking the whole
    case-list endpoint down.
    """
    if isinstance(ranked_donors, dict):
        ranked_donors = ranked_donors.get("ranked_donors", [])
    if not isinstance(ranked_donors, list):
        return []
    return [
        {
            "rank": index,
            "blood_group": donor.get("blood_group"),
            "success_probability": donor.get("success_probability"),
        }
        for index, donor in enumerate(ranked_donors, start=1)
        if isinstance(donor, dict)
    ]


def _delivery_summary(notifications: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate provider delivery without exposing donor or message identity."""
    statuses: dict[str, int] = {}
    for notification in notifications:
        status = str(notification.get("delivery_status") or notification.get("status") or "pending").lower()
        statuses[status] = statuses.get(status, 0) + 1
    delivered = sum(count for status, count in statuses.items() if status in {"delivered", "success", "completed"})
    failed = sum(count for status, count in statuses.items() if status in {"failed", "undelivered", "rejected"})
    pending = max(0, len(notifications) - delivered - failed)
    return {
        "total": len(notifications),
        "delivered": delivered,
        "pending": pending,
        "failed": failed,
        "statuses": statuses,
    }


def _supply_resilience(case: dict[str, Any]) -> dict[str, Any]:
    """Expose facility dependency without leaking unit-level inventory."""
    matches = case.get("inventory_matches") or []
    suppliers = sorted({str(match.get("bank_id")) for match in matches if match.get("bank_id")})
    stocked = sorted({
        str(match.get("bank_id"))
        for match in matches
        if match.get("bank_id") and int(match.get("units_available") or 0) > 0
    })
    return {
        "supplier_count": len(suppliers),
        "stocked_supplier_count": len(stocked),
        "single_supplier_dependency": len(stocked) == 1,
        "alternative_suppliers": max(0, len(stocked) - 1),
    }


def project_case(view: dict[str, Any], role: DemoRole) -> dict[str, Any]:
    """Return the smallest case view appropriate for a demo role.

    This is a presentation projection, not an authorization system. Production
    authentication and authorization must enforce the same boundary server-side.
    """
    projected = deepcopy(view)
    projected["delivery_summary"] = _delivery_summary(projected.get("notifications", []))
    projected["supply_resilience"] = _supply_resilience(projected.get("case", {}))

    if role == DemoRole.BANK_ADMIN or role == DemoRole.REGIONAL_ADMIN:
        ranked_donors = projected.pop("ranked_donors", [])
        projected["donor_summary"] = _operational_donor_summary(ranked_donors)
        projected.pop("notifications", None)
        projected["activity"] = _without_donor_activity_identity(projected.get("activity", []))
        swarm = projected.get("swarm")
        if isinstance(swarm, dict):
            swarm.pop("donors_contacted", None)
        return projected

    if role == DemoRole.AUDITOR:
        projected.pop("ranked_donors", None)
        projected.pop("notifications", None)
        return projected

    ranked_donors = projected.pop("ranked_donors", [])
    projected.pop("notifications", None)
    projected.pop("recommendations", None)
    projected.pop("inventory", None)
    projected["activity"] = _without_donor_activity_identity(projected.get("activity", []))

    if role == DemoRole.DONOR:
        projected["swarm"] = {
            key: value
            for key, value in projected.get("swarm", {}).items()
            if key in {"status", "target_units", "cohort_size"}
        }
    elif role == DemoRole.HOSPITAL_COORDINATOR:
        projected["donor_summary"] = _operational_donor_summary(ranked_donors)
        projected["swarm"] = {
            key: value
            for key, value in projected.get("swarm", {}).items()
            if key in {"status", "target_units", "cohort_size"}
        }

    return projected


def project_case_list(views: list[dict[str, Any]], role: DemoRole) -> list[dict[str, Any]]:
    return [project_case(view, role) for view in views]

"""Central role-to-permission mapping for BloodNet authorization."""

from contracts.models import RoleType


ROLE_PERMISSIONS: dict[RoleType, list[str]] = {
    RoleType.DONOR: ["cases.read", "donations.read", "donations.respond"],
    RoleType.HOSPITAL_COORDINATOR: [
        "cases.read", "cases.create", "cases.approve", "inventory.read",
    ],
    RoleType.BANK_ADMIN: [
        "cases.read", "inventory.read", "inventory.reserve", "inventory.release",
        "recommendations.read", "recommendations.approve",
    ],
    RoleType.REGIONAL_ADMIN: [
        "cases.read", "cases.create", "inventory.read", "organizations.read",
        "organizations.verify", "recommendations.read", "recommendations.approve",
    ],
    RoleType.AUDITOR: ["cases.read", "organizations.read", "audit.read", "recommendations.read", "recommendations.approve"],
}


def permissions_for_role(role: RoleType | str) -> list[str]:
    """Return a copy so callers cannot mutate the shared policy."""
    resolved_role = role if isinstance(role, RoleType) else RoleType(role)
    return list(ROLE_PERMISSIONS.get(resolved_role, []))

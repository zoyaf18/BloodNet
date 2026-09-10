"""Canonical decoding for legacy inventory shared by operational services."""
from datetime import datetime, timedelta, timezone
from contracts.models import InventoryUnit

_LEGACY_COMPONENT_VALUES = {
    "whole_blood": "Whole Blood",
    "whole blood": "Whole Blood",
    "platelets": "Platelets (RDP)",
    "platelets_rdp": "Platelets (RDP)",
    "platelets_sdp": "Platelets (SDP)",
    "ffp": "FFP",
    "plasma": "FFP",
    "cryoprecipitate": "Cryoprecipitate",
}


def _inventory_unit_from_payload(payload: dict) -> InventoryUnit:
    """Read both current contract payloads and pre-contract inventory values."""
    normalized = dict(payload)
    component = normalized.get("component")
    if isinstance(component, str):
        normalized["component"] = _LEGACY_COMPONENT_VALUES.get(component.strip().lower(), component)
    expires_at = normalized.get("expires_at")
    expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00")) if isinstance(expires_at, str) else expires_at
    if isinstance(expires, datetime) and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires:
        normalized["expires_at"] = expires.isoformat()
    collected_at = normalized.get("collected_at")
    collected = datetime.fromisoformat(collected_at.replace("Z", "+00:00")) if isinstance(collected_at, str) else collected_at
    if isinstance(collected, datetime) and collected.tzinfo is None:
        collected = collected.replace(tzinfo=timezone.utc)
    if collected:
        normalized["collected_at"] = collected.isoformat()
    else:
        normalized["collected_at"] = (expires - timedelta(days=42) if expires else datetime.now(timezone.utc)).isoformat()
    return InventoryUnit.model_validate(normalized)


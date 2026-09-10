"""Location helpers for BloodNet's city-based operational scope."""

from __future__ import annotations

from typing import Any


CITY_COORDINATES: dict[str, tuple[float, float]] = {
    "Ahmedabad": (23.0225, 72.5714), "Bengaluru": (12.9716, 77.5946), "Bhopal": (23.2599, 77.4126),
    "Bhubaneswar": (20.2961, 85.8245), "Chandigarh": (30.7333, 76.7794), "Chennai": (13.0827, 80.2707),
    "Coimbatore": (11.0168, 76.9558), "Dehradun": (30.3165, 78.0322), "Delhi": (28.6139, 77.2090),
    "Goa": (15.4909, 73.8278), "Guwahati": (26.1445, 91.7362), "Hyderabad": (17.3850, 78.4867),
    "Indore": (22.7196, 75.8577), "Jaipur": (26.9124, 75.7873), "Kochi": (9.9312, 76.2673),
    "Kolkata": (22.5726, 88.3639), "Lucknow": (26.8467, 80.9462), "Mumbai": (19.0760, 72.8777),
    "Nagpur": (21.1458, 79.0882), "Patna": (25.5941, 85.1376), "Pune": (18.5204, 73.8567),
    "Raipur": (21.2514, 81.6296), "Ranchi": (23.3441, 85.3096), "Surat": (21.1702, 72.8311),
    "Varanasi": (25.3176, 82.9739),
}


def operational_region(metadata: dict[str, Any] | None) -> str | None:
    """Return the facility city used as the operational region."""
    values = metadata or {}
    for key in ("city", "region_id", "region"):
        value = values.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def enrich_location_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Add stable city coordinates when an organization supplies a known city."""
    enriched = dict(metadata or {})
    city = operational_region(enriched)
    coordinates = next(
        (value for name, value in CITY_COORDINATES.items() if city and name.casefold() == city.casefold()),
        None,
    )
    if coordinates and not isinstance(enriched.get("geo"), dict):
        enriched["geo"] = {"lat": coordinates[0], "lng": coordinates[1]}
    return enriched
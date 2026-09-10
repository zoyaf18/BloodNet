"""Provider-neutral travel-time estimates for matching and transfer planning."""

from __future__ import annotations

import json
import os
from math import atan2, cos, radians, sin, sqrt
from typing import Protocol
from urllib.request import Request, urlopen

from contracts.models import GeoPoint

EARTH_RADIUS_KM = 6371.0


def haversine_km(origin: GeoPoint, destination: GeoPoint) -> float:
    lat1, lng1, lat2, lng2 = map(
        radians,
        (origin.lat, origin.lng, destination.lat, destination.lng),
    )
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * atan2(sqrt(h), sqrt(1 - h))


class TravelTimeProvider(Protocol):
    def minutes(self, origin: GeoPoint, destination: GeoPoint) -> float:
        """Return a route estimate in minutes."""


class MockTravelTimeProvider:
    """Deterministic provider used locally and when Maps is not configured."""

    def __init__(self, average_speed_kmh: float = 30.0) -> None:
        if average_speed_kmh <= 0:
            raise ValueError("average_speed_kmh must be positive")
        self.average_speed_kmh = average_speed_kmh

    def minutes(self, origin: GeoPoint, destination: GeoPoint) -> float:
        return haversine_km(origin, destination) / self.average_speed_kmh * 60.0


class CachedTravelTimeProvider:
    """Bounded in-process cache that prevents repeated route-provider calls."""

    def __init__(self, provider: TravelTimeProvider, max_entries: int = 10_000) -> None:
        self.provider = provider
        self.max_entries = max_entries
        self._cache: dict[tuple[float, float, float, float], float] = {}

    def minutes(self, origin: GeoPoint, destination: GeoPoint) -> float:
        key = (round(origin.lat, 5), round(origin.lng, 5), round(destination.lat, 5), round(destination.lng, 5))
        if key not in self._cache:
            if len(self._cache) >= self.max_entries:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = self.provider.minutes(origin, destination)
        return self._cache[key]


class GoogleRoutesProvider:
    """Small Google Routes REST adapter; activation is configuration-gated."""

    def __init__(self, api_key: str, endpoint: str | None = None, timeout: float = 3.0) -> None:
        if not api_key:
            raise ValueError("Google Routes API key is required")
        self.api_key = api_key
        self.endpoint = endpoint or "https://routes.googleapis.com/directions/v2:computeRoutes"
        self.timeout = timeout

    def minutes(self, origin: GeoPoint, destination: GeoPoint) -> float:
        body = json.dumps({
            "origin": {"location": {"latLng": {"latitude": origin.lat, "longitude": origin.lng}}},
            "destination": {"location": {"latLng": {"latitude": destination.lat, "longitude": destination.lng}}},
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE",
            "computeAlternativeRoutes": False,
            "languageCode": "en-US",
            "units": "METRIC",
        }).encode("utf-8")
        request = Request(self.endpoint, data=body, method="POST", headers={
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "routes.duration",
            "Content-Type": "application/json",
        })
        with urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        duration = payload.get("routes", [{}])[0].get("duration", "")
        if not isinstance(duration, str) or not duration.endswith("s"):
            raise RuntimeError("Google Routes response did not contain a duration")
        return float(duration[:-1]) / 60.0


def build_travel_time_provider() -> TravelTimeProvider:
    """Use Google Routes only when explicitly enabled; otherwise stay deterministic."""
    if os.getenv("BLOODNET_TRAVEL_TIME_PROVIDER", "mock").lower() == "google_routes":
        api_key = os.getenv("GOOGLE_MAPS_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_MAPS_API_KEY is required for Google Routes")
        return CachedTravelTimeProvider(GoogleRoutesProvider(api_key))
    return CachedTravelTimeProvider(MockTravelTimeProvider(float(os.getenv("BLOODNET_MOCK_AVERAGE_SPEED_KMH", "30"))))
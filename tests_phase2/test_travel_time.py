from contracts.models import GeoPoint
from contracts.travel_time import CachedTravelTimeProvider, MockTravelTimeProvider, haversine_km


def test_mock_travel_time_is_deterministic_and_positive():
    origin = GeoPoint(lat=18.5204, lng=73.8567)
    destination = GeoPoint(lat=19.0760, lng=72.8777)
    provider = MockTravelTimeProvider(30)

    assert haversine_km(origin, destination) > 0
    assert provider.minutes(origin, destination) == provider.minutes(origin, destination)


def test_cached_provider_deduplicates_route_calls():
    calls = []

    class CountingProvider:
        def minutes(self, origin, destination):
            calls.append((origin, destination))
            return 12.5

    provider = CachedTravelTimeProvider(CountingProvider(), max_entries=2)
    origin = GeoPoint(lat=1, lng=2)
    destination = GeoPoint(lat=3, lng=4)

    assert provider.minutes(origin, destination) == 12.5
    assert provider.minutes(origin, destination) == 12.5
    assert len(calls) == 1
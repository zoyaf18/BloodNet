import math
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "services/match-svc")

from contracts.models import BloodGroup, Component, Donor, GeoPoint, Hospital, Request, Urgency
from request_flow import match_request


def make_request() -> Request:
    return Request(
        request_id="REQ-BENCH",
        group=BloodGroup.A_POS,
        component=Component.RBC,
        qty=2,
        hospital_id="H1",
        urgency=Urgency.CRITICAL,
        required_by=datetime.now(timezone.utc) + timedelta(hours=2),
        source_channel="benchmark",
    )


def make_hospital() -> Hospital:
    return Hospital(
        hospital_id="H1",
        name="Hospital",
        geo=GeoPoint(lat=18.5204, lng=73.8567),
        tier="tier1",
    )


def make_donor(i: int) -> Donor:
    return Donor(
        donor_id=f"D{i}",
        blood_group=BloodGroup.A_POS,
        geo=GeoPoint(lat=18.52, lng=73.85),
        age_years=30,
        reliability_features={
            "historical_response_rate": 0.8,
            "historical_completion_rate": 0.8,
            "days_since_last_donation": 180,
            "is_repeat_donor": 1,
        },
    )


def main() -> int:
    donors = [make_donor(i) for i in range(50_000)]
    eligible = {donor.donor_id for donor in donors}
    request = make_request()
    hospital = make_hospital()

    # Warm up the model and caches once so the measurement reflects the
    # hot-path performance target rather than cold-start loading cost.
    match_request(request, hospital, [], [], donors, eligible_donor_ids=eligible)

    latencies_ms = []
    for _ in range(15):
        started = time.perf_counter()
        match_request(request, hospital, [], [], donors, eligible_donor_ids=eligible)
        latencies_ms.append((time.perf_counter() - started) * 1000)

    latencies_ms.sort()
    p95_ms = latencies_ms[max(0, math.ceil(0.95 * len(latencies_ms)) - 1)]

    print(f"samples={len(latencies_ms)}")
    print(f"avg_ms={statistics.mean(latencies_ms):.3f}")
    print(f"p50_ms={statistics.median(latencies_ms):.3f}")
    print(f"p95_ms={p95_ms:.3f}")
    print(f"max_ms={max(latencies_ms):.3f}")
    print(f"min_ms={min(latencies_ms):.3f}")

    if p95_ms > 800:
        print(f"FAIL: p95_ms {p95_ms:.3f} exceeds 800 ms threshold")
        return 1

    print("PASS: p95_ms within 800 ms threshold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

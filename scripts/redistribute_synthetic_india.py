"""Unevenly redistribute existing synthetic records across declared Indian cities.

This is intentionally limited to rows marked synthetic and runs atomically.
It preserves row counts and foreign-key IDs. ``region`` is normalized to the
operational city used by authorization and forecasts; the broad area is kept
as ``zone`` for aggregate reporting.
"""
from __future__ import annotations

import json
import os
import random
from collections import defaultdict

import psycopg

CITY_WEIGHTS = {
    "North": [("Delhi", 35), ("Jaipur", 24), ("Lucknow", 19), ("Chandigarh", 13), ("Dehradun", 9)],
    "South": [("Bengaluru", 31), ("Chennai", 25), ("Hyderabad", 21), ("Kochi", 14), ("Coimbatore", 9)],
    "East": [("Kolkata", 34), ("Bhubaneswar", 19), ("Guwahati", 18), ("Ranchi", 16), ("Patna", 13)],
    "West": [("Mumbai", 30), ("Pune", 24), ("Ahmedabad", 21), ("Surat", 16), ("Goa", 9)],
    "Central": [("Bhopal", 28), ("Indore", 24), ("Nagpur", 21), ("Raipur", 15), ("Varanasi", 12)],
}

def choose(rng: random.Random, region: str) -> str:
    values = CITY_WEIGHTS[region]
    return rng.choices([name for name, _ in values], weights=[weight for _, weight in values], k=1)[0]

def main() -> None:
    dsn = os.getenv("BLOODNET_DATABASE_URL")
    if not dsn:
        raise SystemExit("BLOODNET_DATABASE_URL is required")
    rng = random.Random(20260906)
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, type, metadata FROM organizations WHERE metadata->>'synthetic' = 'true' ORDER BY id")
            orgs = cur.fetchall()
            org_city: dict[str, str] = {}
            hospital_city: dict[str, str] = {}
            org_updates = []
            for org_id, org_type, metadata in orgs:
                region = str((metadata or {}).get("region", ""))
                if region not in CITY_WEIGHTS:
                    continue
                city = choose(rng, region)
                org_city[str(org_id)] = city
                if getattr(org_type, "value", org_type) == "hospital":
                    hospital_city[str(org_id)] = city
                metadata = dict(metadata or {})
                metadata["city"] = city
                metadata["zone"] = region
                org_updates.append((f"{city}, India", json.dumps(metadata), org_id))
            cur.executemany("UPDATE organizations SET address=%s, metadata=%s, updated_at=NOW() WHERE id=%s", org_updates)

            cur.execute("SELECT unit_id, payload FROM inventory_units WHERE payload->>'synthetic' = 'true'")
            inventory_updates = []
            for unit_id, payload in cur.fetchall():
                bank_id = str(payload.get("bank_id", ""))
                city = org_city.get(bank_id)
                if city:
                    payload = dict(payload)
                    payload["zone"] = payload.get("zone") or payload.get("region")
                    payload["city"] = city
                    payload["region"] = city
                    inventory_updates.append((json.dumps(payload), unit_id))
            cur.executemany("UPDATE inventory_units SET payload=%s, updated_at=NOW() WHERE unit_id=%s", inventory_updates)

            cur.execute("SELECT request_id, payload FROM workflow_requests WHERE payload->>'synthetic' = 'true'")
            request_updates = []
            for request_id, payload in cur.fetchall():
                city = hospital_city.get(str(payload.get("hospital_id", "")))
                if city:
                    payload = dict(payload)
                    payload["zone"] = payload.get("zone") or payload.get("region")
                    payload["city"] = city
                    payload["region"] = city
                    request_updates.append((json.dumps(payload), request_id))
            cur.executemany("UPDATE workflow_requests SET payload=%s, updated_at=NOW() WHERE request_id=%s", request_updates)

            # Case and list-view projections repeat region for reporting. Keep
            # those denormalized copies aligned with the canonical request.
            cur.execute("""UPDATE workflow_cases AS cases
                           SET payload = jsonb_set(
                               jsonb_set(cases.payload, '{zone}', to_jsonb(COALESCE(cases.payload->>'zone', cases.payload->>'region'))),
                               '{region}', to_jsonb(requests.payload->>'region')
                           ), updated_at=NOW()
                           FROM workflow_requests AS requests
                           WHERE cases.payload->>'synthetic' = 'true'
                             AND requests.request_id = cases.payload->>'request_id'
                             AND requests.payload->>'region' IS NOT NULL""")
            cur.execute("""UPDATE workflow_case_projections AS projections
                           SET swarm_status = jsonb_set(
                               jsonb_set(COALESCE(projections.swarm_status, '{}'::jsonb), '{zone}', to_jsonb(COALESCE(projections.swarm_status->>'zone', cases.payload->>'zone'))),
                               '{region}', to_jsonb(requests.payload->>'region')
                           ), updated_at=NOW()
                           FROM workflow_cases AS cases
                           JOIN workflow_requests AS requests
                             ON requests.request_id = cases.payload->>'request_id'
                           WHERE projections.case_id = cases.case_id
                             AND cases.payload->>'synthetic' = 'true'
                             AND requests.payload->>'region' IS NOT NULL""")

            # Early synthetic seeds wrote {ranked_donors: [id, ...]} into a
            # column whose contract is [{donor_id, ...}, ...]. Normalize only
            # that recognizable legacy shape.
            cur.execute("""UPDATE workflow_case_projections AS projections
                           SET ranked_donors = jsonb_build_array(jsonb_build_object(
                                   'donor_id', projections.ranked_donors->'ranked_donors'->>0,
                                   'blood_group', requests.payload->>'group',
                                   'success_probability', 0.0
                               )), updated_at=NOW()
                           FROM workflow_cases AS cases
                           JOIN workflow_requests AS requests
                             ON requests.request_id = cases.payload->>'request_id'
                           WHERE projections.case_id = cases.case_id
                             AND cases.payload->>'synthetic' = 'true'
                             AND jsonb_typeof(projections.ranked_donors) = 'object'
                             AND projections.ranked_donors->>'synthetic' = 'true'
                             AND jsonb_array_length(projections.ranked_donors->'ranked_donors') > 0""")

            cur.execute("""SELECT profile.user_id, profile.city, membership.metadata->>'region'
                         FROM donor_profiles profile
                         JOIN users user_row ON user_row.id = profile.user_id
                         JOIN organization_memberships membership ON membership.user_id = profile.user_id
                         WHERE user_row.email LIKE 'synthetic-donor-%@example.invalid'
                         AND membership.metadata->>'region' IS NOT NULL""")
            donor_updates = []
            for user_id, _old_city, donor_region in cur.fetchall():
                if donor_region in CITY_WEIGHTS:
                    donor_updates.append((choose(rng, donor_region), user_id))
            cur.executemany("UPDATE donor_profiles SET city=%s, updated_at=NOW() WHERE user_id=%s", donor_updates)
        conn.commit()
    print(f"Redistributed {len(org_city)} synthetic organizations and their linked records using uneven city weights.")

if __name__ == "__main__":
    main()

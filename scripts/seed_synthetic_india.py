"""Seed deterministic, non-production synthetic BloodNet data for India.

The seed is additive and idempotent. It never deletes existing rows and never
creates usable credentials: synthetic users have invalid email domains and no
password or external identity subject.

Examples:
    python scripts/seed_synthetic_india.py --database-url "$env:BLOODNET_DATABASE_URL" --rows 50000
    python scripts/seed_synthetic_india.py --database-url "$env:BLOODNET_DATABASE_URL" --rows 50000 --target cloud --confirm-cloud
"""

from __future__ import annotations

import argparse
import json
import os
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid5

import psycopg
from psycopg.types.json import Jsonb

NAMESPACE = UUID("8c0cf8c3-0f48-4a3c-9b1d-7f78c9e8a6d4")
NOW = datetime.now(timezone.utc)
BATCH_SIZE = 500

REGIONS = {
    "North": ["Delhi", "Jaipur", "Lucknow", "Chandigarh", "Dehradun"],
    "South": ["Bengaluru", "Chennai", "Hyderabad", "Kochi", "Coimbatore"],
    "East": ["Kolkata", "Bhubaneswar", "Guwahati", "Ranchi", "Patna"],
    "West": ["Mumbai", "Pune", "Ahmedabad", "Surat", "Goa"],
    "Central": ["Bhopal", "Indore", "Nagpur", "Raipur", "Varanasi"],
}
CITY_COORDINATES = {
    "Delhi": (28.6139, 77.2090), "Jaipur": (26.9124, 75.7873), "Lucknow": (26.8467, 80.9462),
    "Chandigarh": (30.7333, 76.7794), "Dehradun": (30.3165, 78.0322),
    "Bengaluru": (12.9716, 77.5946), "Chennai": (13.0827, 80.2707), "Hyderabad": (17.3850, 78.4867),
    "Kochi": (9.9312, 76.2673), "Coimbatore": (11.0168, 76.9558),
    "Kolkata": (22.5726, 88.3639), "Bhubaneswar": (20.2961, 85.8245), "Guwahati": (26.1445, 91.7362),
    "Ranchi": (23.3441, 85.3096), "Patna": (25.5941, 85.1376),
    "Mumbai": (19.0760, 72.8777), "Pune": (18.5204, 73.8567), "Ahmedabad": (23.0225, 72.5714),
    "Surat": (21.1702, 72.8311), "Goa": (15.4909, 73.8278),
    "Bhopal": (23.2599, 77.4126), "Indore": (22.7196, 75.8577), "Nagpur": (21.1458, 79.0882),
    "Raipur": (21.2514, 81.6296), "Varanasi": (25.3176, 82.9739),
}
BLOOD_GROUPS = ("O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-")
COMPONENTS = ("RBC", "plasma", "platelets", "whole_blood")
COLD_CHAIN_BUFFER_DAYS = 1
RBC_ACCEPTABLE_DONORS = {
    "O-": {"O-"},
    "O+": {"O-", "O+"},
    "A-": {"O-", "A-"},
    "A+": {"O-", "O+", "A-", "A+"},
    "B-": {"O-", "B-"},
    "B+": {"O-", "O+", "B-", "B+"},
    "AB-": {"O-", "A-", "B-", "AB-"},
    "AB+": set(BLOOD_GROUPS),
}
HOSPITAL_TYPES = ("government", "private", "teaching", "district", "trauma", "charitable")
CITY_ACTIVITY_WEIGHTS = {
    city: 2 + ((index * 5) % 9)
    for index, city in enumerate(city for cities in REGIONS.values() for city in cities)
}


def deterministic_id(kind: str, index: int) -> UUID:
    return uuid5(NAMESPACE, f"{kind}:{index}")


def text_id(kind: str, index: int) -> str:
    return f"SYNTH-{kind.upper()}-{index:06d}"


def chunked(rows: list[tuple], size: int = BATCH_SIZE):
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def execute_many(cur, statement: str, rows: list[tuple]) -> int:
    inserted = 0
    for batch in chunked(rows):
        cur.executemany(statement, batch)
        inserted += len(batch)
    return inserted


def weighted_city_sequence() -> list[str]:
    return [
        city
        for city, weight in CITY_ACTIVITY_WEIGHTS.items()
        for _ in range(weight)
    ]


def select_facility(facilities: list[dict], city: str, index: int) -> dict:
    matches = [facility for facility in facilities if facility["city"] == city]
    return matches[index % len(matches)] if matches else facilities[index % len(facilities)]


def city_geo(city: str) -> dict[str, float]:
    latitude, longitude = CITY_COORDINATES[city]
    return {"lat": latitude, "lng": longitude}


def build_organizations(count: int) -> tuple[list[tuple], list[dict]]:
    hospitals = max(1, int(count * 0.72))
    banks = max(1, int(count * 0.22))
    regions = max(1, count - hospitals - banks)
    rows: list[tuple] = []
    metadata: list[dict] = []
    index = 0
    for kind, amount in (("hospital", hospitals), ("blood_bank", banks), ("regional", regions)):
        for local_index in range(amount):
            region = list(REGIONS)[index % len(REGIONS)]
            city = REGIONS[region][(index // len(REGIONS)) % len(REGIONS[region])]
            organization_id = deterministic_id("organization", index)
            email = f"synthetic-org-{index:06d}@example.invalid"
            if kind == "hospital":
                name = f"Synthetic {HOSPITAL_TYPES[local_index % len(HOSPITAL_TYPES)].title()} Hospital {local_index + 1:05d}"
                metadata_value = {"synthetic": True, "region_id": city, "city": city, "zone": region, "geo": city_geo(city), "hospital_type": HOSPITAL_TYPES[local_index % len(HOSPITAL_TYPES)]}
            elif kind == "blood_bank":
                name = f"Synthetic Regional Blood Bank {local_index + 1:05d}"
                metadata_value = {"synthetic": True, "region_id": city, "city": city, "zone": region, "geo": city_geo(city), "bank_id": f"SYNTH-BANK-{local_index:05d}"}
            else:
                name = f"Synthetic {region} Regional Center {local_index + 1:05d}"
                metadata_value = {"synthetic": True, "region_id": city, "city": city, "zone": region, "geo": city_geo(city)}
            rows.append((organization_id, name, kind, f"{city}, India", email, f"+91-90000-{index:05d}", Jsonb(metadata_value), True, NOW, None, "active", NOW, NOW))
            metadata.append({"id": organization_id, "type": kind, "region": region, "city": city})
            index += 1
    return rows, metadata


def build_users(count: int, organizations: list[dict]) -> tuple[list[tuple], list[tuple], list[tuple], list[dict]]:
    donor_orgs = [item for item in organizations if item["type"] == "regional"] or organizations
    user_rows: list[tuple] = []
    membership_rows: list[tuple] = []
    profile_rows: list[tuple] = []
    donor_meta: list[dict] = []
    for index in range(count):
        user_id = deterministic_id("user", index)
        group = BLOOD_GROUPS[index % len(BLOOD_GROUPS)]
        region = list(REGIONS)[index % len(REGIONS)]
        city = REGIONS[region][(index // len(REGIONS)) % len(REGIONS[region])]
        organization_id = donor_orgs[index % len(donor_orgs)]["id"]
        created = NOW - timedelta(days=index % 730)
        user_rows.append((user_id, f"synthetic-donor-{index:06d}@example.invalid", True, None, f"Synthetic Donor {index + 1:06d}", f"+91-80000-{index:05d}", False, "active", created, created))
        membership_rows.append((deterministic_id("membership", index), user_id, organization_id, "donor", "active", None, None, None, Jsonb({"synthetic": True, "region": region}), created, created))
        profile_rows.append((user_id, group, date(1970 + index % 35, 1 + index % 12, 1 + index % 27), city, "available" if index % 10 else "paused", index % 4 != 0, Jsonb(["email"]), "eligible", None, Jsonb([]), created, created))
        donor_meta.append({"id": user_id, "text_id": text_id("donor", index), "group": group, "region": region, "city": city})
    return user_rows, membership_rows, profile_rows, donor_meta


def build_domain_rows(rows: int, organizations: list[dict], donors: list[dict]):
    hospitals = [item for item in organizations if item["type"] == "hospital"]
    banks = [item for item in organizations if item["type"] == "blood_bank"]
    demand_rows = []
    pool_rows = []
    inventory_rows = []
    request_rows = []
    case_rows = []
    projection_rows = []
    cities = weighted_city_sequence()
    hospitals_by_city = {city: [item for item in hospitals if item["city"] == city] for city in CITY_ACTIVITY_WEIGHTS}
    banks_by_city = {city: [item for item in banks if item["city"] == city] for city in CITY_ACTIVITY_WEIGHTS}
    contexts = []
    for index in range(rows):
        donor = donors[index % len(donors)]
        group = donor["group"]
        hospital_city = cities[(index * 11 + index // 13) % len(cities)]
        bank_city = cities[(index * 7 + index // 17 + 3) % len(cities)]
        hospital = select_facility(hospitals_by_city[hospital_city], hospital_city, index) if hospitals_by_city[hospital_city] else hospitals[index % len(hospitals)]
        bank = select_facility(banks_by_city[bank_city], bank_city, index) if banks_by_city[bank_city] else banks[index % len(banks)]
        # A region_id is the operational city everywhere else in BloodNet
        # (identity scope, forecasts, and organization metadata).  The old
        # seed used the donor's broad zone here and paired it with unrelated
        # hospitals/banks, which made every city-scoped dashboard look empty.
        hospital_city = hospital["city"]
        bank_city = bank["city"]
        hospital_zone = hospital["region"]
        bank_zone = bank["region"]
        component = COMPONENTS[(index + len(hospital_city) + index // 23) % len(COMPONENTS)]
        created = NOW - timedelta(days=index % 365)
        quantity = 1 + ((index * 3 + CITY_ACTIVITY_WEIGHTS[hospital_city]) % 12)
        pool_rows.append((donor["text_id"], donor["city"], group, float(5 + index % 95), index % 9 != 0, index % 4 != 0, created))
        unit_id = text_id("unit", index)
        inventory_status = "available" if index % 11 not in {0, 1} else ("reserved" if index % 11 == 0 else "discarded")
        inventory_payload = {"unit_id": unit_id, "synthetic": True, "region": bank_city, "region_id": bank_city, "zone": bank_zone, "city": bank_city, "bank_id": str(bank["id"]), "group": group, "component": component, "status": inventory_status, "expires_at": (date.today() + timedelta(days=7 + ((index * 17) % 360))).isoformat()}
        inventory_rows.append((unit_id, Jsonb(inventory_payload), created, created))
        contexts.append({
            "index": index,
            "donor": donor,
            "group": group,
            "component": component,
            "quantity": quantity,
            "hospital": hospital,
            "hospital_city": hospital_city,
            "hospital_zone": hospital_zone,
            "created": created,
        })

    inventory_payloads = [row[1].obj for row in inventory_rows]
    for context in contexts:
        index = context["index"]
        group = context["group"]
        component = context["component"]
        quantity = context["quantity"]
        hospital = context["hospital"]
        hospital_city = context["hospital_city"]
        hospital_zone = context["hospital_zone"]
        created = context["created"]
        acceptable_groups = RBC_ACCEPTABLE_DONORS[group]
        min_expiry = date.today() + timedelta(days=COLD_CHAIN_BUFFER_DAYS)
        compatible_units = [
            unit for unit in inventory_payloads
            if unit["region"] == hospital_city
            and unit["group"] in acceptable_groups
            and unit["component"] == component
            and unit["status"] == "available"
            and date.fromisoformat(unit["expires_at"]) >= min_expiry
        ]
        units_from_inventory = min(quantity, len(compatible_units))
        units_from_donors_remaining = quantity - units_from_inventory
        request_id = text_id("request", index)
        case_id = text_id("case", index)
        request_status = "fulfilled" if units_from_donors_remaining == 0 else "pending"
        request_payload = {"request_id": request_id, "synthetic": True, "region": hospital_city, "region_id": hospital_city, "zone": hospital_zone, "city": hospital_city, "group": group, "component": component, "qty": quantity, "hospital_id": str(hospital["id"]), "hospital_name": hospital["name"] if "name" in hospital else str(hospital["id"]), "urgency": ("critical", "urgent", "routine")[((index * 5) + CITY_ACTIVITY_WEIGHTS[hospital_city]) % 3], "status": request_status}
        request_rows.append((request_id, Jsonb(request_payload), created, created))
        case_outcome = "fulfilled" if units_from_donors_remaining == 0 else "pending"
        case_payload = {"case_id": case_id, "request_id": request_id, "synthetic": True, "region": hospital_city, "zone": hospital_zone, "outcome": case_outcome, "reservation_state": "reserved" if units_from_inventory > 0 else "not_proposed", "units_from_inventory": units_from_inventory, "units_from_donors_remaining": units_from_donors_remaining}
        case_rows.append((case_id, Jsonb(case_payload), created, created))
        swarm_status = "pending" if units_from_donors_remaining > 0 else "inventory_covered"
        projection_rows.append((case_id, Jsonb([{"donor_id": context["donor"]["text_id"], "blood_group": group, "success_probability": 0.0}]), Jsonb({"status": swarm_status, "synthetic": True, "region": hospital_city, "zone": hospital_zone}), created, created))
        shortage = min(quantity, index % 3)
        demand_rows.append(((date.today() - timedelta(days=index % 730)), hospital_city, str(hospital["id"]), group, component, quantity, quantity - shortage, shortage))
    return pool_rows, inventory_rows, request_rows, case_rows, projection_rows, demand_rows


def seed(database_url: str, rows: int, target: str, confirm_cloud: bool, refresh_synthetic: bool = False) -> dict[str, int]:
    if not 1 <= rows <= 50_000:
        raise ValueError("--rows must be between 1 and 50000")
    if target == "cloud" and not confirm_cloud:
        raise RuntimeError("Cloud seeding requires --confirm-cloud; this is an additive bulk write to Cloud SQL")
    if target == "cloud" and "example.invalid" not in database_url and os.getenv("BLOODNET_ENV") not in {"demo", "synthetic"}:
        print("WARNING: target=cloud writes synthetic rows to the supplied database URL; existing rows are preserved.")

    organization_count = min(5_000, max(20, rows // 10))
    organizations, organization_meta = build_organizations(organization_count)
    users, memberships, profiles, donors = build_users(rows, organization_meta)
    pool, inventory, requests, cases, projections, demand = build_domain_rows(rows, organization_meta, donors)

    organization_conflict = "DO UPDATE SET name=EXCLUDED.name, metadata=EXCLUDED.metadata, updated_at=EXCLUDED.updated_at" if refresh_synthetic else "DO NOTHING"
    inventory_conflict = "DO UPDATE SET payload=EXCLUDED.payload, updated_at=EXCLUDED.updated_at" if refresh_synthetic else "DO NOTHING"
    request_conflict = "DO UPDATE SET payload=EXCLUDED.payload, updated_at=EXCLUDED.updated_at" if refresh_synthetic else "DO NOTHING"
    case_conflict = "DO UPDATE SET payload=EXCLUDED.payload, updated_at=EXCLUDED.updated_at" if refresh_synthetic else "DO NOTHING"
    projection_conflict = "DO UPDATE SET ranked_donors=EXCLUDED.ranked_donors, swarm_status=EXCLUDED.swarm_status, updated_at=EXCLUDED.updated_at" if refresh_synthetic else "DO NOTHING"
    demand_conflict = "DO UPDATE SET requested_units=EXCLUDED.requested_units, fulfilled_units=EXCLUDED.fulfilled_units, shortage_units=EXCLUDED.shortage_units" if refresh_synthetic else "DO NOTHING"
    statements = [
        ("organizations", f"""INSERT INTO organizations (id,name,type,address,contact_email,contact_phone,metadata,verified,verified_at,verified_by,status,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) {organization_conflict}""", organizations),
        ("users", """INSERT INTO users (id,email,email_verified,password_hash,display_name,phone,mfa_enabled,status,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING""", users),
        ("organization_memberships", """INSERT INTO organization_memberships (id,user_id,organization_id,role,status,invited_by,approved_by,approved_at,metadata,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING""", memberships),
        ("donor_profiles", """INSERT INTO donor_profiles (user_id,blood_group,date_of_birth,city,availability,consent_contact,notification_channels,eligibility_status,next_eligible_at,donation_history,consent_updated_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (user_id) DO NOTHING""", profiles),
        ("donor_pool", """INSERT INTO donor_pool (donor_id,region,blood_group,radius_km,eligible,consent_contactable,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (donor_id) DO NOTHING""", pool),
        ("inventory_units", f"""INSERT INTO inventory_units (unit_id,payload,created_at,updated_at) VALUES (%s,%s,%s,%s) ON CONFLICT (unit_id) {inventory_conflict}""", inventory),
        ("workflow_requests", f"""INSERT INTO workflow_requests (request_id,payload,created_at,updated_at) VALUES (%s,%s,%s,%s) ON CONFLICT (request_id) {request_conflict}""", requests),
        ("workflow_cases", f"""INSERT INTO workflow_cases (case_id,payload,created_at,updated_at) VALUES (%s,%s,%s,%s) ON CONFLICT (case_id) {case_conflict}""", cases),
        ("workflow_case_projections", f"""INSERT INTO workflow_case_projections (case_id,ranked_donors,swarm_status,created_at,updated_at) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (case_id) {projection_conflict}""", projections),
        ("demand_history", f"""INSERT INTO demand_history (demand_date,region,hospital_id,blood_group,component,requested_units,fulfilled_units,shortage_units) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (demand_date,region,hospital_id,blood_group,component) {demand_conflict}""", demand),
    ]
    counts: dict[str, int] = {}
    # Reconnect per table so a proxy/database restart can be resumed idempotently.
    for name, statement, rows_to_insert in statements:
        with psycopg.connect(database_url) as conn:
            with conn.cursor() as cur:
                counts[name] = execute_many(cur, statement, rows_to_insert)
            conn.commit()
        print(f"seeded {name}: {counts[name]}")
    counts["organization_target"] = organization_count
    counts["rows_target"] = rows
    counts["synthetic"] = 1
    print(json.dumps(counts, indent=2, default=str))
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("BLOODNET_DATABASE_URL"), required=False)
    parser.add_argument("--rows", type=int, default=50_000, help="Rows for donor/domain tables, max 50000")
    parser.add_argument("--target", choices=("local", "cloud"), default="local")
    parser.add_argument("--confirm-cloud", action="store_true")
    parser.add_argument("--refresh-synthetic", action="store_true", help="Update existing deterministic synthetic rows with the current generator")
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("--database-url or BLOODNET_DATABASE_URL is required")
    seed(args.database_url, args.rows, args.target, args.confirm_cloud, args.refresh_synthetic)


if __name__ == "__main__":
    main()

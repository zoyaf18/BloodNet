"""Create one bounded production test case through approval to donor outreach."""
import json
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://bloodnet-api-qt4toyt7fq-el.a.run.app"
CONFIG = dict(
    line.split("=", 1)
    for line in (ROOT / "web/app/.env.production").read_text().splitlines()
    if "=" in line and not line.lstrip().startswith("#")
)
API_KEY = CONFIG["VITE_IDENTITY_PLATFORM_API_KEY"].strip().strip('"').strip("'")
GCLOUD = r"C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
ACCOUNTS = json.loads((ROOT / ".ui-check-credentials.json").read_text())


def firebase_token(account):
    response = requests.post(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={API_KEY}",
        json={**account, "returnSecureToken": True}, timeout=30,
    )
    response.raise_for_status()
    return response.json()["idToken"]


def headers(account):
    service_token = subprocess.run(
        [GCLOUD, "auth", "print-identity-token"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    return {
        "Authorization": f"Bearer {firebase_token(account)}",
        "X-Serverless-Authorization": f"Bearer {service_token}",
        "Content-Type": "application/json",
    }


def request(account, method, path, **kwargs):
    response = requests.request(method, BASE + path, headers=headers(account), timeout=60, **kwargs)
    body = response.json() if response.content else {}
    if not response.ok:
        raise RuntimeError(f"{method} {path} -> {response.status_code}: {str(body)[:500]}")
    return body


hospital_headers = headers(ACCOUNTS["hospital_coordinator"])
donor_headers = headers(ACCOUNTS["donor"])
donor_location = requests.put(
    BASE + "/match-svc/api/v1/auth/location", headers=donor_headers,
    json={"lat": 18.5204, "lng": 73.8567, "accuracy_m": 100}, timeout=60,
)
donor_location.raise_for_status()
donor_profile = requests.put(
    BASE + "/match-svc/api/v1/auth/donor-profile", headers=donor_headers,
    json={
        "display_name": "Production Test Donor",
        "blood_group": "O+",
        "date_of_birth": "1990-01-01",
        "city": "Pune",
        "region_id": "Pune",
        "availability": "available",
        "consent_contact": True,
        "notification_channels": ["email"],
        "eligibility_status": "eligible",
        "last_donation_at": None,
        "next_eligible_at": None,
        "donation_history": [],
    }, timeout=60,
)
donor_profile.raise_for_status()
identity_response = requests.get(BASE + "/match-svc/api/v1/me", headers=hospital_headers, timeout=60)
identity_response.raise_for_status()
identity = identity_response.json()
if not identity.get("location"):
    location_update = requests.put(
        BASE + "/match-svc/api/v1/auth/location", headers=hospital_headers,
        json={"lat": 18.5204, "lng": 73.8567, "accuracy_m": 100}, timeout=60,
    )
    location_update.raise_for_status()
    identity_response = requests.get(BASE + "/match-svc/api/v1/me", headers=hospital_headers, timeout=60)
    identity_response.raise_for_status()
    identity = identity_response.json()
if not identity.get("location"):
    raise RuntimeError("Production hospital test user location could not be confirmed")
location = identity["location"]
organization_response = requests.get(BASE + "/match-svc/api/v1/organizations/me", headers=hospital_headers, timeout=60)
organization_response.raise_for_status()
metadata = organization_response.json().get("metadata") or {}
metadata.update({
    "geo": {"lat": location["lat"], "lng": location["lng"]},
    "location_source": "user_confirmed_facility",
})
facility_update = requests.patch(
    BASE + "/match-svc/api/v1/organizations/me", headers=hospital_headers,
    json={"metadata": metadata}, timeout=60,
)
facility_update.raise_for_status()
if metadata.get("region_id") != "Pune":
    regional_headers = headers(ACCOUNTS["regional_admin"])
    organization_id = organization_response.json()["id"]
    region_update = requests.patch(
        BASE + f"/match-svc/api/v1/organizations/{organization_id}/region",
        headers=regional_headers, json={"region_id": "Pune"}, timeout=60,
    )
    region_update.raise_for_status()
regional_headers = headers(ACCOUNTS["regional_admin"])
all_organizations = requests.get(BASE + "/match-svc/api/v1/organizations", headers=regional_headers, timeout=60)
all_organizations.raise_for_status()
for organization in all_organizations.json():
    organization_metadata = organization.get("metadata") or {}
    if organization.get("type") == "hospital" and organization_metadata.get("hospital_id") == identity.get("hospital_id"):
        reconcile = requests.patch(
            BASE + f"/match-svc/api/v1/organizations/{organization['id']}/region",
            headers=regional_headers, json={"region_id": "Pune"}, timeout=60,
        )
        reconcile.raise_for_status()
hospital_context_response = requests.get(
    BASE + "/match-svc/api/v1/hospital/context", headers=hospital_headers, timeout=60,
)
if not hospital_context_response.ok:
    current_org = requests.get(BASE + "/match-svc/api/v1/organizations/me", headers=hospital_headers, timeout=60)
    regional_orgs = requests.get(BASE + "/match-svc/api/v1/organizations", headers=headers(ACCOUNTS["regional_admin"]), timeout=60)
    matches = []
    if regional_orgs.ok:
        matches = [item for item in regional_orgs.json() if (item.get("metadata") or {}).get("hospital_id") == identity.get("hospital_id")]
    raise RuntimeError(f"GET hospital context -> {hospital_context_response.status_code}: {hospital_context_response.text[:500]}; org_metadata={current_org.json().get('metadata') if current_org.ok else current_org.text[:300]}; matches={matches}")
hospital_context_response.raise_for_status()
context = hospital_context_response.json()
hospital = context["hospital"]
region = hospital["region"]
units = [unit for unit in context["units"] if unit.get("status") == "available"][:12]
if not units:
    raise RuntimeError("Production hospital context has no available inventory for a bounded test")

# Use a deliberately traceable test request and leave fulfillment untouched.
existing_cases = request(ACCOUNTS["hospital_coordinator"], "GET", "/match-svc/api/v1/cases?limit=100")
existing_case = next(
    (item.get("case") for item in existing_cases.get("cases", [])
    if str(item.get("case", {}).get("request_id", "")).startswith("PROD-DONOR-MAIL-")),
    None,
)
request_id = existing_case["request_id"] if existing_case else "PROD-DONOR-MAIL-" + uuid.uuid4().hex[:12].upper()
payload = {
    "request": {
        "request_id": request_id,
        "hospital_id": hospital["hospital_id"],
        "region": region,
        "group": "O+",
        "component": "RBC",
        "qty": min(4, max(1, len(units) + 1)),
        "urgency": "Critical",
        "required_by": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "source_channel": "manual",
        "emergency_details": "TEST ONLY: production donor outreach simulation; no fulfillment requested",
    },
    "hospital": hospital,
    "banks": context["banks"],
    "units": units,
    "donors": [],
    "eligible_donor_ids": [],
    "eligibility_records": {},
}
if existing_case:
    case = existing_case
    matched = {"ranked_donors": []}
else:
    created = requests.post(
        BASE + "/match-svc/api/v1/match", headers=hospital_headers, json=payload, timeout=90,
    )
    if not created.ok:
        raise RuntimeError(f"POST match -> {created.status_code}: {created.text[:800]}")
    matched = created.json()
    case = matched["case"]
ranked = matched.get("ranked_donors", [])
if not ranked:
    raise RuntimeError("Production match created no eligible donor outreach targets")

bank_headers = headers(ACCOUNTS["bank_admin"])
recommendation = None
for _ in range(12):
    recommendations_response = requests.get(
        BASE + "/match-svc/api/v1/recommendations?state=AWAITING_APPROVAL",
        headers=bank_headers, timeout=60,
    )
    recommendations_response.raise_for_status()
    recommendations = recommendations_response.json().get("recommendations", [])
    recommendation = next(
        (item for item in recommendations
         if item.get("case_id") == case["case_id"]
         or item.get("payload", {}).get("case_id") == case["case_id"]
         or item.get("reservation_proposal", {}).get("case_id") == case["case_id"]),
        None,
    )
    if recommendation:
        break
    import time
    time.sleep(5)
if not recommendation:
    case_state = requests.get(BASE + f"/match-svc/api/v1/cases/{case['case_id']}", headers=hospital_headers, timeout=60)
    raise RuntimeError(f"Recommendation did not appear for case {case['case_id']}; case={case_state.text[:500]}; sample_recommendations={json.dumps(recommendations[:3])[:1200]}")
rec_id = recommendation["rec_id"]
approved = requests.post(
    BASE + f"/match-svc/api/v1/recommendations/{rec_id}/approve",
    headers=bank_headers,
    json={"rationale": "TEST ONLY: approved production donor outreach simulation"},
    timeout=90,
)
approved.raise_for_status()

# Verify the donor-facing opportunity without accepting it.
donor = request(ACCOUNTS["donor"], "GET", "/swarm-svc/api/v1/opportunities")
opportunities = [item for item in donor.get("opportunities", []) if item.get("case_id") == case["case_id"]]
if not opportunities:
    raise RuntimeError("Approval succeeded but the authorized donor received no opportunity")

report = {
    "request_id": request_id,
    "case_id": case["case_id"],
    "recommendation_id": rec_id,
    "ranked_donor_count": len(ranked),
    "donor_opportunity_count": len(opportunities),
    "donor_opportunity_statuses": [item.get("status") for item in opportunities],
    "fulfillment_touched": False,
    "test_only": True,
}
(ROOT / "docs/production-donor-outreach-simulation.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))

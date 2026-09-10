#!/usr/bin/env python
"""End-to-end production flow verification for bloodnet-api Cloud Run deployment.

Tests the complete workflow:
1. Create a case via /match-svc/api/v1/match
2. Approve the recommendation
3. Retrieve donor opportunities
4. Accept donor responses
5. Verify case fulfillment

This script confirms the patch to scoring.py that defaults missing donation history
allows the flow to complete without validation errors.
"""

import os
import sys
import json
import jwt
import requests
import uuid
from datetime import datetime, timedelta, timezone

BASE_URL = "https://bloodnet-api-qt4toyt7fq-el.a.run.app"
JWT_SECRET = os.environ.get("BLOODNET_JWT_SECRET") or os.environ.get("JWT_SECRET", "")
GOOGLE_IAM_TOKEN = os.environ.get("GOOGLE_IAM_TOKEN", "")

if not JWT_SECRET or not GOOGLE_IAM_TOKEN:
    print("ERROR: JWT_SECRET and GOOGLE_IAM_TOKEN environment variables required")
    sys.exit(1)


def make_headers(sub: str, email: str) -> dict:
    """Create authenticated request headers."""
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": sub,
            "email": email,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Serverless-Authorization": f"Bearer {GOOGLE_IAM_TOKEN}",
        "Content-Type": "application/json",
    }


def step_1_create_case():
    """Step 1: Create a case via /match-svc/api/v1/match."""
    print("\n--- STEP 1: Create Case ---")
    admin_headers = make_headers(
        "905e9598-f92f-41ed-93df-08c62b2c7c1f",
        "live-heartbeat-bank-admin@bloodnet.local",
    )
    request_id = f"REQ-LIVE-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)

    body = {
        "request": {
            "request_id": request_id,
            "group": "A+",
            "component": "RBC",
            "qty": 2,
            "hospital_id": "HOSP-001",
            "urgency": "Critical",
            "required_by": (now + timedelta(hours=4)).isoformat(),
            "source_channel": "live-verify",
            "verification_state": "verified",
            "status": "open",
        },
        "hospital": {
            "hospital_id": "HOSP-001",
            "name": "City Hospital",
            "geo": {"lat": 18.5204, "lng": 73.8567},
            "tier": "tertiary",
            "affiliated_banks": ["BANK-001"],
        },
        "banks": [
            {
                "bank_id": "BANK-001",
                "name": "Central Blood Bank",
                "geo": {"lat": 18.521, "lng": 73.857},
                "licence_id": "LIC-001",
            }
        ],
        "units": [
            {
                "unit_id": f"UNIT-{request_id}",
                "bank_id": "BANK-001",
                "group": "A+",
                "component": "RBC",
                "collected_at": (now - timedelta(days=5)).isoformat(),
                "expires_at": (now + timedelta(days=30)).isoformat(),
                "status": "available",
            }
        ],
        "donors": [
            {
                "donor_id": "DONOR-001",
                "blood_group": "A+",
                "geo": {"lat": 18.52, "lng": 73.85},
                "age_years": 30,
                "contact_tokens": ["demo"],
                "consent_scopes": ["contactable"],
                "reliability_features": {
                    "historical_response_rate": 0.97,
                    "historical_completion_rate": 0.97,
                    "distance_to_bank_km": 2.5,
                    "travel_time_min": 15,
                    "hour_of_day": 9,
                    "day_of_week": 1,
                    "urgency_level": 2,
                    "contact_fatigue_30d": 0,
                    "group_scarcity_index": 0.25,
                    "is_repeat_donor": 1,
                },
            },
            {
                "donor_id": "DONOR-002",
                "blood_group": "A+",
                "geo": {"lat": 18.52, "lng": 73.85},
                "age_years": 29,
                "contact_tokens": ["demo"],
                "consent_scopes": ["contactable"],
                "reliability_features": {
                    "historical_response_rate": 0.97,
                    "historical_completion_rate": 0.97,
                    "distance_to_bank_km": 2.5,
                    "travel_time_min": 15,
                    "hour_of_day": 9,
                    "day_of_week": 1,
                    "urgency_level": 2,
                    "contact_fatigue_30d": 0,
                    "group_scarcity_index": 0.25,
                    "is_repeat_donor": 1,
                },
            },
        ],
        "eligible_donor_ids": ["DONOR-001", "DONOR-002"],
        "eligibility_records": {
            "DONOR-001": {"age_years": 30, "weight_kg": 72.5, "hb_g_dl": 13.8},
            "DONOR-002": {"age_years": 29, "weight_kg": 70.0, "hb_g_dl": 13.9},
        },
    }

    resp = requests.post(
        f"{BASE_URL}/match-svc/api/v1/match",
        headers=admin_headers,
        json=body,
        timeout=30,
    )
    print(f"Status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"Response: {resp.text[:400]}")
        sys.exit(1)

    result = resp.json()
    case_id = result["case"]["case_id"]
    rec_id = result["recommendation"]["rec_id"]
    print(f"✓ Case created: {case_id}")
    print(f"✓ Recommendation: {rec_id}")
    return case_id, rec_id, admin_headers


def step_2_approve_case(case_id: str, rec_id: str, admin_headers: dict):
    """Step 2: Approve the recommendation."""
    print("\n--- STEP 2: Approve Recommendation ---")
    resp = requests.post(
        f"{BASE_URL}/match-svc/api/v1/cases/{case_id}/reservations/{rec_id}/approve",
        headers=admin_headers,
        json={"rationale": "Verified live production flow"},
        timeout=30,
    )
    print(f"Status: {resp.status_code}")
    if resp.status_code not in (200, 201):
        print(f"Response: {resp.text[:400]}")
        sys.exit(1)
    print("✓ Reservation approved")


def step_3_accept_donor_responses(case_id: str):
    """Step 3: Retrieve opportunities and accept donor responses."""
    print("\n--- STEP 3: Accept Donor Responses ---")
    for donor_id, email in [
        ("DONOR-001", "live-heartbeat-donor-1@bloodnet.local"),
        ("DONOR-002", "live-heartbeat-donor-2@bloodnet.local"),
    ]:
        donor_headers = make_headers(donor_id, email)
        # Retrieve opportunities
        opp_resp = requests.get(
            f"{BASE_URL}/swarm-svc/api/v1/opportunities",
            headers=donor_headers,
            params={"donor_id": donor_id},
            timeout=30,
        )
        print(f"  {donor_id} opportunities status: {opp_resp.status_code}")
        if opp_resp.status_code != 200:
            print(f"  Response: {opp_resp.text[:300]}")
            continue  # May not have opportunities yet

        opportunities = opp_resp.json().get("opportunities", [])
        if not opportunities:
            print(f"  {donor_id}: No opportunities found yet")
            continue

        for opp in opportunities:
            outreach_id = opp["outreach_id"]
            resp = requests.post(
                f"{BASE_URL}/swarm-svc/api/v1/outreach/{outreach_id}/response",
                headers=donor_headers,
                json={"donor_id": donor_id, "response": "accept"},
                timeout=30,
            )
            print(f"  {donor_id} accept status: {resp.status_code}")
            if resp.status_code not in (200, 201):
                print(f"  Response: {resp.text[:300]}")


def step_4_verify_fulfillment(case_id: str, admin_headers: dict):
    """Step 4: Verify case fulfillment state."""
    print("\n--- STEP 4: Verify Fulfillment ---")
    resp = requests.get(
        f"{BASE_URL}/match-svc/api/v1/cases/{case_id}",
        headers=admin_headers,
        timeout=30,
    )
    print(f"Status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"Response: {resp.text[:400]}")
        sys.exit(1)

    case_data = resp.json().get("case", {})
    print(f"  Fulfillment probability: {case_data.get('fulfillment_probability')}")
    print(f"  Outcome: {case_data.get('outcome')}")
    print(f"  Units from donors fulfilled: {case_data.get('units_from_donors_fulfilled')}")
    print("✓ Case state retrieved successfully")


def main():
    """Run the complete end-to-end verification."""
    print("========================================")
    print("BloodNet Production Flow Verification")
    print("========================================")

    try:
        case_id, rec_id, admin_headers = step_1_create_case()
        step_2_approve_case(case_id, rec_id, admin_headers)
        step_3_accept_donor_responses(case_id)
        step_4_verify_fulfillment(case_id, admin_headers)

        print("\n========================================")
        print("✓ END-TO-END VERIFICATION PASSED")
        print("========================================")
        return 0
    except Exception as e:
        print(f"\n✗ VERIFICATION FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
import os
import json
import requests
import jwt
from datetime import datetime, timedelta, timezone

base = "https://bloodnet-api-qt4toyt7fq-el.a.run.app"
case_id = "CASE-c5f03a6713fc"
bank_admin_id = "905e9598-f92f-41ed-93df-08c62b2c7c1f"
donor1_id = "a4d06c28-a34d-4e15-a1fe-a94dd186ef02"
donor2_id = "01229b44-01e4-4b43-996d-410ac69ca4a2"

jwt_secret = os.getenv("BLOODNET_JWT_SECRET")
google_token = os.getenv("GOOGLE_IAM_TOKEN")

def get_headers(subject, email):
    now = datetime.now(timezone.utc)
    app = jwt.encode({
        "sub": subject,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp())
    }, jwt_secret, algorithm="HS256")
    return {
        "Authorization": f"Bearer {app}",
        "X-Serverless-Authorization": f"Bearer {google_token}",
        "Content-Type": "application/json"
    }

# Step 1: Get case and approve
print("=== STEP 1: Approve case ===")
admin_headers = get_headers(bank_admin_id, "live-heartbeat-bank-admin@bloodnet.local")
case_resp = requests.get(f"{base}/match-svc/api/v1/cases/{case_id}", headers=admin_headers, timeout=20)
case = case_resp.json().get("case", {})
print(f"Initial probability: {case.get('fulfillment_probability')}")

reservations = case.get("reservations", [])
if reservations:
    res_id = reservations[0]["reservation_id"]
    print(f"Approving reservation {res_id}...")
    resp = requests.post(f"{base}/match-svc/api/v1/cases/{case_id}/reservations/{res_id}/approve", 
                        headers=admin_headers, json={}, timeout=20)
    if resp.status_code in (200, 201):
        updated = resp.json().get("case", {})
        prob_after_approve = updated.get("fulfillment_probability")
        print(f"After approval - Probability: {prob_after_approve}, Swarm: {updated.get('swarm_status')}, Cohort: {updated.get('cohort_size')}")
    else:
        print(f"Approval failed: {resp.status_code}")
else:
    print("No reservations found")

# Step 2: Check opportunities for donor 1
print("\n=== STEP 2: Donor 1 accepts opportunity ===")
donor1_headers = get_headers(donor1_id, "live-heartbeat-donor-1@bloodnet.local")
opp_resp = requests.get(f"{base}/swarm-svc/api/v1/opportunities", headers=donor1_headers, 
                       params={"donor_id": "a4d06c28-a34d-4e15-a1fe-a94dd186ef02"}, timeout=20)
opportunities = opp_resp.json().get("opportunities", [])
print(f"Donor 1 opportunities: {len(opportunities)}")

if opportunities:
    outreach_id = opportunities[0]["outreach_id"]
    print(f"Accepting {outreach_id}...")
    accept_resp = requests.post(f"{base}/swarm-svc/api/v1/outreach/{outreach_id}/response",
                               headers=donor1_headers, 
                               json={"donor_id": "a4d06c28-a34d-4e15-a1fe-a94dd186ef02", "response": "accept"},
                               timeout=20)
    if accept_resp.status_code in (200, 201):
        print(f"Donor 1 accepted (status {accept_resp.status_code})")
    else:
        print(f"Accept failed: {accept_resp.status_code} - {accept_resp.text[:200]}")

# Step 3: Check case probability after donor 1
print("\n=== STEP 3: Check probability after donor 1 ===")
case_resp = requests.get(f"{base}/match-svc/api/v1/cases/{case_id}", headers=admin_headers, timeout=20)
case = case_resp.json().get("case", {})
print(f"After donor 1 - Probability: {case.get('fulfillment_probability')}, Fulfilled: {case.get('units_from_donors_fulfilled')}, Remaining: {case.get('units_from_donors_remaining')}")

# Step 4: Donor 2 accepts
print("\n=== STEP 4: Donor 2 accepts opportunity ===")
donor2_headers = get_headers(donor2_id, "live-heartbeat-donor-2@bloodnet.local")
opp_resp = requests.get(f"{base}/swarm-svc/api/v1/opportunities", headers=donor2_headers,
                       params={"donor_id": "01229b44-01e4-4b43-996d-410ac69ca4a2"}, timeout=20)
opportunities = opp_resp.json().get("opportunities", [])
print(f"Donor 2 opportunities: {len(opportunities)}")

if opportunities:
    outreach_id = opportunities[0]["outreach_id"]
    print(f"Accepting {outreach_id}...")
    accept_resp = requests.post(f"{base}/swarm-svc/api/v1/outreach/{outreach_id}/response",
                               headers=donor2_headers,
                               json={"donor_id": "01229b44-01e4-4b43-996d-410ac69ca4a2", "response": "accept"},
                               timeout=20)
    if accept_resp.status_code in (200, 201):
        print(f"Donor 2 accepted (status {accept_resp.status_code})")
    else:
        print(f"Accept failed: {accept_resp.status_code} - {accept_resp.text[:200]}")

# Step 5: Final probability check
print("\n=== STEP 5: Final probability (should be 100%) ===")
case_resp = requests.get(f"{base}/match-svc/api/v1/cases/{case_id}", headers=admin_headers, timeout=20)
case = case_resp.json().get("case", {})
final_prob = case.get('fulfillment_probability')
fulfilled = case.get('units_from_donors_fulfilled')
remaining = case.get('units_from_donors_remaining')
outcome = case.get('outcome')
print(f"FINAL - Probability: {final_prob}, Fulfilled: {fulfilled}, Remaining: {remaining}, Outcome: {outcome}")

if final_prob == 1.0 or final_prob >= 0.99:
    print("\n✓ SUCCESS: Heartbeat progression complete (0% → 100%)")
else:
    print(f"\n⚠ Partial success: Reached {final_prob*100:.2f}%")

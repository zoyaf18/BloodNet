#!/usr/bin/env python
import os
import json
import requests
import jwt
from datetime import datetime, timedelta, timezone

base = "https://bloodnet-api-qt4toyt7fq-el.a.run.app"
case_id = "CASE-c5f03a6713fc"
bank_admin_id = "905e9598-f92f-41ed-93df-08c62b2c7c1f"

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

admin_headers = get_headers(bank_admin_id, "live-heartbeat-bank-admin@bloodnet.local")
case_resp = requests.get(f"{base}/match-svc/api/v1/cases/{case_id}", headers=admin_headers, timeout=20)
case = case_resp.json().get("case", {})

print(f"Case {case_id}:")
print(f"  Status: {case.get('outcome')}")
print(f"  Inventory matches: {len(case.get('inventory_matches', []))}")
print(f"  Reservations: {len(case.get('reservations', []))}")
print(f"  Fulfillment prob: {case.get('fulfillment_probability')}")
print(f"\nInventory matches:")
for i, match in enumerate(case.get('inventory_matches', [])):
    print(f"  [{i}] Bank: {match.get('bank_id')}, Selected units: {match.get('selected_unit_ids')}")

print(f"\nReservations:")
for i, res in enumerate(case.get('reservations', [])):
    print(f"  [{i}] ID: {res.get('reservation_id')}, Status: {res.get('reservation_state')}")

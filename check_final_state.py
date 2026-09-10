#!/usr/bin/env python
import os
import json
import requests
import jwt
from datetime import datetime, timedelta, timezone

base = "https://bloodnet-api-qt4toyt7fq-el.a.run.app"
case_id = "CASE-4e1bd0ea6b4d"
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
r = requests.get(f"{base}/match-svc/api/v1/cases/{case_id}", headers=admin_headers, timeout=20)
c = r.json().get("case", {})

print(f"=== FINAL CASE STATE ===")
print(f"Probability: {c.get('fulfillment_probability')}")
print(f"Units fulfilled: {c.get('units_from_donors_fulfilled')}")
print(f"Units remaining: {c.get('units_from_donors_remaining')}")
print(f"Outcome: {c.get('outcome')}")
print(f"Swarm status: {c.get('swarm_status')}")
print(f"Cohort size: {c.get('cohort_size')}")

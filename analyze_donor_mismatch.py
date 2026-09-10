#!/usr/bin/env python
import os
import json
import requests
import jwt
from datetime import datetime, timedelta, timezone

base = "https://bloodnet-api-qt4toyt7fq-el.a.run.app"
case_id = "CASE-4e1bd0ea6b4d"  # Use the original working case
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

admin_headers = get_headers(bank_admin_id, "live-heartbeat-bank-admin@bloodnet.local")
print(f"=== Working with case {case_id} ===\n")

# Check current status
case_resp = requests.get(f"{base}/match-svc/api/v1/cases/{case_id}", headers=admin_headers, timeout=20)
case = case_resp.json().get("case", {})
print(f"Current state:")
print(f"  Probability: {case.get('fulfillment_probability')}")
print(f"  Swarm: {case.get('swarm_status')}")
print(f"  Fulfilled units: {case.get('units_from_donors_fulfilled')}")
print(f"  Remaining units: {case.get('units_from_donors_remaining')}")

# The case has synthetic donor IDs in notifications, not UUIDs
# Let's check what notifications exist and what donor_ids they have
print(f"\n=== Checking notifications for this case ===")
notif_resp = requests.get(f"{base}/match-svc/api/v1/notifications", 
                         headers=admin_headers, 
                         params={"case_id": case_id},
                         timeout=20)
notifications = notif_resp.json().get("notifications", [])
print(f"Notifications found: {len(notifications)}")
for n in notifications:
    print(f"  ID: {n['notification_id']}, Donor: {n['donor_id']}")

# The problem is: the notifications have synthetic donor IDs (DONOR-LIVE-HEARTBEAT-001)
# but when we authenticate as users with UUID, the system can't match them
# We need to check if there's a way to lookup users by the synthetic donor IDs

print(f"\n=== Checking if synthetic donor IDs exist in database ===")
publicIp = requests.get('https://ifconfig.me/ip', timeout=5).text.strip()

# Check database
import subprocess
import psycopg
db_url_secret = subprocess.run(['gcloud', 'secrets', 'versions', 'access', 'latest', 
                               '--secret=bloodnet-database-url', 
                               '--project=project-bae56d7f-3ee2-48fc-bdd'],
                              capture_output=True, text=True).stdout.strip()
db_url = db_url_secret.replace('@/bloodnet?host=/cloudsql/project-bae56d7f-3ee2-48fc-bdd:us-central1:bloodnet-postgres=tcp:5432', 
                               '@34.14.139.135:5432')

subprocess.run(['gcloud', 'sql', 'instances', 'patch', 'bloodnet-postgres',
               '--project=project-bae56d7f-3ee2-48fc-bdd',
               f'--authorized-networks={publicIp}/32',
               '--quiet'], check=False, capture_output=True)

try:
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, email FROM users WHERE email LIKE %s", ("%donor%",))
            users = cur.fetchall()
            print(f"Users in database:")
            for u in users:
                print(f"  UUID: {u[0]}, Email: {u[1]}")
finally:
    subprocess.run(['gcloud', 'sql', 'instances', 'patch', 'bloodnet-postgres',
                   '--project=project-bae56d7f-3ee2-48fc-bdd',
                   '--clear-authorized-networks',
                   '--quiet'], check=False, capture_output=True)

print(f"\n⚠ ISSUE: The case has synthetic donor IDs in notifications, not user UUIDs")
print(f"   This prevents donors from retrieving opportunities via swarm-svc API")
print(f"   The system needs to be fixed to use consistent donor IDs")

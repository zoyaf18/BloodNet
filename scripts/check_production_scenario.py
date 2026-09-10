"""Read-only preflight for the four-account production workflow scenario."""
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone
import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://bloodnet-api-qt4toyt7fq-el.a.run.app"


def main():
    accounts = json.loads(os.environ.pop("BLOODNET_SCENARIO_ACCOUNTS"))
    config = dict(line.split("=", 1) for line in (ROOT / "web/app/.env.production").read_text().splitlines() if "=" in line and not line.startswith("#"))
    key = config["VITE_IDENTITY_PLATFORM_API_KEY"].strip().strip('"')
    cli = r"C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
    iam = subprocess.run([cli, "auth", "print-identity-token"], capture_output=True, text=True, check=True).stdout.strip()
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "roles": {}}
    organizations = {}
    context = {}
    bank_id = None
    for role, credentials in accounts.items():
        auth = requests.post(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={key}", json={**credentials, "returnSecureToken": True}, timeout=30)
        auth.raise_for_status()
        headers = {"Authorization": "Bearer " + auth.json()["idToken"], "X-Serverless-Authorization": "Bearer " + iam}
        def get(path):
            response = requests.get(BASE + path, headers=headers, timeout=45)
            response.raise_for_status()
            return response.json()
        identity = get("/match-svc/api/v1/me")
        item = {"role": identity.get("role"), "has_user_location": bool(identity.get("location"))}
        if role in {"bank", "hospital"}:
            org = get("/match-svc/api/v1/organizations/me")
            if os.environ.get("BLOODNET_SET_CONFIRMED_FACILITY_LOCATIONS") == "true":
                location = identity.get("location")
                if not location:
                    raise RuntimeError("Confirmed account location is missing")
                before = org.get("metadata") or {}
                geo = {key: location[key] for key in ("lat", "lng")}
                response = requests.patch(BASE + "/match-svc/api/v1/organizations/me", headers=headers,
                                          json={"metadata": {"geo": geo, "location_source": "user_confirmed_facility"}}, timeout=30)
                response.raise_for_status()
                org = get("/match-svc/api/v1/organizations/me")
                after = org.get("metadata") or {}
                if after.get("geo") != geo or any(after.get(key) != value for key, value in before.items() if key not in {"geo", "location_source"}):
                    raise RuntimeError("Facility location reconciliation failed")
                item["confirmed_facility_location_saved"] = True
            organizations[role] = org.get("metadata") or {}
            item["organization_metadata_fields"] = sorted(organizations[role])
            item["has_facility_geo"] = bool(organizations[role].get("geo"))
            item["region"] = organizations[role].get("region_id") or organizations[role].get("region") or organizations[role].get("city")
            if role == "bank":
                bank_id = identity.get("bank_id")
                inventory = get("/match-svc/api/v1/inventory")
                item["inventory"] = [{field: unit.get(field) for field in
                                      ("unit_id", "group", "component", "status", "collected_at", "expires_at")}
                                     for unit in inventory.get("units", [])]
            else:
                context = get("/match-svc/api/v1/hospital/context")
                item["visible_banks"] = len(context.get("banks", []))
        else:
            profile = get("/match-svc/api/v1/auth/donor-profile")
            item["O_positive_RBC_compatible"] = profile.get("blood_group") in {"O+", "O-"}
            item["available"] = profile.get("availability") == "available"
            item["contact_consent"] = profile.get("consent_contact")
            item["eligibility_status"] = profile.get("eligibility_status")
            deadline = profile.get("next_eligible_at")
            item["deferral_active"] = bool(deadline and datetime.fromisoformat(deadline.replace("Z", "+00:00")) > datetime.now(timezone.utc))
        report["roles"][role] = item
    report["bank_visible_to_hospital"] = bool(bank_id and any(bank.get("bank_id") == bank_id for bank in context.get("banks", [])))
    (ROOT / "docs/production-scenario-preflight.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

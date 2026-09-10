"""Authenticated production GET checks. Credentials/tokens are never persisted."""
import json
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone

import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("BLOODNET_VERIFY_API_BASE", "https://bloodnet-api-qt4toyt7fq-el.a.run.app")


def main():
    config = {}
    for line in (ROOT / "web/app/.env.production").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip().strip('"').strip("'")
    email = os.environ.pop("BLOODNET_VERIFY_EMAIL")
    password = os.environ.pop("BLOODNET_VERIFY_PASSWORD")
    api_key = config["VITE_IDENTITY_PLATFORM_API_KEY"]
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "api_base": BASE, "api": []}
    auth = requests.post(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}",
                         json={"email": email, "password": password, "returnSecureToken": True}, timeout=30)
    del password
    report["sign_in_status"] = auth.status_code
    body = auth.json()
    if not auth.ok:
        report["sign_in_error"] = body.get("error", {}).get("message", "Authentication failed")
    else:
        token = body["idToken"]
        lookup = requests.post(f"https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={api_key}", json={"idToken": token}, timeout=30)
        report["email_verified"] = lookup.json().get("users", [{}])[0].get("emailVerified")
        iam = subprocess.run([r"C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd", "auth", "print-identity-token"], capture_output=True, text=True, check=True).stdout.strip()
        headers = {"Authorization": f"Bearer {token}", "X-Serverless-Authorization": f"Bearer {iam}"}
        paths = ["/match-svc/api/v1/me", "/match-svc/api/v1/auth/me", "/match-svc/api/v1/me/session", "/api/v1/capabilities", "/match-svc/api/v1/organizations/me", "/match-svc/api/v1/auth/region-preference", "/match-svc/api/v1/inventory", "/match-svc/api/v1/reservations", "/match-svc/api/v1/reservations/pending", "/match-svc/api/v1/recommendations?state=AWAITING_APPROVAL", "/match-svc/api/v1/inventory/expiry-risk", "/match-svc/api/v1/notifications", "/match-svc/api/v1/cases?limit=10", "/match-svc/api/v1/audit", "/match-svc/api/v1/regional/forecast", "/match-svc/api/v1/auth/donor-profile", "/swarm-svc/api/v1/opportunities"]
        paths += ["/match-svc/api/v1/hospital/context", "/match-svc/api/v1/hospital/inventory", "/match-svc/api/v1/notifications/delivery-operations"]
        if os.environ.get("BLOODNET_VERIFY_PROFILE_ONLY") == "true":
            paths = ["/match-svc/api/v1/me", "/match-svc/api/v1/auth/donor-profile"]
        subject_id = None
        for path in paths:
            started = time.monotonic()
            try:
                response = requests.get(BASE + path, headers=headers, timeout=45)
                item = {"method": "GET", "path": path, "status": response.status_code, "duration_ms": round((time.monotonic()-started)*1000, 1)}
                try:
                    result = response.json()
                except ValueError:
                    result = {}
                    item["non_json_response"] = True
                if isinstance(result, dict):
                    item["keys"] = sorted(result)
                    item["counts"] = {key: len(value) for key, value in result.items() if isinstance(value, list)}
                    if path == "/match-svc/api/v1/me" and response.ok:
                        subject_id = result.get("subject_id") or result.get("id")
                        report["identity"] = {"role": result.get("role"), "status": result.get("status"), "has_bank_scope": bool(result.get("bank_id")), "has_hospital_scope": bool(result.get("hospital_id")), "has_region_scope": bool(result.get("region_id")), "mfa_enabled": result.get("mfa_enabled")}
                        if result.get("role") == "donor" and subject_id:
                            paths.append("/swarm-svc/api/v1/opportunities?donor_id=" + str(subject_id))
                    if path == "/match-svc/api/v1/auth/donor-profile" and response.ok:
                        report["donor_profile"] = {key: result.get(key) for key in ("profile_complete", "eligibility_status", "availability_status") if key in result}
                        report["donor_profile"]["has_city"] = bool(result.get("city"))
                        report["donor_profile"]["has_blood_group"] = bool(result.get("blood_group"))
                        report["donor_profile"]["compatible_with_O_positive_RBC_request"] = result.get("blood_group") in {"O+", "O-"}
                        report["donor_profile"]["contact_consent"] = result.get("consent_contact")
                        report["donor_profile"]["availability"] = result.get("availability")
                    if not response.ok and isinstance(result.get("detail"), str):
                        # Avoid raw 500 messages, which may include SQL or profile data.
                        item["detail"] = result["detail"] if response.status_code < 500 else "Server error; details omitted"
            except requests.RequestException as exc:
                item = {"path": path, "error": type(exc).__name__}
            item["path"] = path.split("?donor_id=")[0] + ("?donor_id=<self>" if "?donor_id=" in path else "")
            report["api"].append(item)
            print(f"{item['path']}: {item.get('status', item.get('error'))}", flush=True)
    output = os.environ.get("BLOODNET_VERIFY_REPORT", "docs/authenticated-production-review.json")
    (ROOT / output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "api"}))


if __name__ == "__main__":
    main()

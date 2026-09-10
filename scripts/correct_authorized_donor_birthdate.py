"""Apply only the user's explicit Mitchell DOB correction and reconcile it."""
import json
import os
from datetime import datetime, timezone
import requests
from stage_mvp_candidate import run, ROOT


def main():
    email = os.environ.pop("BLOODNET_VERIFY_EMAIL")
    password = os.environ.pop("BLOODNET_VERIFY_PASSWORD")
    if email != "mitchell.tucker3214@gmail.com":
        raise RuntimeError("This correction is authorized only for the supplied second donor")
    config = dict(line.split("=", 1) for line in (ROOT / "web/app/.env.production").read_text().splitlines() if "=" in line and not line.startswith("#"))
    key = config["VITE_IDENTITY_PLATFORM_API_KEY"].strip().strip('"')
    auth = requests.post(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={key}",
                         json={"email": email, "password": password, "returnSecureToken": True}, timeout=30)
    del password
    auth.raise_for_status()
    headers = {"Authorization": "Bearer " + auth.json()["idToken"], "X-Serverless-Authorization": "Bearer " + run("auth", "print-identity-token").stdout.strip()}
    report = {"at": datetime.now(timezone.utc).isoformat(), "authorized_birth_date": "1999-10-07", "results": {},
              "observed_api_side_effect": "The existing profile API recomputed next_eligible_at during the correction; source now preserves existing first-donation eligibility timestamps."}
    for name, base in (("live", "https://bloodnet-api-qt4toyt7fq-el.a.run.app"),
                       ("candidate", "https://bloodnet-mvp-review-qt4toyt7fq-el.a.run.app")):
        url = base + "/match-svc/api/v1/auth/donor-profile"
        before_response = requests.get(url, headers=headers, timeout=30)
        before_response.raise_for_status()
        before = before_response.json()
        if before.get("date_of_birth") == "1999-10-07":
            report["results"][name] = {"status": 200, "changed_fields": [], "reconciled": True, "already_correct": True}
            continue
        update = requests.put(url, headers=headers, json={**before, "date_of_birth": "1999-10-07"}, timeout=30)
        if not update.ok:
            raise RuntimeError(f"{name} profile correction returned HTTP {update.status_code}")
        after = requests.get(url, headers=headers, timeout=30)
        after.raise_for_status()
        data = after.json()
        changed = [field for field, value in before.items() if data.get(field) != value]
        assert data["date_of_birth"] == "1999-10-07"
        equivalent_region = str(data.get("region_id", "")).casefold() == str(before.get("region_id", "")).casefold()
        allowed = {"date_of_birth", "region_id"} if equivalent_region else {"date_of_birth"}
        # The current live API recomputes this metadata as "now" on every
        # profile save for first-time donors. Verify that it does not impose
        # a deferral or alter recorded donation history, and report the field.
        if before.get("last_donation_at") is None and data.get("next_eligible_at"):
            if datetime.fromisoformat(data["next_eligible_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                allowed.add("next_eligible_at")
        if set(changed) - allowed:
            raise RuntimeError("Unexpected changed profile fields: " + ", ".join(changed))
        report["results"][name] = {"status": update.status_code, "changed_fields": changed, "reconciled": True}
    (ROOT / "docs/authorized-donor-birthdate-correction.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()

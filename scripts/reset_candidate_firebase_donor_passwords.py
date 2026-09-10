"""Reset only the two candidate Firebase donor passwords through the admin API."""
import json
from pathlib import Path

import requests

from stage_mvp_candidate import run

ROOT = Path(__file__).resolve().parents[1]
project = "project-bae56d7f-3ee2-48fc-bdd"
credentials = json.loads((ROOT / ".ui-check-credentials.json").read_text(encoding="utf-8"))
token = run("auth", "print-access-token").stdout.strip()
for local_id, email in (
    ("bloodnet-candidate_donor_1", "imzoya.shakeel@gmail.com"),
    ("bloodnet-candidate_donor_2", "mitchell.tucker3214@gmail.com"),
):
    response = requests.post(
        f"https://identitytoolkit.googleapis.com/v1/projects/{project}/accounts:update",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "localId": local_id,
            "password": credentials["donor"]["password"],
            "emailVerified": True,
        },
        timeout=30,
    )
    response.raise_for_status()
    print(json.dumps({"email": email, "updated": True}))

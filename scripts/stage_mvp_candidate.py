"""Stage an isolated Cloud Run candidate; never changes the live service's traffic.

Credentials are read from Secret Manager and sent through stdin, never files/logs.
The candidate database contains validation data only and must not be promoted as
the production database. Publishing the fixes requires a separate live release.
"""
import json
import subprocess
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
SDK = Path(r"C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk")
CLI = [str(SDK / "platform/bundledpython/python.exe"), str(SDK / "lib/gcloud.py")]
PROJECT = "project-bae56d7f-3ee2-48fc-bdd"
REGION = "asia-south1"
SERVICE = "bloodnet-mvp-review"
DATABASE = "bloodnet_mvp_review_20260910"
SECRET = "bloodnet-mvp-review-database-url"
TOPIC = "bloodnet-mvp-review-events"


def run(*args, input=None, check=True):
    result = subprocess.run([*CLI, *args, f"--project={PROJECT}", "--quiet"],
                            input=input, capture_output=True, text=True, timeout=600)
    if check and result.returncode:
        # Never expose a command line or secret-bearing stdout on failures.
        raise RuntimeError(f"Candidate staging failed at {' '.join(args[:3])}: {result.stderr[-1800:]}")
    return result


def main():
    from psycopg.conninfo import conninfo_to_dict
    live = json.loads(run("run", "services", "describe", "bloodnet-api", f"--region={REGION}", "--format=json").stdout)
    spec = live["spec"]["template"]
    aliases = dict(item.split(":", 1) for item in spec["metadata"]["annotations"].get("run.googleapis.com/secrets", "").split(",") if item)
    account = spec["spec"]["serviceAccountName"]
    values, secrets = {}, {}
    for item in spec["spec"]["containers"][0]["env"]:
        if "value" in item:
            values[item["name"]] = item["value"]
        else:
            ref = item["valueFrom"]["secretKeyRef"]
            secrets[item["name"]] = aliases.get(ref["name"], ref["name"]).split("/")[-1] + ":" + ref["key"]
    dsn = conninfo_to_dict(run("secrets", "versions", "access", "latest", "--secret=bloodnet-database-url").stdout.strip())
    socket = f"/cloudsql/{PROJECT}:{REGION}:bloodnet-postgres"
    candidate_dsn = f"postgresql://{quote(dsn['user'], safe='')}:{quote(dsn['password'], safe='')}@/{DATABASE}?host={socket}"
    if run("secrets", "describe", SECRET, check=False).returncode:
        run("secrets", "create", SECRET, "--replication-policy=automatic")
    version = run("secrets", "versions", "add", SECRET, "--data-file=-", "--format=value(name)", input=candidate_dsn).stdout.strip().split("/")[-1]
    del candidate_dsn, dsn
    run("secrets", "add-iam-policy-binding", SECRET, f"--member=serviceAccount:{account}", "--role=roles/secretmanager.secretAccessor")
    if run("pubsub", "topics", "describe", TOPIC, check=False).returncode:
        run("pubsub", "topics", "create", TOPIC)
    values.update(BLOODNET_ENABLE_NOTIFICATIONS="false", BLOODNET_NOTIFICATION_DEFAULT_CHANNEL="email",
                  BLOODNET_NOTIFICATION_PROVIDER="smtp", BLOODNET_EMAIL_SENDER_NAME="BloodNet",
                  BLOODNET_PUBSUB_TOPIC=TOPIC, BLOODNET_BQ_EVENTS_ENABLED="false")
    secrets["BLOODNET_DATABASE_URL"] = f"{SECRET}:{version}"
    result = run("run", "deploy", SERVICE, f"--region={REGION}",
                 f"--image={REGION}-docker.pkg.dev/{PROJECT}/bloodnet/bloodnet-api:mvp-review-20260910",
                 f"--service-account={account}", f"--set-cloudsql-instances={PROJECT}:{REGION}:bloodnet-postgres",
                 "--no-allow-unauthenticated", "--ingress=all", "--cpu=2", "--memory=2Gi", "--max-instances=2",
                 "--set-env-vars=^|^" + "|".join(f"{key}={value}" for key, value in values.items()),
                 "--set-secrets=" + ",".join(f"{key}={value}" for key, value in secrets.items()), "--format=json")
    deployed = json.loads(result.stdout)
    report = {"service": SERVICE, "database": DATABASE, "url": deployed["status"]["url"],
              "revision": deployed["status"]["latestReadyRevisionName"], "notifications_enabled": False,
              "live_service_modified": False, "database_scope": "isolated validation database on production Cloud SQL instance"}
    (ROOT / "docs/mvp-candidate-deployment.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()

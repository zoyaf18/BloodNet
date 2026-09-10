"""Configure only the candidate for the two user-authorized test recipients."""
import json
from stage_mvp_candidate import run, ROOT, PROJECT, REGION, SERVICE


def main():
    account = f"bloodnet-runtime@{PROJECT}.iam.gserviceaccount.com"
    origin = "https://project-bae56d7f-3ee2-48fc-bdd--mvp-review-20260910-bvz9wm6e.web.app"
    base = json.loads((ROOT / "docs/mvp-candidate-deployment.json").read_text())["url"]
    queue = "bloodnet-mvp-review-delivery"
    if run("tasks", "queues", "describe", queue, f"--location={REGION}", check=False).returncode:
        run("tasks", "queues", "create", queue, f"--location={REGION}",
            "--max-dispatches-per-second=1", "--max-concurrent-dispatches=1", "--max-attempts=3")
    run("projects", "add-iam-policy-binding", PROJECT, f"--member=serviceAccount:{account}",
        "--role=roles/cloudtasks.enqueuer", "--condition=None")
    run("iam", "service-accounts", "add-iam-policy-binding", account,
        f"--member=serviceAccount:{account}", "--role=roles/iam.serviceAccountUser")
    run("run", "services", "add-iam-policy-binding", SERVICE, f"--region={REGION}",
        f"--member=serviceAccount:{account}", "--role=roles/run.invoker")
    values = {
        "BLOODNET_ENABLE_NOTIFICATIONS": "true", "BLOODNET_NOTIFICATION_DEFAULT_CHANNEL": "email",
        "BLOODNET_NOTIFICATION_PROVIDER": "smtp", "BLOODNET_EMAIL_TEST_MODE": "true",
        "BLOODNET_EMAIL_TEST_RECIPIENTS": "imzoya.shakeel@gmail.com,mitchell.tucker3214@gmail.com",
        "BLOODNET_CLOUD_TASKS_QUEUE": queue, "BLOODNET_NOTIFICATION_DELIVERY_URL": base + "/match-svc/internal/notifications/deliver",
        "BLOODNET_SERVICE_AUDIENCE": base, "BLOODNET_TRUSTED_SERVICE_ACCOUNTS": account,
        "BLOODNET_ALLOWED_ORIGINS": origin, "BLOODNET_FRONTEND_ORIGIN": origin,
    }
    deployed = json.loads(run("run", "services", "update", SERVICE, f"--region={REGION}",
        f"--image={REGION}-docker.pkg.dev/{PROJECT}/bloodnet/bloodnet-api:mvp-review-worker-20260910",
        "--update-env-vars=^|^" + "|".join(f"{key}={value}" for key, value in values.items()), "--format=json").stdout)
    report = {"service": SERVICE, "revision": deployed["status"]["latestReadyRevisionName"],
              "frontend_origin": origin, "worker_audience": base, "queue": queue,
              "test_recipient_count": 2, "notifications_enabled": True, "live_service_modified": False}
    (ROOT / "docs/mvp-candidate-delivery-config.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()

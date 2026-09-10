"""Publish the reviewed gateway specification to a separate candidate gateway."""
import json
from stage_mvp_candidate import ROOT, run, PROJECT, REGION, SERVICE


def main():
    deployment = json.loads((ROOT / "docs/mvp-candidate-deployment.json").read_text())
    source = (ROOT / "infra/terraform/edge_security.tf").read_text()
    start = source.index('swagger: "2.0"')
    spec = source[start:source.index("\nEOT", start)]
    spec = spec.replace("${var.project_id}", PROJECT).replace("${local.gateway_backend_address}", deployment["url"])
    path = ROOT / ".logs/mvp-candidate-openapi.yaml"
    path.write_text(spec)
    account = "862096053766-compute@developer.gserviceaccount.com"
    run("run", "services", "add-iam-policy-binding", SERVICE, f"--region={REGION}",
        f"--member=serviceAccount:{account}", "--role=roles/run.invoker")
    config = "bloodnet-mvp-review-20260910"
    run("api-gateway", "api-configs", "create", config, "--api=bloodnet-gateway",
        f"--openapi-spec={path}", f"--backend-auth-service-account={account}")
    result = run("api-gateway", "gateways", "create", "bloodnet-mvp-review",
                 "--api=bloodnet-gateway", f"--api-config={config}", "--location=us-central1", "--format=json")
    gateway = json.loads(result.stdout)
    report = {"config": config, "gateway": "bloodnet-mvp-review", "url": "https://" + gateway["defaultHostname"],
              "backend": deployment["url"], "live_gateway_modified": False}
    (ROOT / "docs/mvp-candidate-gateway.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()

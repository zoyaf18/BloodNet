from pathlib import Path


def test_deployment_retries_artifact_registry_digest_lookup_failures():
    deploy = Path("scripts/deploy.ps1").read_text(encoding="utf-8")

    assert "$digestOutput = @(gcloud artifacts docker images describe" in deploy
    assert "$lastDescribeExitCode = $LASTEXITCODE" in deploy
    assert "Write-Warning \"Artifact Registry image lookup failed" in deploy
    assert "if ($lastDescribeExitCode -ne 0)" in deploy
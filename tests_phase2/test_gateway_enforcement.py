from pathlib import Path
import re


def test_gateway_config_defaults_cannot_select_a_stale_config():
    variables = Path("infra/terraform/variables.tf").read_text(encoding="utf-8")
    deploy = Path("scripts/deploy.ps1").read_text(encoding="utf-8")

    assert 'variable "gateway_api_config_id"' in variables
    assert 'description = "API Gateway config ID.' in variables
    assert 'default     = "bloodnet-api-config-' not in variables
    assert '$gatewayConfigId = "bloodnet-api-config-$releaseId"' in deploy
    assert '$gatewayConfigId = $env:TF_VAR_gateway_api_config_id' not in deploy


def test_gateway_supports_browser_preflight_for_each_published_path():
    text = Path("infra/terraform/edge_security.tf").read_text(encoding="utf-8")
    spec = text[text.index('swagger: "2.0"'):text.index("\nEOT", text.index('swagger: "2.0"'))]
    blocks = re.split(r"(?m)(?=^  /[^\n]+:\s*$)", spec)[1:]
    assert blocks
    missing = [block.splitlines()[0].strip() for block in blocks if not re.search(r"^    options:", block, re.M)]
    assert not missing, f"Browser Authorization headers require preflight: {missing}"
    for path in ("/match-svc/api/v1/reservations", "/match-svc/api/v1/inventory/expiry-risk", "/match-svc/api/v1/cases/{case_id}/outcomes", "/match-svc/api/v1/auth/donor-profile/donations/complete"):
        assert f"  {path}:" in spec
    ids = re.findall(r"operationId: (\S+)", spec)
    assert len(ids) == len(set(ids))


def test_gateway_public_health_and_jwt_protects_everything_else():
    tf = Path("infra/terraform/edge_security.tf").read_text(encoding="utf-8")
    main_tf = Path("infra/terraform/main.tf").read_text(encoding="utf-8")

    assert "/health:" in tf
    assert "/match-svc/api/v1/inventory:" in tf
    assert "/match-svc/api/v1/cases:" in tf
    assert "/swarm-svc/api/v1/opportunities:" in tf
    assert "/agent-svc/api/v1/investigations/gemini:" in tf
    assert "/match-svc/api/v1/auth/login:" in tf
    assert "/match-svc/api/v1/auth/signup:" in tf
    assert "/match-svc/api/v1/invitations:" in tf
    assert "/match-svc/api/v1/invitations/{token}/accept:" in tf
    assert "/match-svc/api/v1/auth/me:" in tf
    assert "/match-svc/api/v1/auth/location:" in tf
    assert "/match-svc/api/v1/auth/password-reset:" in tf
    assert "/match-svc/api/v1/auth/mfa/verify:" in tf
    for index, line in enumerate(tf.splitlines()):
        if "security: []" in line:
            assert any(
                candidate.strip() == "options:"
                for candidate in tf.splitlines()[max(0, index - 8):index]
            )
    assert "firebase_auth" in tf
    assert tf.count("- api_key: []") >= 20
    assert "x-google-issuer" in tf
    assert "x-google-audience" in tf
    assert "x-google-allow: all" not in tf
    assert "type: apiKey" in tf
    assert "gcp-sa-apigateway.iam.gserviceaccount.com" in main_tf
    assert "allUsers" not in main_tf

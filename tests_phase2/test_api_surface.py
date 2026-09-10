from pathlib import Path
import ast


ROOT = Path(__file__).resolve().parents[1]


def test_service_api_documentation_is_disabled():
    service_sources = [ROOT / "main.py", *sorted((ROOT / "services").glob("*/main.py"))]
    for source in service_sources:
        text = source.read_text(encoding="utf-8")
        assert "docs_url=None" in text, source
        assert "redoc_url=None" in text, source
        assert "openapi_url=None" in text, source


def test_gateway_rejects_undocumented_backend_paths():
    text = (ROOT / "infra" / "terraform" / "edge_security.tf").read_text(encoding="utf-8")
    assert "x-google-allow: all" not in text
    for path in (
        "/api/v1/capabilities:",
        "/api/v1/inbound/messaging/events:",
        "/agent-svc/api/v1/sops/status:",
        "/agent-svc/api/v1/sops/search:",
        "/match-svc/api/v1/notifications/delivery-operations:",
        "/match-svc/api/v1/notifications/provider-receipt:",
        "/match-svc/api/v1/public/dashboard:",
        "/match-svc/api/v1/public/demo-scenarios:",
        "/match-svc/api/v1/public/demo-scenarios/{scenario_id}/run:",
        "/match-svc/api/v1/organizations/onboard:",
        "/match-svc/api/v1/match:",
        "/match-svc/api/v1/audit/search:",
        "/match-svc/api/v1/me/session:",
        "/match-svc/api/v1/privacy/policies:",
        "/match-svc/api/v1/auth/donor-profile:",
        "/match-svc/api/v1/inventory/transfers:",
    ):
        assert path in text


def test_protected_service_handlers_require_identity_and_role():
    handlers = {
        ROOT / "services" / "match-svc" / "main.py": {
            "api_score_donors",
            "api_inventory_match",
        },
        ROOT / "services" / "intake-svc" / "main.py": {"extract"},
        ROOT / "services" / "graph-svc" / "main.py": {"analyze"},
        ROOT / "services" / "copilot-svc" / "main.py": {"transfer"},
    }
    for source, expected_names in handlers.items():
        tree = ast.parse(source.read_text(encoding="utf-8"))
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in expected_names:
            function = functions[name]
            arguments = {argument.arg for argument in function.args.args}
            calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
            assert "identity" in arguments, f"{source}:{name} has no identity argument"
            assert any(
                isinstance(call.func, ast.Name) and call.func.id == "Depends"
                and any(isinstance(argument, ast.Name) and argument.id == "get_identity" for argument in call.args)
                for call in calls
            ), f"{source}:{name} does not depend on get_identity"
            assert any(
                isinstance(call.func, ast.Name) and call.func.id == "require_role"
                for call in calls
            ), f"{source}:{name} does not enforce a role"


def test_frontend_has_one_authenticated_role_router():
    text = (ROOT / "web" / "app" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert "AuthenticatedApp" not in text
    assert "<AppShell onSignOut={handleSignOut} />" in text


def test_ui_hides_operational_controls_from_auditors_and_restricts_organization_management():
    text = (ROOT / "web" / "app" / "src" / "AppShell.tsx").read_text(encoding="utf-8")
    features = (ROOT / "web" / "app" / "src" / "FeaturePanels.tsx").read_text(encoding="utf-8")
    regional_surface = (ROOT / "web" / "app" / "src" / "SurfacesEnhanced.tsx").read_text(encoding="utf-8")
    assert 'const auditor = identity.role === "auditor";' in text
    assert 'view === "graph"' not in text
    assert "NetworkIntelligencePanel" in features
    assert "<NetworkIntelligencePanel" in regional_surface
    assert "{(auditor || regionalAdmin) && (" in text and "Audit explorer" in text
    assert "{regionalAdmin && (" in text and "Organization" in text
    assert '{view === "org" && regionalAdmin' in text
    assert 'platform_admin' not in text

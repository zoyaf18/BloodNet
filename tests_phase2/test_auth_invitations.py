import importlib


def test_invitation_routes_are_registered(monkeypatch):
    monkeypatch.setenv("BLOODNET_DATABASE_URL", "postgresql://bloodnet:test@localhost/bloodnet")
    auth_api = importlib.import_module("auth_api")

    paths = {route.path for route in auth_api.invitation_router.routes}

    assert "/api/v1/invitations" in paths
    assert "/api/v1/invitations/{token}/accept" in paths
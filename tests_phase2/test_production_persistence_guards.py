import importlib
from pathlib import Path

import pytest

from contracts.config import ConfigurationError, load_runtime_config


ROOT = Path(__file__).resolve().parents[1]


def test_production_requires_database_url(monkeypatch):
    monkeypatch.setenv("BLOODNET_ENV", "production")
    monkeypatch.setenv("GCP_PROJECT_ID", "bloodnet-test")
    monkeypatch.setenv("GCP_REGION", "us-central1")
    monkeypatch.setenv("BLOODNET_AUTH_MODE", "production")
    monkeypatch.setenv("BLOODNET_JWT_SECRET", "test-secret")
    monkeypatch.setenv("BLOODNET_ALLOWED_ORIGINS", "https://bloodnet.example")
    monkeypatch.setenv("BLOODNET_EVENT_TRANSPORT", "pubsub")
    monkeypatch.delenv("BLOODNET_DATABASE_URL", raising=False)

    with pytest.raises(ConfigurationError, match="BLOODNET_DATABASE_URL"):
        load_runtime_config()


def test_demo_requires_production_configuration(monkeypatch):
    monkeypatch.setenv("BLOODNET_ENV", "demo")
    monkeypatch.delenv("BLOODNET_DATABASE_URL", raising=False)
    monkeypatch.delenv("BLOODNET_JWT_SECRET", raising=False)
    monkeypatch.delenv("BLOODNET_ALLOWED_ORIGINS", raising=False)

    with pytest.raises(ConfigurationError, match="BLOODNET_DATABASE_URL"):
        load_runtime_config()


def test_production_rejects_short_jwt_secret(monkeypatch):
    monkeypatch.setenv("BLOODNET_ENV", "production")
    monkeypatch.setenv("GCP_PROJECT_ID", "bloodnet-test")
    monkeypatch.setenv("GCP_REGION", "us-central1")
    monkeypatch.setenv("BLOODNET_AUTH_MODE", "production")
    monkeypatch.setenv("BLOODNET_JWT_SECRET", "too-short")
    monkeypatch.setenv("BLOODNET_ALLOWED_ORIGINS", "https://bloodnet.example")
    monkeypatch.setenv("BLOODNET_EVENT_TRANSPORT", "pubsub")
    monkeypatch.setenv("BLOODNET_DATABASE_URL", "postgresql://bloodnet:test@localhost/bloodnet")

    with pytest.raises(ConfigurationError, match="at least 32 bytes"):
        load_runtime_config()


def test_auth_utils_rejects_missing_jwt_secret_in_production(monkeypatch):
    monkeypatch.setenv("BLOODNET_ENV", "production")
    monkeypatch.setenv("BLOODNET_AUTH_MODE", "development")
    monkeypatch.delenv("BLOODNET_JWT_SECRET", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)

    import contracts.auth_utils as auth_utils
    with pytest.raises(RuntimeError, match="BLOODNET_JWT_SECRET"):
        monkeypatch.delenv("BLOODNET_AUTH_MODE", raising=False)
        monkeypatch.setenv("BLOODNET_AUTH_MODE", "production")
        importlib.reload(auth_utils)


def test_match_entrypoint_has_no_sqlite_production_path():
    source = (ROOT / "services" / "match-svc" / "main.py").read_text()

    assert "sqlite3" not in source
    assert "SQLite" not in source
    assert "BLOODNET_DB_PATH" not in source


def test_cloud_run_does_not_expose_public_invocation():
    main_source = (ROOT / "infra" / "terraform" / "main.tf").read_text()
    data_protection_source = (ROOT / "infra" / "terraform" / "data_protection.tf").read_text()

    assert 'var.gateway_enabled ? "INGRESS_TRAFFIC_ALL" : "INGRESS_TRAFFIC_INTERNAL_ONLY"' in main_source
    assert "invoker_iam_disabled = false" in main_source
    assert 'member   = "allUsers"' not in main_source
    assert 'gcp-sa-pubsub.iam.gserviceaccount.com' in main_source
    assert 'gcp-sa-secretmanager.iam.gserviceaccount.com' in data_protection_source
    assert 'roles/pubsub.publisher' in data_protection_source


def test_production_database_url_is_normalized_to_cloudsql_socket(monkeypatch):
    monkeypatch.setenv("BLOODNET_ENV", "production")
    monkeypatch.setenv("GCP_PROJECT_ID", "project-bae56d7f-3ee2-48fc-bdd")
    monkeypatch.setenv("GCP_REGION", "asia-south1")
    monkeypatch.setenv("BLOODNET_AUTH_MODE", "production")
    monkeypatch.setenv("BLOODNET_JWT_SECRET", "a-very-long-and-secure-production-jwt-secret-000")
    monkeypatch.setenv("BLOODNET_ALLOWED_ORIGINS", "https://bloodnet.example")
    monkeypatch.setenv("BLOODNET_EVENT_TRANSPORT", "pubsub")
    monkeypatch.setenv("BLOODNET_DATABASE_URL", "postgresql://bloodnet:secret@10.0.0.5:5432/bloodnet")
    monkeypatch.setenv("CLOUD_SQL_CONNECTION_NAME", "project-bae56d7f-3ee2-48fc-bdd:asia-south1:bloodnet-postgres")

    cfg = load_runtime_config()

    assert cfg.database_url.startswith("postgresql://bloodnet:secret@/")
    assert "host=/cloudsql/project-bae56d7f-3ee2-48fc-bdd:asia-south1:bloodnet-postgres" in cfg.database_url

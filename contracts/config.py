"""Validated runtime configuration for the BloodNet API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote, urlparse


class ConfigurationError(RuntimeError):
    """Raised when deployment configuration is incomplete or unsafe."""


def _normalize_database_url(database_url: str | None, project_id: str | None, region: str | None) -> str | None:
    """Normalize PostgreSQL DSNs for Cloud SQL socket-based connectivity in production.

    Cloud Run and the mounted Cloud SQL socket expect a socket SQLAlchemy/psycopg DSN of the
    form: postgresql://USER:PASSWORD@/DB?host=/cloudsql/PROJECT:REGION:INSTANCE

    The live secret may still be generated with a private IP host during rollout. Rewrite that
    stale form automatically so the runtime remains able to connect without an explicit
    authorized-networks workaround.
    """
    if not database_url:
        return None

    if "host=/cloudsql/" in database_url:
        return database_url

    try:
        parsed = urlparse(database_url)
    except ValueError:
        return database_url

    if not parsed.scheme or parsed.scheme.lower() not in {"postgresql", "postgres"}:
        return database_url

    conn_name = (
        os.getenv("CLOUD_SQL_CONNECTION_NAME")
        or os.getenv("INSTANCE_CONNECTION_NAME")
        or os.getenv("BLOODNET_CLOUD_SQL_INSTANCE")
        or os.getenv("BLOODNET_CLOUD_SQL_INSTANCE_NAME")
    )
    if not conn_name and project_id and region:
        instance_name = os.getenv("BLOODNET_CLOUD_SQL_INSTANCE_NAME") or "bloodnet-postgres"
        conn_name = f"{project_id}:{region}:{instance_name}"
    if not conn_name:
        return database_url

    username = parsed.username or "bloodnet"
    password = parsed.password or ""
    database = parsed.path.lstrip("/") or "bloodnet"
    return (
        f"postgresql://{quote(username, safe='')}:{quote(password, safe='')}@/{database}"
        f"?host=/cloudsql/{conn_name}"
    )


@dataclass(frozen=True)
class RuntimeConfig:
    environment: str
    project_id: str | None
    region: str | None
    database_url: str | None
    event_transport: str
    pubsub_topic: str
    allowed_origins: tuple[str, ...]


def load_runtime_config() -> RuntimeConfig:
    environment = os.getenv("BLOODNET_ENV", "local").lower()
    production = environment in {"demo", "production"}
    project_id = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    region = os.getenv("GCP_REGION") or os.getenv("GOOGLE_CLOUD_LOCATION")
    database_url = os.getenv("BLOODNET_DATABASE_URL")
    event_transport = os.getenv("BLOODNET_EVENT_TRANSPORT", "local").lower()
    pubsub_topic = os.getenv("BLOODNET_PUBSUB_TOPIC", "bloodnet-events")
    origins = tuple(origin.strip() for origin in os.getenv("BLOODNET_ALLOWED_ORIGINS", "").split(",") if origin.strip())
    database_url = _normalize_database_url(database_url, project_id, region)

    if not production:
        return RuntimeConfig(environment, project_id, region, database_url, event_transport, pubsub_topic, origins)

    required = {
        "GCP_PROJECT_ID": project_id,
        "GCP_REGION": region,
        "BLOODNET_DATABASE_URL": database_url,
        "BLOODNET_AUTH_MODE": os.getenv("BLOODNET_AUTH_MODE"),
        "BLOODNET_JWT_SECRET": os.getenv("BLOODNET_JWT_SECRET"),
        "BLOODNET_ALLOWED_ORIGINS": os.getenv("BLOODNET_ALLOWED_ORIGINS"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ConfigurationError(
            "Missing production configuration: " + ", ".join(missing)
        )
    if event_transport != "pubsub":
        raise ConfigurationError("BLOODNET_EVENT_TRANSPORT must be 'pubsub' in production")
    if os.getenv("BLOODNET_AUTH_MODE") not in {"production", "identity-platform"}:
        raise ConfigurationError("BLOODNET_AUTH_MODE must be 'identity-platform' in production")
    if not origins or "*" in origins:
        raise ConfigurationError("BLOODNET_ALLOWED_ORIGINS must contain explicit HTTPS origins in production")
    jwt_secret = os.getenv("BLOODNET_JWT_SECRET", "")
    if len(jwt_secret.encode("utf-8")) < 32:
        raise ConfigurationError("BLOODNET_JWT_SECRET must be at least 32 bytes in production")
    if any(
        not origin.startswith("https://")
        and not (environment == "demo" and origin.startswith("http://localhost:"))
        for origin in origins
    ):
        raise ConfigurationError("Production CORS origins must use https://")

    return RuntimeConfig(environment, project_id, region, database_url, event_transport, pubsub_topic, origins)
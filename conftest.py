"""Pytest infrastructure for isolated PostgreSQL integration tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import sys
from uuid import uuid4

import psycopg
import pytest


_CONTAINER: str | None = None


@pytest.fixture(autouse=True)
def isolate_auth_repository_globals():
    """API tests replace module globals; never let a fake leak into another test."""
    database_url = os.environ.get("BLOODNET_DATABASE_URL")
    yield
    if database_url:
        from contracts.auth_repository import AuthRepository
        for module in tuple(sys.modules.values()):
            filename = str(getattr(module, "__file__", "") or "").replace("\\", "/")
            if filename.endswith(("/match-svc/auth_api.py", "/match-svc/main.py")) and hasattr(module, "auth_repo"):
                module.auth_repo = AuthRepository(database_url)


def _docker(*arguments: str) -> str:
    result = subprocess.run(["docker", *arguments], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def pytest_configure(config: pytest.Config) -> None:
    global _CONTAINER
    if os.getenv("BLOODNET_DATABASE_URL"):
        return
    if not shutil.which("docker"):
        raise RuntimeError("PostgreSQL integration tests require Docker or BLOODNET_DATABASE_URL")

    _CONTAINER = f"bloodnet-pytest-{uuid4().hex[:12]}"
    try:
        _docker(
            "run", "--detach", "--name", _CONTAINER,
            "--env", "POSTGRES_USER=bloodnet",
            "--env", "POSTGRES_PASSWORD=bloodnet",
            "--env", "POSTGRES_DB=bloodnet_test",
            "--publish", "127.0.0.1::5432",
            "pgvector/pgvector:pg16",
        )
        port = _docker("port", _CONTAINER, "5432/tcp").rsplit(":", 1)[-1]
        database_url = f"postgresql://bloodnet:bloodnet@127.0.0.1:{port}/bloodnet_test"
        deadline = time.monotonic() + 60
        while True:
            try:
                with psycopg.connect(database_url):
                    break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Disposable PostgreSQL did not become ready within 60 seconds")
                time.sleep(1)

        os.environ["BLOODNET_DATABASE_URL"] = database_url
        os.environ["BLOODNET_MIGRATION_RETRY_ATTEMPTS"] = "1"
        from migrations.runner import MigrationRunner
        MigrationRunner(database_url).upgrade()
    except BaseException:
        subprocess.run(["docker", "rm", "--force", _CONTAINER], check=False, capture_output=True)
        _CONTAINER = None
        raise


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _CONTAINER:
        subprocess.run(["docker", "rm", "--force", _CONTAINER], check=False, capture_output=True)

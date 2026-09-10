"""Tests for transient database failures during startup migrations."""

import psycopg

from migrations.runner import MigrationRunner


def test_upgrade_retries_transient_database_connection(monkeypatch):
    runner = MigrationRunner("postgresql://unused")
    attempts = 0

    def pending_migrations():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise psycopg.OperationalError("connection refused")
        return []

    monkeypatch.setattr(runner, "_get_pending_migrations", pending_migrations)
    monkeypatch.setattr("migrations.runner.time.sleep", lambda _: None)
    monkeypatch.setenv("BLOODNET_MIGRATION_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("BLOODNET_MIGRATION_RETRY_DELAY_SECONDS", "0")

    runner.upgrade()

    assert attempts == 3
"""Test that migrations run successfully on a fresh database."""

import os
import sys
from pathlib import Path

import psycopg
import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
if str(MIGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(MIGRATIONS_DIR))

from runner import MigrationRunner

DATABASE_URL = os.environ.get("BLOODNET_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BLOODNET_DATABASE_URL is required for deployment tests",
)


def clean_database(database_url: str) -> None:
    """Drop all tables to simulate a fresh database."""
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            # Get all user tables
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                AND table_type = 'BASE TABLE'
                """
            )
            tables = cursor.fetchall()
            
            # Drop all tables
            for (table_name,) in tables:
                cursor.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")
        
        connection.commit()


@pytest.mark.deployment
def test_migrations_run_on_empty_database():
    """Test that migrations can run successfully on a fresh database."""
    assert DATABASE_URL is not None
    
    # Clean the database to simulate fresh deployment
    clean_database(DATABASE_URL)
    
    # Run migrations
    runner = MigrationRunner(DATABASE_URL)
    runner.upgrade()
    
    # Verify required tables exist
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            required_tables = [
                "inventory_units",
                "inventory_reservations",
                "workflow_cases",
                "workflow_recommendations",
                "workflow_requests",
                "notifications",
                "audit_records",
                "donor_responses",
                "processed_events",
                "notification_outbox",
                "approval_state_tracking",
                "schema_versions",
            ]
            
            for table_name in required_tables:
                cursor.execute(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_name = %s
                        AND table_schema = 'public'
                    )
                    """,
                    (table_name,),
                )
                exists = cursor.fetchone()[0]
                assert exists, f"Table {table_name} does not exist after migrations"
            
            # Verify all migrations are recorded in schema_versions
            cursor.execute(
                "SELECT COUNT(*) FROM schema_versions"
            )
            migration_count = cursor.fetchone()[0]
            assert migration_count > 0, "No migrations were recorded"


@pytest.mark.deployment
def test_migrations_are_idempotent():
    """Test that running migrations twice succeeds without error."""
    assert DATABASE_URL is not None
    
    # Clean the database
    clean_database(DATABASE_URL)
    
    # Run migrations once
    runner = MigrationRunner(DATABASE_URL)
    runner.upgrade()
    
    # Get count of executed migrations
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM schema_versions")
            first_count = cursor.fetchone()[0]
    
    # Run migrations again
    runner.upgrade()
    
    # Verify no new migrations were executed
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM schema_versions")
            second_count = cursor.fetchone()[0]
    
    assert first_count == second_count, "Migrations were executed twice"


@pytest.mark.deployment
def test_migrations_status_command():
    """Test that the migration status command works correctly."""
    assert DATABASE_URL is not None
    
    # Clean the database
    clean_database(DATABASE_URL)
    
    # Check status before migrations
    runner = MigrationRunner(DATABASE_URL)
    pending_before = runner._get_pending_migrations()
    assert len(pending_before) > 0, "Should have pending migrations before running"
    
    # Run migrations
    runner.upgrade()
    
    # Check status after migrations
    pending_after = runner._get_pending_migrations()
    assert len(pending_after) == 0, "Should have no pending migrations after upgrade"

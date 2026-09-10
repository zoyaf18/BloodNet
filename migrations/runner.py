"""PostgreSQL migration runner for the bloodnet application."""

import os
import sys
import time
from pathlib import Path
from typing import Optional

import psycopg


class MigrationRunner:
    """Discovers, tracks, and executes SQL migrations."""

    DEFAULT_RETRY_ATTEMPTS = 6
    DEFAULT_RETRY_DELAY_SECONDS = 5.0

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.migrations_dir = Path(__file__).parent

    def _ensure_schema_versions_table(self, connection: psycopg.Connection) -> None:
        """Create schema_versions table if it doesn't exist."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version_id TEXT PRIMARY KEY,
                    migration_name TEXT NOT NULL UNIQUE,
                    executed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        connection.commit()

    def _get_executed_migrations(
        self, connection: psycopg.Connection
    ) -> set[str]:
        """Get the set of migrations that have already been executed."""
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT version_id
                    FROM schema_versions
                    ORDER BY executed_at
                    """
                )
                rows = cursor.fetchall()
            return {row[0] for row in rows}
        except psycopg.ProgrammingError:
            # Table doesn't exist yet
            return set()

    def _get_pending_migrations(self) -> list[tuple[str, Path]]:
        """
        Get pending migrations in order.
        
        Returns a list of (version_id, migration_path) tuples.
        Migrations are sorted by filename (numeric prefix order).
        """
        migration_files = sorted(
            f for f in self.migrations_dir.glob("*.sql")
            if f.name != "runner.py"  # Skip this file
        )
        
        with psycopg.connect(self.database_url) as connection:
            self._ensure_schema_versions_table(connection)
            executed = self._get_executed_migrations(connection)
        
        pending = []
        for migration_file in migration_files:
            version_id = migration_file.stem  # e.g., "001_initial_schema"
            if version_id not in executed:
                pending.append((version_id, migration_file))
        
        return pending

    def execute_migration(
        self, version_id: str, migration_path: Path
    ) -> None:
        """Execute a single migration and record it in schema_versions."""
        with psycopg.connect(self.database_url) as connection:
            # Read migration file
            migration_sql = migration_path.read_text()
            
            # Execute migration
            with connection.cursor() as cursor:
                cursor.execute(migration_sql)
                
                # Record in schema_versions
                cursor.execute(
                    """
                    INSERT INTO schema_versions (version_id, migration_name)
                    VALUES (%s, %s)
                    """,
                    (version_id, migration_path.name),
                )
            
            connection.commit()

    def upgrade(self) -> None:
        """Execute all pending migrations in order."""
        retry_attempts = int(
            os.getenv("BLOODNET_MIGRATION_RETRY_ATTEMPTS", self.DEFAULT_RETRY_ATTEMPTS)
        )
        retry_delay = float(
            os.getenv(
                "BLOODNET_MIGRATION_RETRY_DELAY_SECONDS",
                self.DEFAULT_RETRY_DELAY_SECONDS,
            )
        )

        for attempt in range(1, retry_attempts + 1):
            try:
                self._upgrade_once()
                return
            except psycopg.OperationalError as error:
                if attempt == retry_attempts:
                    raise
                print(
                    f"Database unavailable during migration attempt {attempt}/"
                    f"{retry_attempts}: {error}. Retrying in {retry_delay:g}s."
                )
                time.sleep(retry_delay)

    def _upgrade_once(self) -> None:
        """Execute migrations once; transient connection errors are retried by upgrade."""
        pending = self._get_pending_migrations()
        
        if not pending:
            print("No migrations to execute.")
            return
        
        print(f"Found {len(pending)} pending migration(s).")
        
        for version_id, migration_path in pending:
            try:
                print(f"Executing migration: {migration_path.name}")
                self.execute_migration(version_id, migration_path)
                print(f"[OK] Migration {migration_path.name} executed successfully.")
            except Exception as e:
                print(f"[ERROR] Migration {migration_path.name} failed: {e}")
                raise

    def status(self) -> None:
        """Print migration status."""
        with psycopg.connect(self.database_url) as connection:
            self._ensure_schema_versions_table(connection)
            executed = self._get_executed_migrations(connection)
        
        pending = self._get_pending_migrations()
        
        print(f"Executed migrations: {len(executed)}")
        for migration in sorted(executed):
            print(f"  ✓ {migration}")
        
        if pending:
            print(f"\nPending migrations: {len(pending)}")
            for version_id, migration_path in pending:
                print(f"  ⏳ {migration_path.name}")
        else:
            print("\nNo pending migrations.")


def main():
    """CLI entry point for migration runner."""
    database_url = os.environ.get("BLOODNET_DATABASE_URL")
    if not database_url:
        print("Error: BLOODNET_DATABASE_URL environment variable is not set")
        sys.exit(1)
    
    runner = MigrationRunner(database_url)
    
    if len(sys.argv) < 2:
        print("Usage: python -m migrations.runner [upgrade|status]")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "upgrade":
        runner.upgrade()
    elif command == "status":
        runner.status()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()

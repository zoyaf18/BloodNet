"""CLI entry point for migration runner."""

import sys
import os
from .runner import MigrationRunner


def main():
    """Run migrations."""
    database_url = os.environ.get("BLOODNET_DATABASE_URL")
    if not database_url:
        print("Error: BLOODNET_DATABASE_URL environment variable is not set")
        sys.exit(1)
    
    runner = MigrationRunner(database_url)
    
    if len(sys.argv) < 2:
        print("Usage: python -m migrations [upgrade|status]")
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

"""Copy only the six authorized accounts into the isolated Cloud SQL candidate.

Live reads are repeatable-read/read-only. No live workflow, inventory, password
hashes, reset tokens or notification backlog are copied. Reports contain counts.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from google.cloud.sql.connector import Connector
from google.oauth2.credentials import Credentials
from psycopg.conninfo import conninfo_to_dict

from stage_mvp_candidate import run, PROJECT, REGION, DATABASE, ROOT

EMAILS = ("zoya1804@gmail.com", "mary.jane73748@gmail.com", "imzoya.shakeel@gmail.com",
          "fatimazoya2002@gmail.com", "mitchell.tucker3214@gmail.com", "tj399250@gmail.com")


def main():
    dsn = conninfo_to_dict(run("secrets", "versions", "access", "latest", "--secret=bloodnet-database-url").stdout.strip())
    if dsn["dbname"] == DATABASE:
        raise RuntimeError("Candidate database must differ from the live database")
    credentials = Credentials(run("auth", "print-access-token").stdout.strip())
    with Connector(credentials=credentials, refresh_strategy="LAZY") as connector:
        source = connector.connect(f"{PROJECT}:{REGION}:bloodnet-postgres", "pg8000", user=dsn["user"], password=dsn["password"], db=dsn["dbname"])
        target = connector.connect(f"{PROJECT}:{REGION}:bloodnet-postgres", "pg8000", user=dsn["user"], password=dsn["password"], db=DATABASE)
        try:
            source_cursor, target_cursor = source.cursor(), target.cursor()
            source_cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            source_cursor.execute("SET LOCAL statement_timeout = '20s'")
            target_cursor.execute("SELECT current_database()")
            assert target_cursor.fetchone()[0] == DATABASE

            def fetch(table, predicate, params):
                source_cursor.execute(f"SELECT row_to_json(t) FROM {table} t WHERE {predicate}", params)
                return [row[0] for row in source_cursor.fetchall()]

            users = fetch("users", "email IN (" + ",".join(["%s"] * len(EMAILS)) + ")", EMAILS)
            assert len(users) == len(EMAILS), "All authorized accounts must exist before candidate seeding"
            ids = [str(user["id"]) for user in users]
            slots = ",".join(["%s"] * len(ids))
            memberships = fetch("organization_memberships", f"user_id::text IN ({slots})", ids)
            org_ids = sorted({str(member["organization_id"]) for member in memberships})
            organizations = fetch("organizations", "id::text IN (" + ",".join(["%s"] * len(org_ids)) + ")", org_ids)
            for user in users:
                for field in ("password_hash", "password_reset_token", "password_reset_expires_at"):
                    user[field] = None
            for member in memberships:
                for field in ("approved_by", "invited_by"):
                    if member.get(field) and str(member[field]) not in ids:
                        member[field] = None
            rows = {"users": users, "organizations": organizations, "organization_memberships": memberships}
            for table in ("donor_profiles", "user_locations", "user_region_preferences"):
                rows[table] = fetch(table, f"user_id::text IN ({slots})", ids)
            for table, records in rows.items():
                for record in records:
                    target_cursor.execute(f"INSERT INTO {table} SELECT * FROM json_populate_record(NULL::{table}, %s::json) ON CONFLICT DO NOTHING", (json.dumps(record, default=str),))
            target.commit()
            counts = {}
            for table in (*rows, "inventory_units", "workflow_cases", "notification_outbox"):
                target_cursor.execute(f"SELECT count(*) FROM {table}")
                counts[table] = target_cursor.fetchone()[0]
            report = {"at": datetime.now(timezone.utc).isoformat(), "database": DATABASE,
                      "source_read_only": True, "target_counts": counts,
                      "note": "Authorized identity/profile subset only. No live workflow or inventory copied."}
            (ROOT / "docs/mvp-candidate-account-seed.json").write_text(json.dumps(report, indent=2))
            print(json.dumps(report))
        finally:
            source.rollback()
            target.rollback()
            source.close()
            target.close()


if __name__ == "__main__":
    main()

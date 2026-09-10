"""Read-only production integrity audit. Never imports application startup code.

Credentials stay in memory. Reports contain schema metadata and aggregate counts,
not user profiles, contact details, tokens, or database credentials.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

from google.cloud.sql.connector import Connector
from google.oauth2.credentials import Credentials
from urllib.parse import urlsplit, unquote

ROOT = Path(__file__).resolve().parents[1]
GCLOUD = r"C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
PROJECT = "project-bae56d7f-3ee2-48fc-bdd"
INSTANCE = f"{PROJECT}:asia-south1:bloodnet-postgres"


def gcloud(*args):
    result = subprocess.run([GCLOUD, *args, f"--project={PROJECT}"],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError("gcloud command failed; credential output suppressed")
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checks", type=Path)
    parser.add_argument("--validate-models", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/production-integrity-results.json")
    args = parser.parse_args()
    parsed = urlsplit(gcloud("secrets", "versions", "access", "latest", "--secret=bloodnet-database-url"))
    if parsed.scheme not in ("postgres", "postgresql") or not parsed.username or not parsed.password:
        raise RuntimeError("Expected a PostgreSQL URL; credential output suppressed")
    dsn = {"user": unquote(parsed.username), "password": unquote(parsed.password), "dbname": unquote(parsed.path.lstrip("/"))}
    credentials = Credentials(gcloud("auth", "print-access-token"))
    report = {"instance": INSTANCE, "started_at": datetime.now(timezone.utc).isoformat(), "checks": []}
    with Connector(credentials=credentials, refresh_strategy="LAZY", timeout=30) as connector:
        connection = connector.connect(INSTANCE, "pg8000", user=dsn["user"],
                                       password=dsn["password"], db=dsn["dbname"], timeout=30)
        try:
            cursor = connection.cursor()
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cursor.execute("SET LOCAL statement_timeout = '20s'")
            cursor.execute("SET LOCAL lock_timeout = '2s'")
            cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '120s'")

            def query(sql):
                cursor.execute(sql)
                columns = [c[0] for c in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]

            report["connection"] = query("SELECT current_database() AS database, current_setting('transaction_read_only') AS read_only, current_setting('transaction_isolation') AS isolation, now() AS snapshot_time, version() AS version")[0]
            report["columns"] = query("SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position")
            report["constraints"] = query("SELECT c.relname AS table_name, con.conname AS name, con.contype AS type, con.convalidated AS validated, pg_get_constraintdef(con.oid) AS definition FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname,con.conname")
            report["indexes"] = query("SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname")
            report["table_statistics"] = query("SELECT relname,n_live_tup,n_dead_tup,last_analyze,last_autoanalyze,seq_scan,idx_scan FROM pg_stat_user_tables ORDER BY relname")
            tables = query("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
            report["row_counts"] = {}
            for table in tables:
                name = table["tablename"]
                quoted = '"' + name.replace('"', '""') + '"'
                report["row_counts"][name] = query(f"SELECT count(*) AS count FROM {quoted}")[0]["count"]
            report["migrations"] = query("SELECT version_id FROM schema_versions ORDER BY version_id")
            if args.checks:
                for check in json.loads(args.checks.read_text(encoding="utf-8")):
                    started = time.monotonic()
                    cursor.execute("SAVEPOINT audit_check")
                    try:
                        rows = query(check["sql"])
                        item = {**check, "rows": rows, "status": "pass" if check.get("kind") == "assert_zero" and all(r["count"] == 0 for r in rows) else "finding" if check.get("kind") == "assert_zero" else "observed"}
                    except Exception as exc:
                        cursor.execute("ROLLBACK TO SAVEPOINT audit_check")
                        detail = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {}
                        item = {**check, "status": "error", "error_type": type(exc).__name__, "sqlstate": detail.get("C")}
                    cursor.execute("RELEASE SAVEPOINT audit_check")
                    item["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
                    report["checks"].append(item)
                    print(f"{check['id']}: {item['status']}", flush=True)
            if args.validate_models:
                import sys
                from collections import Counter
                sys.path.insert(0, str(ROOT))
                from contracts.models import InventoryUnit, InventoryReservation, Case, Request, Recommendation, Notification, AuditRecord
                from pydantic import ValidationError
                report["model_validation"] = {}
                for table, model in [("inventory_units", InventoryUnit), ("inventory_reservations", InventoryReservation), ("workflow_cases", Case), ("workflow_requests", Request), ("workflow_recommendations", Recommendation), ("notification_outbox", Notification), ("audit_records", AuditRecord)]:
                    cursor.execute(f"SELECT payload FROM {table}")
                    counts = Counter()
                    errors = Counter()
                    while rows := cursor.fetchmany(1000):
                        for (payload,) in rows:
                            counts["total"] += 1
                            try:
                                model.model_validate(payload)
                                counts["valid"] += 1
                            except ValidationError as exc:
                                counts["invalid"] += 1
                                counts["invalid_synthetic" if payload.get("synthetic") else "invalid_unmarked"] += 1
                                for error in exc.errors(include_input=False, include_url=False):
                                    errors[".".join(map(str, error["loc"])) + ":" + error["type"]] += 1
                    report["model_validation"][table] = {"counts": dict(counts), "errors": dict(errors)}
                    print(f"{table} contract: {dict(counts)}", flush=True)
        finally:
            connection.rollback()
            connection.close()
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"report": str(args.output), "read_only": report["connection"]["read_only"], "row_counts": report["row_counts"]}))


if __name__ == "__main__":
    main()

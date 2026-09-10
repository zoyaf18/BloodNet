"""Repair orphaned synthetic workflow recommendations.

This is a focused data-consistency helper intended for synthetic/demo data.
It looks for workflow_recommendations rows whose payload.case_id points to a
non-existent workflow case and repairs them by joining on request_id.

If a matching workflow case cannot be found, the synthetic recommendation is
stripped of its stale case_id so the orphaned recommendation no longer violates
production-integrity checks.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import psycopg
from psycopg.rows import dict_row


def load_dsn(database_url: str | None) -> str:
    if database_url:
        return database_url
    value = os.getenv("BLOODNET_DATABASE_URL")
    if value:
        return value
    raise SystemExit("--database-url or BLOODNET_DATABASE_URL is required")


def resolve_case_id(payload: dict[str, Any], conn: psycopg.Connection) -> str | None:
    request_id = (payload.get("request_id") or payload.get("reservation_proposal", {}).get("request_id") or "").strip()
    if not request_id:
        return None

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT case_id
            FROM workflow_cases
            WHERE payload->>'request_id' = %s
            ORDER BY case_id
            LIMIT 1
            """,
            (request_id,),
        )
        row = cur.fetchone()
    return row["case_id"] if row else None


def repair(conn: psycopg.Connection, dry_run: bool) -> dict[str, int]:
    stats = {
        "scanned": 0,
        "updated": 0,
        "stale_case_ids_removed": 0,
        "deleted_orphans": 0,
    }

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT rec_id, payload
            FROM workflow_recommendations
            ORDER BY rec_id
            """
        )
        rows = cur.fetchall()

    stats["scanned"] = len(rows)

    for row in rows:
        rec_id = row["rec_id"]
        payload = row["payload"] or {}
        case_id = payload.get("case_id")

        if not case_id:
            continue

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM workflow_cases
                WHERE case_id = %s
                """,
                (case_id,),
            )
            case_exists = cur.fetchone() is not None

        if case_exists:
            continue

        matched_case_id = resolve_case_id(payload, conn)

        if matched_case_id:
            payload["case_id"] = matched_case_id
            if isinstance(payload.get("reservation_proposal"), dict):
                payload["reservation_proposal"]["case_id"] = matched_case_id
            if not dry_run:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE workflow_recommendations
                        SET payload = %s, updated_at = NOW()
                        WHERE rec_id = %s
                        """,
                        (json.dumps(payload), rec_id),
                    )
                stats["updated"] += 1
            continue

        # Synthetic rows with no matching workflow case are stripped of the stale
        # case reference to keep the data consistent while preserving the
        # recommendation record itself.
        payload.pop("case_id", None)
        if isinstance(payload.get("reservation_proposal"), dict):
            payload["reservation_proposal"].pop("case_id", None)

        if not dry_run:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE workflow_recommendations
                    SET payload = %s, updated_at = NOW()
                    WHERE rec_id = %s
                    """,
                    (json.dumps(payload), rec_id),
                )

        stats["stale_case_ids_removed"] += 1

    if not dry_run:
        conn.commit()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="PostgreSQL DSN to repair")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without modifying the database",
    )
    args = parser.parse_args()

    dsn = load_dsn(args.database_url)
    conn = psycopg.connect(dsn)

    try:
        stats = repair(conn, args.dry_run)
        print(json.dumps(stats, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()

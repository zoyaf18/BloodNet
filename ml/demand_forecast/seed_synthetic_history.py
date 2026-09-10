"""Seed clearly marked synthetic requests for development-only forecasting.

This utility never runs unless BLOODNET_ALLOW_SYNTHETIC_HISTORY=1 is set.
Synthetic rows must not be used as a production Blood Weather data source.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import random
from typing import Any
from uuid import uuid4

from google.cloud.sql.connector import Connector, IPTypes


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", default=os.getenv("BLOODNET_CLOUD_SQL_INSTANCE"), required=False)
    parser.add_argument("--database", default=os.getenv("BLOODNET_DB_NAME", "bloodnet"))
    parser.add_argument("--user", default=os.getenv("BLOODNET_DB_USER", "bloodnet"))
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--region", default=os.getenv("BLOODNET_DEFAULT_REGION", "Pune"))
    return parser.parse_args()


def seed_history(args: argparse.Namespace) -> int:
    if os.getenv("BLOODNET_ENV") != "demo":
        raise RuntimeError("Synthetic history is restricted to BLOODNET_ENV=demo")
    if os.getenv("BLOODNET_ALLOW_SYNTHETIC_HISTORY") != "1":
        raise RuntimeError("Refusing synthetic history seed; set BLOODNET_ALLOW_SYNTHETIC_HISTORY=1 for development only")
    password = os.getenv("BLOODNET_DB_PASSWORD")
    if not password:
        raise RuntimeError("BLOODNET_DB_PASSWORD must be set")
    if not args.instance:
        raise RuntimeError("--instance or BLOODNET_CLOUD_SQL_INSTANCE is required")
    if not 30 <= args.days <= 365:
        raise ValueError("days must be between 30 and 365")

    random_generator = random.Random(7)
    now = datetime.now(timezone.utc)
    rows: list[tuple[str, Any, datetime]] = []
    for day_offset in range(args.days, 0, -1):
        created_at = now - timedelta(days=day_offset)
        for group_index, (group, baseline) in enumerate((("O+", 5), ("A+", 4), ("B+", 3), ("AB+", 1))):
            qty = max(1, baseline + random_generator.choice((-1, 0, 0, 1)))
            request_id = f"SYNTHETIC-{uuid4().hex}"
            payload = {
                "request": {
                    "request_id": request_id,
                    "region": args.region,
                    "group": group,
                    "component": ("RBC", "Whole Blood", "Platelets (RDP)", "FFP")[group_index],
                    "qty": qty,
                    "hospital_id": "SYNTHETIC-HOSPITAL",
                    "fulfilled_units": max(0, qty - random_generator.choice((0, 0, 1))),
                    "status": "fulfilled",
                    "synthetic": True,
                },
                "synthetic": True,
                "source": "development_forecast_seed",
            }
            rows.append((request_id, payload, created_at))

    connector = Connector(ip_type=IPTypes.PUBLIC)
    try:
        connection = connector.connect(
            args.instance, "pg8000", user=args.user, password=password, db=args.database
        )
        try:
            cursor = connection.cursor()
            cursor.executemany(
                """
                INSERT INTO workflow_requests (request_id, payload, created_at, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (request_id) DO NOTHING
                """,
                [(request_id, json.dumps(payload), created_at, created_at) for request_id, payload, created_at in rows],
            )
            connection.commit()
        finally:
            connection.close()
    finally:
        connector.close()
    print({"inserted_candidates": len(rows), "days": args.days, "region": args.region, "synthetic": True})
    return len(rows)


if __name__ == "__main__":
    seed_history(_args())
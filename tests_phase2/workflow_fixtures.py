"""Canonical setup for isolated PostgreSQL workflow tests."""
import os
import psycopg
from psycopg.types.json import Jsonb


def seed_hospital():
    with psycopg.connect(os.environ["BLOODNET_DATABASE_URL"]) as connection:
        connection.execute("""INSERT INTO organizations(id,name,type,contact_email,metadata,verified,status)
            VALUES ('00000000-0000-0000-0000-000000000901','Workflow Test Hospital','hospital',
                    'workflow-hospital@example.invalid',%s,TRUE,'active')
            ON CONFLICT(id) DO UPDATE SET metadata=EXCLUDED.metadata""",
            (Jsonb({"hospital_id":"HOSP-001","region_id":"Pune","city":"Pune"}),))


def seed_units(payload):
    with psycopg.connect(os.environ["BLOODNET_DATABASE_URL"]) as connection:
        for unit in payload["units"]:
            connection.execute("INSERT INTO inventory_units(unit_id,payload) VALUES (%s,%s) ON CONFLICT(unit_id) DO UPDATE SET payload=EXCLUDED.payload", (unit["unit_id"], Jsonb(unit)))
    return payload

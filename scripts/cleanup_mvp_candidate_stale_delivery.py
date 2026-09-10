"""Remove the known synthetic demo notification left by the candidate setup replay."""
import json
from urllib.parse import urlsplit, unquote

from google.cloud.sql.connector import Connector
from google.oauth2.credentials import Credentials

from stage_mvp_candidate import run, PROJECT, REGION, DATABASE

parsed = urlsplit(run("secrets", "versions", "access", "latest", "--secret=bloodnet-database-url").stdout.strip())
with Connector(credentials=Credentials(run("auth", "print-access-token").stdout.strip()), refresh_strategy="LAZY") as connector:
    connection = connector.connect(
        f"{PROJECT}:{REGION}:bloodnet-postgres",
        "pg8000",
        user=unquote(parsed.username),
        password=unquote(parsed.password),
        db=DATABASE,
    )
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            DELETE FROM approval_state_tracking
            WHERE outbox_event_id IN (
                SELECT event_id
                FROM notification_outbox
                WHERE status = 'pending'
                  AND delivery_status = 'pending'
                  AND payload->>'channel' = 'demo'
                  AND payload->>'donor_id' = 'system'
            )
            RETURNING outbox_event_id
            """
        )
        removed_tracking = [row[0] for row in cursor.fetchall()]
        cursor.execute(
            """
            DELETE FROM notification_outbox
            WHERE status = 'pending'
              AND delivery_status = 'pending'
              AND payload->>'channel' = 'demo'
              AND payload->>'donor_id' = 'system'
            RETURNING event_id
            """
        )
        removed = [row[0] for row in cursor.fetchall()]
        connection.commit()
        print(json.dumps({"database": DATABASE, "removed_tracking_event_ids": removed_tracking, "removed_event_ids": removed}))
    finally:
        connection.close()

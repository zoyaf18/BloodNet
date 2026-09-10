"""PostgreSQL persistence for donor outreach responses."""

from __future__ import annotations

from datetime import datetime

import psycopg


class DonorResponseRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def save_response(
        self,
        outreach_id: str,
        donor_id: str,
        response: str,
        responded_at: datetime,
    ) -> None:
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """
                INSERT INTO donor_responses (
                    outreach_id, donor_id, response, responded_at
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (outreach_id, donor_id)
                DO UPDATE SET
                    response = EXCLUDED.response,
                    responded_at = EXCLUDED.responded_at,
                    processed_at = NULL
                """,
                (outreach_id, donor_id, response, responded_at),
            )

    def get_response(self, outreach_id: str, donor_id: str) -> str | None:
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                """
                SELECT response
                FROM donor_responses
                WHERE outreach_id = %s AND donor_id = %s
                """,
                (outreach_id, donor_id),
            ).fetchone()
        return row[0] if row else None

    def is_processed(self, outreach_id: str, donor_id: str) -> bool:
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                """
                SELECT processed_at IS NOT NULL
                FROM donor_responses
                WHERE outreach_id = %s AND donor_id = %s
                """,
                (outreach_id, donor_id),
            ).fetchone()
        return bool(row[0]) if row else False

    def mark_processed(self, outreach_id: str, donor_id: str) -> None:
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """
                UPDATE donor_responses
                SET processed_at = NOW()
                WHERE outreach_id = %s AND donor_id = %s
                """,
                (outreach_id, donor_id),
            )
import os
from datetime import datetime, timezone
import sys

import psycopg
import pytest

SWARM_SVC_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "services", "swarm-svc")
)
if SWARM_SVC_DIR not in sys.path:
    sys.path.insert(0, SWARM_SVC_DIR)

from donor_response_repository import DonorResponseRepository


DATABASE_URL = os.environ.get("BLOODNET_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BLOODNET_DATABASE_URL is required for PostgreSQL tests",
)
OUTREACH_ID = "PG-DONOR-RESPONSE-OUTREACH"
DONOR_ID = "PG-DONOR-RESPONSE-DONOR"


@pytest.fixture
def donor_response_rows():
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "DELETE FROM donor_responses WHERE outreach_id = %s AND donor_id = %s",
            (OUTREACH_ID, DONOR_ID),
        )
    yield
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "DELETE FROM donor_responses WHERE outreach_id = %s AND donor_id = %s",
            (OUTREACH_ID, DONOR_ID),
        )


def test_response_survives_repository_restart(donor_response_rows):
    assert DATABASE_URL is not None
    responded_at = datetime.now(timezone.utc)
    DonorResponseRepository(DATABASE_URL).save_response(
        OUTREACH_ID, DONOR_ID, "accept", responded_at
    )

    restarted_repository = DonorResponseRepository(DATABASE_URL)

    assert restarted_repository.get_response(OUTREACH_ID, DONOR_ID) == "accept"


def test_duplicate_response_keeps_one_row(donor_response_rows):
    assert DATABASE_URL is not None
    repository = DonorResponseRepository(DATABASE_URL)
    responded_at = datetime.now(timezone.utc)
    repository.save_response(OUTREACH_ID, DONOR_ID, "accept", responded_at)
    repository.save_response(OUTREACH_ID, DONOR_ID, "accept", responded_at)

    with psycopg.connect(DATABASE_URL) as connection:
        count = connection.execute(
            """
            SELECT COUNT(*)
            FROM donor_responses
            WHERE outreach_id = %s AND donor_id = %s
            """,
            (OUTREACH_ID, DONOR_ID),
        ).fetchone()[0]

    assert count == 1


def test_changed_response_replaces_existing_response(donor_response_rows):
    assert DATABASE_URL is not None
    repository = DonorResponseRepository(DATABASE_URL)
    responded_at = datetime.now(timezone.utc)
    repository.save_response(OUTREACH_ID, DONOR_ID, "accept", responded_at)
    repository.save_response(OUTREACH_ID, DONOR_ID, "decline", responded_at)

    assert repository.get_response(OUTREACH_ID, DONOR_ID) == "decline"

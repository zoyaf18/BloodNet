from concurrent.futures import ThreadPoolExecutor
import os

import psycopg
import pytest

MATCH_SVC_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "services", "match-svc")
)
if MATCH_SVC_DIR not in os.sys.path:
    os.sys.path.insert(0, MATCH_SVC_DIR)

from processed_event_repository import ProcessedEventRepository


DATABASE_URL = os.environ.get("BLOODNET_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BLOODNET_DATABASE_URL is required for PostgreSQL tests",
)
EVENT_ID = "PG-PROCESSED-EVENT-TEST"


@pytest.fixture
def processed_event_rows():
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("DELETE FROM processed_events WHERE event_id = %s", (EVENT_ID,))
    yield
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("DELETE FROM processed_events WHERE event_id = %s", (EVENT_ID,))


def test_first_claim_succeeds_and_duplicate_claim_is_rejected(processed_event_rows):
    repository = ProcessedEventRepository(DATABASE_URL)

    assert repository.try_claim(EVENT_ID, "INVENTORY_RESERVED") is True
    assert repository.try_claim(EVENT_ID, "INVENTORY_RESERVED") is False


def test_claim_survives_repository_restart(processed_event_rows):
    ProcessedEventRepository(DATABASE_URL).try_claim(EVENT_ID, "INVENTORY_RESERVED")

    restarted_repository = ProcessedEventRepository(DATABASE_URL)

    assert restarted_repository.try_claim(EVENT_ID, "INVENTORY_RESERVED") is False


def test_concurrent_claims_have_one_winner(processed_event_rows):
    def claim() -> bool:
        return ProcessedEventRepository(DATABASE_URL).try_claim(
            EVENT_ID, "INVENTORY_RESERVED"
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: claim(), range(2)))

    assert sorted(results) == [False, True]
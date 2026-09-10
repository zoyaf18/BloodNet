import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "services" / "swarm-svc", ROOT / "services" / "notify-svc"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from contracts.events import EventEnvelope
from contracts.models import Case, Notification
from escalation import AdaptiveSwarmPolicy
from notification_service import NotificationRepository, NotificationService


def test_adaptive_round_widens_and_excludes_pending_donors():
    policy = AdaptiveSwarmPolicy()
    donors = [
        {"donor_id": "D1", "success_probability": 0.99},
        {"donor_id": "D2", "success_probability": 0.8, "contact_fatigue_30d": 4},
        {"donor_id": "D3", "success_probability": 0.7, "exploration_bonus": 0.1},
    ]

    first = policy.plan(donors, 1, round_number=1)
    second = policy.plan(donors, 1, round_number=2, pending_ids=set(first.donor_ids))

    assert first is not None
    assert first.radius_km == 2.0
    assert second is not None
    assert second.radius_km == 5.0
    assert set(first.donor_ids).isdisjoint(second.donor_ids)


def test_demo_provider_deduplicates_by_notification_key():
    sent = []
    service = NotificationService(NotificationRepository(), sent.append)
    case = Case(case_id="CASE-KEY", request_id="REQ-KEY")
    event = EventEnvelope.notifications_requested(case, ["D1"], correlation_id="CORR-KEY")

    service.handle_request(event)
    service.handle_request(event.model_copy(update={"event_id": "EVT-RETRY"}))

    assert len(sent) == 1
    assert sent[0].notification_id == "NOTIFY-CASE-KEY-D1"

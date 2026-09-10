from contracts.realtime import CaseRealtimeHub


def test_local_case_subscription_receives_updates_and_cleans_up():
    hub = CaseRealtimeHub()
    updates = hub.subscribe_to_case("CASE-1")
    snapshot = {"case": {"case_id": "CASE-1", "status": "reserved"}}

    hub.publish_case("CASE-1", snapshot)

    assert updates.get_nowait() == snapshot
    hub.unsubscribe_from_case("CASE-1", updates)
    hub.publish_case("CASE-1", {"case": {"status": "fulfilled"}})
    assert updates.empty()

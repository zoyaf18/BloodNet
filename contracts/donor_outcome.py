"""Transactional donor responses and durable case commitment counts across services."""
from datetime import datetime, timezone

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from contracts.models import AuditRecord, Case, CaseOutcome, Recommendation


def record_donor_response(store, outreach_id: str, donor_id: str, response: str) -> str:
    if response not in {"accept", "decline"}:
        raise HTTPException(status_code=422, detail="Response must be accept or decline")
    notification = store.notifications.repository.get(outreach_id)
    if notification is None or notification.donor_id != donor_id:
        raise HTTPException(status_code=404, detail="Outreach was not found")
    case_id = notification.case_id
    with store._workflow_connection() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"case:{case_id}",))
        row = connection.execute("SELECT payload FROM workflow_cases WHERE case_id = %s FOR UPDATE", (case_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Outreach case was not found")
        case = Case.model_validate(row["payload"])
        request_row = connection.execute(
            "SELECT payload FROM workflow_requests WHERE request_id = %s",
            (case.request_id,),
        ).fetchone()
        request_payload = request_row["payload"] if request_row else {}
        region_id = str(
            request_payload.get("region_id") or request_payload.get("region") or ""
        ).strip()
        previous = connection.execute("SELECT response, processed_at FROM donor_responses WHERE outreach_id = %s AND donor_id = %s", (outreach_id, donor_id)).fetchone()
        if previous and previous["response"] == response and previous["processed_at"] is not None:
            return case_id
        if case.outcome in {CaseOutcome.FULFILLED, CaseOutcome.CANCELLED}:
            raise HTTPException(status_code=409, detail="This case is closed")
        now = datetime.now(timezone.utc)
        connection.execute("""INSERT INTO donor_responses(outreach_id, donor_id, response, responded_at, processed_at)
            VALUES (%s, %s, %s, %s, %s) ON CONFLICT (outreach_id, donor_id)
            DO UPDATE SET response=EXCLUDED.response, responded_at=EXCLUDED.responded_at, processed_at=EXCLUDED.processed_at""",
            (outreach_id, donor_id, response, now, now))
        # Derive counts from evidence instead of incrementing a worker's cache.
        # A donor may receive another outreach round, but contributes at most one
        # commitment to the case, using their latest response.
        accepted = connection.execute("""SELECT count(*) AS count FROM (
            SELECT DISTINCT ON (r.donor_id) r.response FROM donor_responses r
            JOIN notifications n ON n.notification_id = r.outreach_id
            WHERE n.payload->>'case_id' = %s
            ORDER BY r.donor_id, r.responded_at DESC, r.outreach_id DESC
        ) latest WHERE response = 'accept'""", (case_id,)).fetchone()["count"]
        target = case.donor_target_units or (case.units_from_donors_remaining + case.units_from_donors_fulfilled)
        commitments = min(accepted, target)
        if commitments < case.confirmed_donor_units:
            raise HTTPException(status_code=409, detail="This response would contradict units already confirmed received")
        case.units_from_donors_fulfilled = commitments
        case.units_from_donors_remaining = max(target - commitments, 0)
        case.outcome = CaseOutcome.PARTIALLY_FULFILLED
        case.escalation_state = "awaiting_hospital_confirmation" if case.units_from_donors_remaining == 0 else "required"
        if not case.units_from_donors_remaining:
            case.fulfillment_probability = 1.0
            connection.execute("""UPDATE workflow_recommendations SET
                payload=jsonb_set(payload, '{state}', '"RESOLVED"'::jsonb), updated_at=now()
                WHERE payload->>'case_id'=%s AND payload->>'type'='MOBILIZE_DONORS'
                  AND payload->>'state'='AWAITING_APPROVAL'
                  AND payload->'provenance'->>'source'='deterministic_outcome_loop'""", (case_id,))
        elif region_id:
            recommendation = Recommendation(rec_id=f"ESC-{case_id}-{case.units_from_donors_remaining}",
                type="MOBILIZE_DONORS", request_id=case.request_id, case_id=case_id,
                region_id=region_id,
                payload={"target_units": case.units_from_donors_remaining, "trigger": "donor_response", "parent_case_id": case_id, "region_id": region_id},
                rationale="Review remaining donor coverage after a donor response.",
                expected_impact={"units_needed": case.units_from_donors_remaining},
                provenance={"source": "deterministic_outcome_loop"}, state="AWAITING_APPROVAL")
            connection.execute("INSERT INTO workflow_recommendations(rec_id,payload) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                               (recommendation.rec_id, Jsonb(recommendation.model_dump(mode="json"))))
        connection.execute("UPDATE workflow_cases SET payload=%s, updated_at=now() WHERE case_id=%s",
                           (Jsonb(case.model_dump(mode="json")), case_id))
        store.audit.append(AuditRecord(audit_id=f"AUDIT-DONOR-RESPONSE-{outreach_id}-{now.isoformat()}",
            action="donor_response_recorded", request_id=case.request_id, case_id=case_id,
            actor=donor_id, at=now, details={"response": response, "outreach_id": outreach_id,
                "donor_commitments": commitments, "remaining_shortfall": case.units_from_donors_remaining}), cursor=connection.cursor())
    return case_id

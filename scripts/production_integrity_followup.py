"""Follow-up queries distinguish historical/demo evidence from live invariants."""
import json
from pathlib import Path

CHECKS = []


def add(name, sql, kind="observe"):
    CHECKS.append({"id": name, "kind": kind, "sql": sql})


add("request_missing_hospital_optimized", "WITH hospital_keys AS (SELECT id::text AS key FROM organizations WHERE type='hospital' UNION SELECT metadata->>'hospital_id' FROM organizations WHERE type='hospital' AND metadata->>'hospital_id' IS NOT NULL) SELECT count(*) AS count FROM workflow_requests r LEFT JOIN hospital_keys h ON h.key=r.payload->>'hospital_id' WHERE h.key IS NULL", "assert_zero")
add("outbox_missing_case_by_channel", "SELECT n.payload->>'channel' AS channel, (n.payload->>'donor_id'='system') AS system_recipient, n.status, n.delivery_status, count(*) AS count FROM notification_outbox n LEFT JOIN workflow_cases c USING(case_id) WHERE c.case_id IS NULL GROUP BY 1,2,3,4")
add("outbox_delivery_evidence", "SELECT status,count(*) AS count,count(provider_message_id) AS provider_ids,count(delivered_at) AS handoff_timestamps,min(attempts) AS min_attempts,max(attempts) AS max_attempts FROM notification_outbox GROUP BY 1")
add("outbox_stale_processing_details", "SELECT count(*) AS count,min(lease_expires_at) AS lease_expired_at,max(attempts) AS attempts,min(updated_at) AS last_update FROM notification_outbox WHERE status='processing' AND lease_expires_at<now()")
add("approval_orphans_by_status", "SELECT a.status,count(*) AS count,min(a.created_at) AS oldest,max(a.created_at) AS latest FROM approval_records a LEFT JOIN workflow_cases c USING(case_id) WHERE c.case_id IS NULL GROUP BY 1")
add("recommendation_orphans_by_type", "SELECT r.payload->>'type' AS type,r.payload->>'state' AS state,count(*) AS count FROM workflow_recommendations r LEFT JOIN workflow_cases c ON c.case_id=r.payload->>'case_id' WHERE c.case_id IS NULL GROUP BY 1,2")
add("role_type_mismatch_details", "SELECT m.role,o.type AS organization_type,count(*) AS count FROM organization_memberships m JOIN organizations o ON o.id=m.organization_id WHERE m.status='active' AND ((m.role='bank_admin' AND o.type<>'blood_bank') OR (m.role='hospital_coordinator' AND o.type<>'hospital') OR (m.role='regional_admin' AND o.type<>'regional')) GROUP BY 1,2")
add("invalid_donor_age_context", "SELECT p.availability,p.eligibility_status,p.consent_contact,u.status AS user_status,(u.email LIKE '%@example.invalid') AS synthetic_user,count(*) AS count FROM donor_profiles p JOIN users u ON u.id=p.user_id WHERE p.date_of_birth>current_date GROUP BY 1,2,3,4,5")
add("donor_underage_context", "SELECT (u.email LIKE '%@example.invalid') AS synthetic_user,p.eligibility_status,count(*) AS count FROM donor_profiles p JOIN users u ON u.id=p.user_id WHERE p.date_of_birth>current_date-interval '18 years' GROUP BY 1,2")
add("demand_error_breakdown", "SELECT count(*) AS invalid_rows,count(*) FILTER (WHERE shortage_units>requested_units) AS shortage_exceeds_request,count(*) FILTER (WHERE fulfilled_units>requested_units) AS fulfillment_exceeds_request,count(*) FILTER (WHERE requested_units<>fulfilled_units+shortage_units) AS arithmetic_mismatch,min(demand_date) AS first_date,max(demand_date) AS last_date FROM demand_history WHERE fulfilled_units>requested_units OR requested_units<>fulfilled_units+shortage_units")
add("demand_error_synthetic_organizations", "SELECT coalesce(o.metadata->>'synthetic','false') AS synthetic_hospital,count(*) AS count FROM demand_history d LEFT JOIN organizations o ON o.id::text=d.hospital_id WHERE d.requested_units<>d.fulfilled_units+d.shortage_units GROUP BY 1")
add("case_reserved_without_reservation", "SELECT coalesce(c.payload->>'synthetic','false') AS synthetic,count(*) AS count FROM workflow_cases c WHERE c.payload->>'reservation_state'='reserved' AND NOT EXISTS (SELECT 1 FROM inventory_reservations r WHERE r.payload->>'case_id'=c.case_id) GROUP BY 1")
add("case_fulfilled_without_units", "SELECT coalesce(payload->>'synthetic','false') AS synthetic,count(*) AS count FROM workflow_cases WHERE payload->>'outcome'='fulfilled' AND coalesce((payload->>'units_from_inventory')::int,0)+coalesce((payload->>'units_from_donors_fulfilled')::int,0)=0 GROUP BY 1", "assert_zero")
add("case_request_quantity_balance", "SELECT coalesce(c.payload->>'synthetic','false') AS synthetic,count(*) AS count FROM workflow_cases c JOIN workflow_requests r ON r.request_id=c.payload->>'request_id' WHERE coalesce((c.payload->>'units_from_inventory')::int,0)+coalesce((c.payload->>'units_from_donors_remaining')::int,0)+coalesce((c.payload->>'units_from_donors_fulfilled')::int,0)<>(r.payload->>'qty')::int GROUP BY 1", "assert_zero")
add("request_contract_statuses", "SELECT coalesce(payload->>'synthetic','false') AS synthetic,payload->>'status' AS status,payload->>'urgency' AS urgency,count(*) AS count FROM workflow_requests GROUP BY 1,2,3 ORDER BY 1,2,3")
add("audit_hash_coverage", "SELECT count(*) AS total,count(*) FILTER (WHERE payload->>'prev_audit_hash' IS NOT NULL) AS linked_records,count(*) FILTER (WHERE payload->>'actor' IS NULL) AS missing_actor FROM audit_records")
add("audit_orphan_case_by_action", "SELECT a.payload->>'action' AS action,count(*) AS count FROM audit_records a LEFT JOIN workflow_cases c ON c.case_id=a.payload->>'case_id' WHERE c.case_id IS NULL GROUP BY 1 ORDER BY 2 DESC")
add("duplicate_operational_bank_keys", "SELECT count(*) AS count FROM (SELECT metadata->>'bank_id' FROM organizations WHERE type='blood_bank' AND metadata->>'bank_id' IS NOT NULL GROUP BY 1 HAVING count(*)>1) x", "assert_zero")
add("duplicate_operational_hospital_keys", "SELECT count(*) AS count FROM (SELECT metadata->>'hospital_id' FROM organizations WHERE type='hospital' AND metadata->>'hospital_id' IS NOT NULL GROUP BY 1 HAVING count(*)>1) x", "assert_zero")
add("donor_pool_profile_linkage", "SELECT count(*) AS total,count(p.user_id) AS linked_by_user_id FROM donor_pool d LEFT JOIN donor_profiles p ON p.user_id::text=d.donor_id")
add("donor_eligibility_future_deferral", "SELECT count(*) AS count FROM donor_profiles WHERE eligibility_status='eligible' AND next_eligible_at>now()")
add("inventory_component_values", "SELECT payload->>'component' AS component,count(*) AS count FROM inventory_units GROUP BY 1 ORDER BY 1")
add("request_required_by_missing", "SELECT coalesce(payload->>'synthetic','false') AS synthetic,count(*) AS count FROM workflow_requests WHERE payload->>'required_by' IS NULL GROUP BY 1")
add("foreign_key_integrity", "SELECT count(*) AS count FROM approval_state_tracking a LEFT JOIN audit_records r ON r.audit_id=a.audit_id WHERE r.audit_id IS NULL", "assert_zero")

add("missing_hospitals_synthetic_breakdown", "WITH hospital_keys AS (SELECT id::text AS key FROM organizations WHERE type='hospital' UNION SELECT metadata->>'hospital_id' FROM organizations WHERE type='hospital' AND metadata->>'hospital_id' IS NOT NULL) SELECT coalesce(r.payload->>'synthetic','false') AS synthetic,count(*) AS count FROM workflow_requests r LEFT JOIN hospital_keys h ON h.key=r.payload->>'hospital_id' WHERE h.key IS NULL GROUP BY 1")
add("duplicate_facility_scope_impact", "WITH duplicate_keys AS (SELECT type,metadata->>'bank_id' AS key FROM organizations WHERE type='blood_bank' AND metadata->>'bank_id' IS NOT NULL GROUP BY 1,2 HAVING count(*)>1 UNION ALL SELECT type,metadata->>'hospital_id' FROM organizations WHERE type='hospital' AND metadata->>'hospital_id' IS NOT NULL GROUP BY 1,2 HAVING count(*)>1) SELECT o.type,count(DISTINCT o.id) AS organizations,count(m.id) FILTER (WHERE m.status='active') AS active_memberships FROM duplicate_keys d JOIN organizations o ON o.type=d.type AND (CASE WHEN o.type='blood_bank' THEN o.metadata->>'bank_id' ELSE o.metadata->>'hospital_id' END)=d.key LEFT JOIN organization_memberships m ON m.organization_id=o.id GROUP BY 1")


if __name__ == "__main__":
    Path(__file__).with_suffix(".json").write_text(json.dumps(CHECKS, indent=2), encoding="utf-8")

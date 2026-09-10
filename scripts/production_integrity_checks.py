"""Aggregate-only SQL checks; no production writes or identifying row samples."""
CHECKS = []


def check(name, sql, kind="assert_zero"):
    CHECKS.append({"id": name, "kind": kind, "sql": sql})


for table, key in [("inventory_units", "unit_id"), ("inventory_reservations", "reservation_id"),
                   ("workflow_cases", "case_id"), ("workflow_requests", "request_id"),
                   ("workflow_recommendations", "rec_id"), ("notifications", "notification_id"),
                   ("audit_records", "audit_id")]:
    check(f"{table}_payload_identity", f"SELECT count(*) AS count FROM {table} WHERE payload->>'{key}' IS DISTINCT FROM {key}")

for table, field, parent, key in [
    ("workflow_cases", "request_id", "workflow_requests", "request_id"),
    ("workflow_recommendations", "case_id", "workflow_cases", "case_id"),
    ("inventory_reservations", "case_id", "workflow_cases", "case_id"),
    ("inventory_reservations", "request_id", "workflow_requests", "request_id"),
    ("notifications", "case_id", "workflow_cases", "case_id"),
]:
    check(f"{table}_missing_{parent}", f"SELECT count(*) AS count FROM {table} c LEFT JOIN {parent} p ON c.payload->>'{field}'=p.{key} WHERE c.payload->>'{field}' IS NOT NULL AND p.{key} IS NULL")

for table, field, parent, key in [
    ("donor_profiles", "user_id", "users", "id"),
    ("organization_memberships", "user_id", "users", "id"),
    ("organization_memberships", "organization_id", "organizations", "id"),
    ("workflow_case_projections", "case_id", "workflow_cases", "case_id"),
    ("approval_records", "case_id", "workflow_cases", "case_id"),
    ("approval_records", "recommendation_id", "workflow_recommendations", "rec_id"),
    ("approval_state_tracking", "case_id", "workflow_cases", "case_id"),
    ("notification_outbox", "case_id", "workflow_cases", "case_id"),
    ("notification_outbox", "notification_id", "notifications", "notification_id"),
]:
    check(f"{table}_{field}_missing_parent", f"SELECT count(*) AS count FROM {table} c LEFT JOIN {parent} p ON c.{field}=p.{key} WHERE c.{field} IS NOT NULL AND p.{key} IS NULL")

check("inventory_status_distribution", "SELECT coalesce(payload->>'synthetic','false') AS synthetic,payload->>'status' AS status,count(*) AS count FROM inventory_units GROUP BY 1,2 ORDER BY 1,2", "observe")
check("inventory_invalid_blood_group", "SELECT count(*) AS count FROM inventory_units WHERE coalesce(payload->>'group','') NOT IN ('A+','A-','B+','B-','AB+','AB-','O+','O-')")
check("inventory_invalid_status", "SELECT count(*) AS count FROM inventory_units WHERE coalesce(payload->>'status','') NOT IN ('available','reserved','issued','discarded','in_transit')")
check("inventory_timestamp_shape", "SELECT count(*) AS count FROM inventory_units WHERE coalesce(payload->>'expires_at','') !~ '^\\d{4}-\\d{2}-\\d{2}'")
check("inventory_expired_allocatable", "SELECT coalesce(payload->>'synthetic','false') AS synthetic,payload->>'status' AS status,count(*) AS count FROM inventory_units WHERE payload->>'status' IN ('available','reserved','in_transit') AND (payload->>'expires_at')::timestamptz<=now() GROUP BY 1,2")
check("inventory_collected_after_expiry", "SELECT count(*) AS count FROM inventory_units WHERE (payload->>'collected_at')::timestamptz >= (payload->>'expires_at')::timestamptz")
check("inventory_missing_collection_date", "SELECT coalesce(payload->>'synthetic','false') AS synthetic,count(*) AS count FROM inventory_units WHERE payload->>'collected_at' IS NULL GROUP BY 1", "observe")
check("inventory_missing_bank", "SELECT count(*) AS count FROM inventory_units u WHERE NOT EXISTS (SELECT 1 FROM organizations o WHERE o.type='blood_bank' AND (o.id::text=u.payload->>'bank_id' OR o.metadata->>'bank_id'=u.payload->>'bank_id'))")
check("reserved_unit_missing_reservation", "SELECT coalesce(u.payload->>'synthetic','false') AS synthetic,count(*) AS count FROM inventory_units u WHERE u.payload->>'status'='reserved' AND NOT EXISTS (SELECT 1 FROM inventory_reservations r WHERE r.payload->>'status'='reserved' AND r.payload->'unit_ids' ? u.unit_id) GROUP BY 1")
check("active_reservation_expired", "SELECT count(*) AS count FROM inventory_reservations WHERE payload->>'status'='reserved' AND (payload->>'expires_at')::timestamptz<=now()")
check("reservation_duplicate_units", "SELECT count(*) AS count FROM inventory_reservations r WHERE jsonb_array_length(r.payload->'unit_ids') <> (SELECT count(DISTINCT value) FROM jsonb_array_elements_text(r.payload->'unit_ids'))")
check("unit_reserved_twice", "SELECT count(*) AS count FROM (SELECT value FROM inventory_reservations r CROSS JOIN LATERAL jsonb_array_elements_text(r.payload->'unit_ids') WHERE r.payload->>'status'='reserved' GROUP BY value HAVING count(*)>1) d")
check("reservation_unit_missing", "SELECT count(*) AS count FROM inventory_reservations r CROSS JOIN LATERAL jsonb_array_elements_text(r.payload->'unit_ids') x LEFT JOIN inventory_units u ON u.unit_id=x.value WHERE u.unit_id IS NULL")
check("active_reservation_unit_state_or_owner", "SELECT count(*) AS count FROM inventory_reservations r CROSS JOIN LATERAL jsonb_array_elements_text(r.payload->'unit_ids') x JOIN inventory_units u ON u.unit_id=x.value WHERE r.payload->>'status'='reserved' AND (u.payload->>'status' IS DISTINCT FROM 'reserved' OR u.payload->>'bank_id' IS DISTINCT FROM r.payload->>'bank_id')")
check("multiple_cases_per_request", "SELECT count(*) AS count FROM (SELECT payload->>'request_id' FROM workflow_cases GROUP BY 1 HAVING count(*)>1) d")
check("request_nonpositive_quantity", "SELECT count(*) AS count FROM workflow_requests WHERE coalesce((payload->>'qty')::numeric,0)<=0")
check("request_missing_hospital", "WITH hospital_keys AS (SELECT id::text AS key FROM organizations WHERE type='hospital' UNION SELECT metadata->>'hospital_id' FROM organizations WHERE type='hospital' AND metadata->>'hospital_id' IS NOT NULL) SELECT count(*) AS count FROM workflow_requests r LEFT JOIN hospital_keys h ON h.key=r.payload->>'hospital_id' WHERE h.key IS NULL")
check("case_missing_projection", "SELECT count(*) AS count FROM workflow_cases c LEFT JOIN workflow_case_projections p USING(case_id) WHERE p.case_id IS NULL", "observe")
check("case_outcomes", "SELECT coalesce(payload->>'synthetic','false') AS synthetic,payload->>'outcome' AS outcome,count(*) AS count FROM workflow_cases GROUP BY 1,2 ORDER BY 1,2", "observe")
check("case_negative_counters", "SELECT count(*) AS count FROM workflow_cases WHERE (payload->>'units_from_inventory')::numeric<0 OR (payload->>'units_from_donors_fulfilled')::numeric<0 OR (payload->>'units_from_donors_remaining')::numeric<0")
check("fulfilled_case_with_donor_shortfall", "SELECT count(*) AS count FROM workflow_cases WHERE payload->>'outcome'='fulfilled' AND (payload->>'units_from_donors_remaining')::numeric>0")
check("outbox_status_distribution", "SELECT status,delivery_status,terminal_failure,count(*) AS count,min(created_at) AS oldest,max(updated_at) AS latest FROM notification_outbox GROUP BY 1,2,3 ORDER BY 1,2,3", "observe")
check("outbox_pending_over_day", "SELECT count(*) AS count FROM notification_outbox WHERE status='pending' AND NOT terminal_failure AND available_at<now()-interval '1 day'")
check("outbox_expired_lease", "SELECT count(*) AS count FROM notification_outbox WHERE status='processing' AND lease_expires_at<now()")
check("outbox_delivered_missing_evidence", "SELECT count(*) AS count FROM notification_outbox WHERE delivery_status='delivered' AND (delivered_at IS NULL OR provider_message_id IS NULL)")
check("outbox_payload_identity", "SELECT count(*) AS count FROM notification_outbox WHERE payload->>'notification_id' IS DISTINCT FROM notification_id")
check("outbox_payload_case_identity", "SELECT count(*) AS count FROM notification_outbox WHERE payload->>'case_id' IS DISTINCT FROM case_id")
check("outbox_negative_attempts", "SELECT count(*) AS count FROM notification_outbox WHERE attempts<0")
check("duplicate_normalized_email", "SELECT count(*) AS count FROM (SELECT lower(email) FROM users GROUP BY lower(email) HAVING count(*)>1) d")
check("memberships_by_role", "SELECT role,status,count(*) AS count FROM organization_memberships GROUP BY 1,2 ORDER BY 1,2", "observe")
check("expired_active_memberships", "SELECT count(*) AS count FROM organization_memberships WHERE status='active' AND expires_at<now()")
check("operational_role_wrong_org_type", "SELECT count(*) AS count FROM organization_memberships m JOIN organizations o ON o.id=m.organization_id WHERE m.status='active' AND ((m.role='bank_admin' AND o.type<>'blood_bank') OR (m.role='hospital_coordinator' AND o.type<>'hospital') OR (m.role='regional_admin' AND o.type NOT IN ('regional','platform')))")
check("donor_invalid_blood_group", "SELECT count(*) AS count FROM donor_profiles WHERE blood_group NOT IN ('A+','A-','B+','B-','AB+','AB-','O+','O-')")
check("donor_future_donation", "SELECT count(*) AS count FROM donor_profiles WHERE last_donation_at>now()")
check("donor_future_birth_date", "SELECT count(*) AS count FROM donor_profiles WHERE date_of_birth>current_date")
check("donor_profile_states", "SELECT availability,eligibility_status,consent_contact,count(*) AS count FROM donor_profiles GROUP BY 1,2,3 ORDER BY 1,2,3", "observe")
check("demand_arithmetic", "SELECT count(*) AS count FROM demand_history WHERE fulfilled_units>requested_units OR requested_units<>fulfilled_units+shortage_units")
check("demand_unknown_group", "SELECT count(*) AS count FROM demand_history WHERE blood_group NOT IN ('A+','A-','B+','B-','AB+','AB-','O+','O-')")
check("graph_duplicate_logical_edges", "SELECT count(*) AS count FROM (SELECT region,source_id,target_id,edge_type,blood_group,component FROM network_graph_edges GROUP BY 1,2,3,4,5,6 HAVING count(*)>1) d")
check("unvalidated_constraints", "SELECT count(*) AS count FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='public' AND NOT c.convalidated")
check("invalid_indexes", "SELECT count(*) AS count FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT i.indisvalid")


if __name__ == "__main__":
    import json
    from pathlib import Path
    Path(__file__).with_suffix(".json").write_text(json.dumps(CHECKS, indent=2), encoding="utf-8")

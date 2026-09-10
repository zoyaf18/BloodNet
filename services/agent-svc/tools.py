"""Data-backed read and recommendation tools for the operations agent."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from gemini_client import GeminiClient, GeminiUnavailable
from schemas import RecommendationProposal


def _database_url() -> str | None:
    return os.getenv("BLOODNET_DATABASE_URL")


def get_network_inventory() -> dict[str, Any]:
    """Return currently available inventory grouped by blood group/component."""
    data: dict[str, int] = {
        f"{blood_group}|RBC": 0
        for blood_group in ("O+", "A+", "B+", "AB+", "O-", "A-", "B-", "AB-")
    }
    data["platelets_total"] = 0
    database_url = _database_url()
    if database_url:
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT payload->>'group' AS blood_group,
                       payload->>'component' AS component,
                       COUNT(*) AS units
                FROM inventory_units
                WHERE payload->>'status' = 'available'
                GROUP BY payload->>'group', payload->>'component'
                """
            ).fetchall()
        data.update({
            f"{row['blood_group']}|{row['component']}": int(row["units"])
            for row in rows
        })
    return {"status": "success", "data": data, "source": "postgresql" if database_url else "empty"}


def get_swarm_status(swarm_id: str) -> dict[str, Any]:
    """Return persisted case notification progress for a swarm/case ID."""
    result = {
        "swarm_id": swarm_id,
        "target_units": 0,
        "donors_contacted": 0,
        "confirmed_donors": 0,
        "donations_completed": 0,
    }
    database_url = _database_url()
    if database_url:
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            case_row = connection.execute(
                "SELECT payload FROM workflow_cases WHERE case_id = %s",
                (swarm_id,),
            ).fetchone()
            notification_row = connection.execute(
                "SELECT COUNT(*) AS count FROM notifications WHERE payload->>'case_id' = %s",
                (swarm_id,),
            ).fetchone()
            accepted_row = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM donor_responses response
                JOIN notifications notification
                  ON notification.payload->>'outreach_id' = response.outreach_id
                WHERE notification.payload->>'case_id' = %s
                  AND response.response = 'accept'
                """,
                (swarm_id,),
            ).fetchone()
        if case_row:
            case = case_row["payload"]
            result["target_units"] = int(case.get("units_from_donors_remaining", 0))
            result["donations_completed"] = int(case.get("units_from_donors_fulfilled", 0))
        result["donors_contacted"] = int(notification_row["count"])
        result["confirmed_donors"] = int(accepted_row["count"])
    return {"status": "success", "data": result, "source": "postgresql" if database_url else "empty"}


def get_weather_forecast(region: str, horizon_days: int = 7) -> dict[str, Any]:
    """Return persisted Blood Weather demand intervals for a region."""
    data: dict[str, Any] = {
        "region": region,
        "forecast_7_day": [],
        "drivers": [],
        "expected_shortfall": {},
    }
    if os.getenv("BLOODNET_ENV", "local").lower() in {"production", "demo"}:
        from forecast_service import build_forecast_repository, forecast_response

        result = build_forecast_repository().get_forecast(region, horizon_days)
        if result:
            response = forecast_response(result, region, horizon_days)
            data["forecast_7_day"] = response["forecast"]
            data["latest_target_date"] = response["latest_target_date"]
            data["is_stale"] = response["is_stale"]
    return {"status": "success", "data": data, "source": "bigquery" if data["forecast_7_day"] else "empty"}


def get_inventory_status(
    blood_group: str | None = None,
    component: str | None = None,
    bank_id: str | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """Return deterministic inventory facts without reserving or mutating units."""
    result = get_network_inventory()
    result["data"] = {
        "blood_group": blood_group,
        "component": component,
        "bank_id": bank_id,
        "region": region,
        "available_by_group_component": result.pop("data"),
        "reserved_quantity": 0,
        "expiry_risk": [],
        "relevant_blood_banks": [],
    }
    return result


def get_demand_forecast(
    region: str,
    blood_group: str | None = None,
    component: str | None = None,
    horizon_days: int = 7,
) -> dict[str, Any]:
    result = get_weather_forecast(region, horizon_days)
    points = result["data"]["forecast_7_day"]
    if blood_group is not None:
        points = [point for point in points if point["blood_group"] == blood_group]
    if component is not None:
        points = [point for point in points if point["component"] == component]
    result["data"]["forecast_7_day"] = points
    result["data"].update({
        "blood_group": blood_group,
        "component": component,
        "horizon_days": horizon_days,
    })
    return result


def get_regional_overview(region: str) -> dict[str, Any]:
    """Return a PII-free operational snapshot limited to one region."""
    data: dict[str, Any] = {
        "region": region, "cases": [], "inventory_by_group_component": {},
        "inventory_by_status": {}, "inventory_by_bank": [], "expiry_risk_7_days": {},
        "eligible_donors_by_group": {}, "blood_banks": [], "swarms": [],
        "recommendations": [], "network_health": {},
    }
    database_url = _database_url()
    if not database_url:
        return {"status": "success", "data": data, "source": "empty"}
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        cases = connection.execute(
            """SELECT c.case_id, c.payload AS case_payload, r.payload AS request_payload,
                      p.swarm_status
               FROM workflow_cases c JOIN workflow_requests r
                 ON r.request_id = c.payload->>'request_id'
               LEFT JOIN workflow_case_projections p ON p.case_id = c.case_id
               WHERE r.payload->>'region' = %s ORDER BY c.updated_at DESC LIMIT 100""",
            (region,),
        ).fetchall()
        banks = connection.execute(
            """SELECT id::text, name, metadata FROM organizations
               WHERE type = 'blood_bank' AND status = 'active'
                 AND COALESCE(metadata->>'city', metadata->>'region_id', metadata->>'region') = %s
               ORDER BY name""",
            (region,),
        ).fetchall()
        bank_ids = [str((row["metadata"] or {}).get("bank_id") or row["id"]) for row in banks]
        inventory = connection.execute(
            """SELECT payload->>'group' AS blood_group,
                      payload->>'component' AS component, COUNT(*) AS units,
                      COUNT(*) FILTER (
                        WHERE (payload->>'expires_at')::timestamptz <= NOW() + INTERVAL '7 days'
                      ) AS expiring_7_days
               FROM inventory_units
               WHERE payload->>'status' = 'available' AND payload->>'bank_id' = ANY(%s)
               GROUP BY payload->>'group', payload->>'component'""",
            (bank_ids,),
        ).fetchall() if bank_ids else []
        inventory_status = connection.execute(
            """SELECT payload->>'bank_id' AS bank_id, payload->>'status' AS status,
                      COUNT(*) AS units
               FROM inventory_units WHERE payload->>'bank_id' = ANY(%s)
               GROUP BY payload->>'bank_id', payload->>'status'""",
            (bank_ids,),
        ).fetchall() if bank_ids else []
        donors = connection.execute(
            """SELECT blood_group, COUNT(*) AS eligible_count FROM donor_pool
               WHERE region = %s AND eligible = TRUE AND consent_contactable = TRUE
               GROUP BY blood_group""",
            (region,),
        ).fetchall()
        case_ids = [row["case_id"] for row in cases]
        recommendations = connection.execute(
            """SELECT rec_id, payload FROM workflow_recommendations
               WHERE payload->>'case_id' = ANY(%s)
               ORDER BY updated_at DESC LIMIT 100""",
            (case_ids,),
        ).fetchall() if case_ids else []
    case_keys = {"outcome", "urgency", "fulfillment_probability", "units_from_inventory", "units_from_donors_remaining", "units_from_donors_fulfilled", "escalation_state", "required_qty", "requested_qty"}
    request_keys = {"blood_group", "component", "qty", "required_by", "region"}
    data["cases"] = [{"case_id": row["case_id"], **{k: v for k, v in (row["case_payload"] or {}).items() if k in case_keys}, "request": {k: v for k, v in (row["request_payload"] or {}).items() if k in request_keys}} for row in cases]
    data["blood_banks"] = [{"bank_id": bank_id, "name": row["name"]} for row, bank_id in zip(banks, bank_ids)]
    data["inventory_by_group_component"] = {f"{row['blood_group']}|{row['component']}": int(row["units"]) for row in inventory}
    data["expiry_risk_7_days"] = {f"{row['blood_group']}|{row['component']}": int(row["expiring_7_days"]) for row in inventory if int(row["expiring_7_days"]) > 0}
    data["inventory_by_status"] = {}
    bank_inventory: dict[str, dict[str, Any]] = {
        bank_id: {"bank_id": bank_id, "name": row["name"], "by_status": {}}
        for row, bank_id in zip(banks, bank_ids)
    }
    for row in inventory_status:
        status = str(row["status"] or "unknown")
        units = int(row["units"])
        data["inventory_by_status"][status] = data["inventory_by_status"].get(status, 0) + units
        bank_inventory[str(row["bank_id"])]["by_status"][status] = units
    data["inventory_by_bank"] = list(bank_inventory.values())
    data["eligible_donors_by_group"] = {row["blood_group"]: int(row["eligible_count"]) for row in donors}
    safe_swarm_keys = {"status", "target_units", "cohort_size", "confirmed_donors", "donations_completed"}
    data["swarms"] = [
        {"case_id": row["case_id"], **{k: v for k, v in (row["swarm_status"] or {}).items() if k in safe_swarm_keys}}
        for row in cases if row["swarm_status"]
    ]
    recommendation_keys = {"type", "case_id", "rationale", "expected_impact", "confidence", "state", "created_at", "source"}
    data["recommendations"] = [
        {"rec_id": row["rec_id"], **{k: v for k, v in (row["payload"] or {}).items() if k in recommendation_keys}}
        for row in recommendations
    ]
    active_cases = [case for case in data["cases"] if case.get("outcome") not in {"fulfilled", "cancelled"}]
    required_units = sum(int(case.get("request", {}).get("qty", 0) or 0) for case in active_cases)
    covered_units = sum(int(case.get("units_from_inventory", 0) or 0) for case in active_cases)
    data["network_health"] = {
        "active_cases": len(active_cases),
        "shortage_exposure": sum(int(case.get("units_from_donors_remaining", 0) or 0) for case in active_cases),
        "inventory_coverage": round(covered_units / required_units, 4) if required_units else None,
        "fulfillment_probability": round(sum(float(case.get("fulfillment_probability", 0) or 0) for case in active_cases) / len(active_cases), 4) if active_cases else None,
        "escalated_cases": sum(1 for case in active_cases if case.get("escalation_state") == "required"),
    }
    return {"status": "success", "data": data, "source": "postgresql"}


def get_case_status(case_id: str) -> dict[str, Any]:
    """Return the persisted case projection, without exposing donor PII."""
    database_url = _database_url()
    case: dict[str, Any] = {"case_id": case_id}
    if database_url:
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT payload FROM workflow_cases WHERE case_id = %s", (case_id,)
            ).fetchone()
        if row:
            case.update(row["payload"])
    return {"status": "success", "data": case, "source": "postgresql" if database_url else "empty"}


def get_case_shortfall(case_id: str) -> dict[str, Any]:
    case_result = get_case_status(case_id)
    case = case_result["data"]
    required_qty = int(case.get("required_qty", case.get("requested_qty", 0)))
    inventory_coverage = int(case.get("units_from_inventory", 0))
    donor_gap = int(case.get("units_from_donors_remaining", 0))
    remaining_shortfall = max(required_qty - inventory_coverage, donor_gap)
    return {
        "status": "success",
        "data": {
            "case_id": case_id,
            "required_qty": required_qty,
            "inventory_coverage": inventory_coverage,
            "donor_gap": donor_gap,
            "remaining_shortfall": remaining_shortfall,
            "urgency": case.get("urgency", "unknown"),
        },
        "source": case_result["source"],
    }


def get_donor_mobilization_options(
    region: str,
    blood_group: str,
    radius_km: float = 5.0,
    case_id: str | None = None,
) -> dict[str, Any]:
    """Return deterministic donor aggregate and swarm options; never selects recipients."""
    pool = get_donor_pool(region, blood_group, radius_km)
    shortfall = get_case_shortfall(case_id)["data"] if case_id else None
    return {
        "status": "success",
        "data": {
            "region": region,
            "blood_group": blood_group,
            "radius_km": radius_km,
            "eligible_pool": pool["data"],
            "shortfall": shortfall,
            "cohort_size": None,
            "recipient_selection": "deterministic_matching_service",
        },
        "source": pool["source"],
    }


def get_expiry_risk(bank_id: str, threshold: int = 7) -> dict[str, Any]:
    """Return available units expiring within threshold days without PII."""
    units: list[dict[str, Any]] = []
    database_url = _database_url()
    if database_url:
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT payload
                FROM inventory_units
                WHERE payload->>'bank_id' = %s
                  AND payload->>'status' = 'available'
                  AND (payload->>'expires_at')::timestamptz <= NOW() + (%s * INTERVAL '1 day')
                ORDER BY (payload->>'expires_at')::timestamptz
                """,
                (bank_id, threshold),
            ).fetchall()
        units = [
            {key: payload[key] for key in ("unit_id", "group", "component", "expires_at")}
            for row in rows
            if (payload := row["payload"])
        ]
    return {"status": "success", "data": {"bank_id": bank_id, "threshold_days": threshold, "at_risk_units": units}, "source": "postgresql" if database_url else "empty"}


def get_donor_pool(region: str, group: str, radius_km: float) -> dict[str, Any]:
    """Return an aggregate donor pool; donor identity is never exposed."""
    database_url = _database_url()
    count = 0
    if database_url:
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM donor_pool
                WHERE region = %s AND blood_group = %s
                  AND consent_contactable = TRUE AND eligible = TRUE
                  AND radius_km <= %s
                """,
                (region, group, radius_km),
            ).fetchone()
        count = int(row["count"])
    return {
        "status": "success",
        "data": {
            "region": region,
            "group": group,
            "radius_km": radius_km,
            "eligible_count": count,
            "filters": {
                "eligible": True,
                "consent_contactable": True,
                "within_radius": True,
            },
            "privacy": "aggregate_only",
        },
        "source": "postgresql" if database_url else "empty",
    }


def score_donors(case_or_hypothetical_context: dict[str, Any], k: int = 10) -> dict[str, Any]:
    """Score explicitly eligibility-gated donor records without exposing PII."""
    donors = case_or_hypothetical_context.get("donors", [])
    eligible_ids = set(case_or_hypothetical_context.get("eligible_donor_ids", []))
    if not donors or not eligible_ids:
        return {"status": "success", "data": {"ranked_probabilities": [], "limit": k}, "source": "scoring"}
    from contracts.models import Donor
    from scoring import score_donors as model_score_donors

    eligible_donors = [Donor.model_validate(donor) for donor in donors if donor.get("donor_id") in eligible_ids]
    scores = model_score_donors(eligible_donors)
    ranked = sorted(
        ({"donor_id": donor.donor_id, "probability": probability} for donor, probability in zip(eligible_donors, scores)),
        key=lambda item: item["probability"],
        reverse=True,
    )
    return {"status": "success", "data": {"ranked_probabilities": ranked[:k], "limit": k}, "source": "donor_success_model"}


def find_compatible_inventory(
    region: str,
    blood_group: str,
    component: str,
    required_qty: int,
) -> dict[str, Any]:
    """Find facilities in a region that have compatible inventory to fulfill a requirement."""
    database_url = _database_url()
    compatible_facilities: list[dict[str, Any]] = []
    
    if database_url:
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            # Get blood banks in the region
            banks = connection.execute(
                """SELECT id::text, name, metadata FROM organizations
                   WHERE type = 'blood_bank' AND status = 'active'
                     AND COALESCE(metadata->>'city', metadata->>'region_id', metadata->>'region') = %s
                   ORDER BY name""",
                (region,),
            ).fetchall()
            
            bank_ids = [str((row["metadata"] or {}).get("bank_id") or row["id"]) for row in banks]
            
            # Find inventory at each bank that matches blood_group and component
            if bank_ids:
                rows = connection.execute(
                    """
                    SELECT payload->>'bank_id' AS bank_id,
                           payload->>'group' AS blood_group,
                           payload->>'component' AS component,
                           COUNT(*) AS available_units,
                           MAX((payload->>'expires_at')::timestamptz) AS latest_expiry
                    FROM inventory_units
                    WHERE payload->>'bank_id' = ANY(%s)
                      AND payload->>'status' = 'available'
                      AND payload->>'group' = %s
                      AND payload->>'component' = %s
                    GROUP BY payload->>'bank_id', payload->>'group', payload->>'component'
                    ORDER BY available_units DESC
                    """,
                    (bank_ids, blood_group, component),
                ).fetchall()
                
                # Map bank_id to bank names for display
                bank_name_map = {str((row["metadata"] or {}).get("bank_id") or row["id"]): row["name"] for row in banks}
                
                for row in rows:
                    available = int(row["available_units"])
                    if available > 0:
                        compatible_facilities.append({
                            "bank_id": str(row["bank_id"]),
                            "bank_name": bank_name_map.get(str(row["bank_id"]), "Unknown"),
                            "blood_group": row["blood_group"],
                            "component": row["component"],
                            "available_units": available,
                            "can_fulfill": available >= required_qty,
                            "shortage_if_used": max(0, required_qty - available),
                            "latest_expiry": row["latest_expiry"],
                        })
    
    # Calculate fulfillment summary
    total_compatible = sum(f["available_units"] for f in compatible_facilities)
    fulfillment_possible = any(f["can_fulfill"] for f in compatible_facilities)
    
    return {
        "status": "success",
        "data": {
            "region": region,
            "blood_group": blood_group,
            "component": component,
            "required_qty": required_qty,
            "compatible_facilities": compatible_facilities,
            "total_compatible_units": total_compatible,
            "fulfillment_possible": fulfillment_possible,
            "summary": {
                "facilities_with_stock": len(compatible_facilities),
                "facilities_can_fulfill": sum(1 for f in compatible_facilities if f["can_fulfill"]),
                "recommendation": (
                    f"Can fulfill requirement from single facility: {compatible_facilities[0]['bank_name']}"
                    if fulfillment_possible and compatible_facilities
                    else (
                        f"Requires transfer from multiple facilities ({len([f for f in compatible_facilities if f['available_units'] > 0])} facilities). "
                        f"Total available: {total_compatible} units, required: {required_qty}"
                        if total_compatible >= required_qty
                        else f"Insufficient compatible inventory. Available: {total_compatible} units, required: {required_qty}"
                    )
                ),
            },
        },
        "source": "postgresql" if database_url else "empty",
    }


def query_graph(analysis_type: str, params: dict[str, Any]) -> dict[str, Any]:
    database_url = _database_url()
    findings: list[dict[str, Any]] = []
    if database_url and analysis_type == "inventory_by_bank":
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT payload->>'bank_id' AS bank_id, COUNT(*) AS units
                FROM inventory_units WHERE payload->>'status' = 'available'
                GROUP BY payload->>'bank_id' ORDER BY units DESC
                """
            ).fetchall()
        findings = [dict(row) for row in rows]
    elif not database_url and analysis_type in {"supplier_concentration", "coverage_zones", "cascade"}:
        graph_dir = Path(__file__).resolve().parents[1] / "graph-svc"
        if str(graph_dir) not in sys.path:
            sys.path.insert(0, str(graph_dir))
        from graph_service import DonorEdge, NetworkGraph, SupplyEdge, TransferEdge

        graph = NetworkGraph(
            supply_edges=[SupplyEdge(**edge) for edge in params.get("supply_edges", [])],
            transfer_edges=[TransferEdge(**edge) for edge in params.get("transfer_edges", [])],
            donor_edges=[DonorEdge(**edge) for edge in params.get("donor_edges", [])],
            demand_by_region=params.get("demand_by_region", {}),
        )
        findings = [graph.analyze(analysis_type, params)]
    return {"status": "success", "data": {"analysis_type": analysis_type, "params": params, "findings": findings}, "source": "postgresql" if database_url else "empty"}


def simulate_intervention(intervention_spec: dict[str, Any]) -> dict[str, Any]:
    """Return a transparent local projection; production delegates to the digital twin."""
    baseline_shortfall = max(int(intervention_spec.get("baseline_shortfall", 0)), 0)
    recruited = max(int(intervention_spec.get("recruited_units", 0)), 0)
    transferred = max(int(intervention_spec.get("transferred_units", 0)), 0)
    resolved = min(baseline_shortfall, recruited + transferred)
    return {
        "status": "success",
        "data": {
            "intervention": intervention_spec,
            "projected_metrics": {
                "baseline_shortfall": baseline_shortfall,
                "projected_shortfall": baseline_shortfall - resolved,
                "projected_fulfillment_rate": 1.0 if baseline_shortfall == 0 else resolved / baseline_shortfall,
                "units_recruited": recruited,
                "units_transferred": transferred,
            },
            "assumptions": ["recruited and transferred units are compatible and available within the request window"],
        },
        "source": "local_digital_twin_projection",
    }


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def search_sops(query: str) -> dict[str, Any]:
    passages: list[dict[str, Any]] = []
    database_url = _database_url()
    if database_url:
        terms = [term for term in query.split() if term]
        vector_query = None
        try:
            vector_query = GeminiClient().embed_texts([query])[0]
        except GeminiUnavailable:
            vector_query = None

        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            if vector_query:
                vector_literal = _vector_literal(vector_query)
                try:
                    rows = connection.execute(
                        """
                        SELECT document_id, title, content, citation,
                               1 - (embedding <=> %s::vector) AS similarity
                        FROM sop_documents
                        WHERE embedding IS NOT NULL
                        ORDER BY embedding <=> %s::vector
                        LIMIT 5
                        """,
                        (vector_literal, vector_literal),
                    ).fetchall()
                    passages = [dict(row) for row in rows]
                except Exception:
                    passages = []

            if not passages:
                rows = connection.execute(
                    """
                    SELECT document_id, title, content, citation
                    FROM sop_documents
                    WHERE content ILIKE ANY(%s) OR title ILIKE ANY(%s)
                    ORDER BY document_id LIMIT 10
                    """,
                    ([f"%{term}%" for term in terms], [f"%{term}%" for term in terms]),
                ).fetchall()
                passages = [dict(row) for row in rows]
                for row in passages:
                    row.setdefault("similarity", None)
    source = "pgvector" if passages and any("similarity" in row for row in passages) else "postgresql" if database_url else "empty"
    return {"status": "success", "data": {"query": query, "passages": passages}, "source": source}


get_forecast = get_weather_forecast
get_inventory = get_network_inventory


def propose_recommendation(
    rec_payload: dict[str, Any] | str | None = None,
    recommendation_type: str | None = None,
    details: str | None = None,
    region_id: str | None = None,
) -> dict[str, Any]:
    """Persist a validated recommendation for human approval; never execute it."""
    recommendation_id = f"REC-{uuid4().hex}"
    if isinstance(rec_payload, str) and recommendation_type and details is None:
        rec_payload, recommendation_type, details = None, rec_payload, recommendation_type
    if rec_payload is not None:
        proposal = RecommendationProposal.model_validate(rec_payload)
        scoped_region = str(region_id or rec_payload.get("region_id") or "").strip()
        if not scoped_region:
            raise ValueError("Operational recommendations require a region_id")
        payload = {
            "rec_id": recommendation_id,
            "type": proposal.recommendation_type,
            "request_id": proposal.request_id,
            "case_id": proposal.case_id,
            "region_id": scoped_region,
            "payload": {
                "proposed_actions": [action.model_dump() for action in proposal.proposed_actions],
                "region_id": scoped_region,
            },
            "rationale": proposal.rationale,
            "evidence": [item.model_dump() for item in proposal.evidence],
            "expected_impact": proposal.expected_effect,
            "confidence": proposal.confidence,
            "provenance": proposal.provenance.model_dump(mode="json"),
            "state": "AWAITING_APPROVAL",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": "GEMINI",
        }
    else:
        if not recommendation_type or not details:
            raise ValueError("rec_payload or recommendation_type and details are required")
        scoped_region = str(region_id or "").strip()
        if not scoped_region:
            raise ValueError("Operational recommendations require a region_id")
        payload = {
            "rec_id": recommendation_id,
            "type": recommendation_type,
            "region_id": scoped_region,
            "payload": {"details": details, "region_id": scoped_region},
            "rationale": details,
            "state": "AWAITING_APPROVAL",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": "GEMINI",
        }
    database_url = _database_url()
    if database_url:
        with psycopg.connect(database_url) as connection:
            connection.execute(
                """
                INSERT INTO workflow_recommendations (rec_id, payload, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (rec_id) DO NOTHING
                """,
                (recommendation_id, Jsonb(payload)),
            )
    return {
        "status": "success",
        "message": f"Recommendation '{payload['type']}' queued for human review.",
        "recommendation_id": recommendation_id,
        "approval_id": recommendation_id,
        "state": "AWAITING_APPROVAL",
        "source": payload.get("source", "GEMINI"),
        "provenance": payload.get("provenance", {}),
        "persisted": bool(database_url),
    }


AGENT_TOOLS = {
    "get_regional_overview": get_regional_overview,
    "get_inventory_status": get_inventory_status,
    "get_demand_forecast": get_demand_forecast,
    "get_case_shortfall": get_case_shortfall,
    "get_case_status": get_case_status,
    "get_donor_mobilization_options": get_donor_mobilization_options,
    "get_forecast": get_forecast,
    "get_inventory": get_inventory,
    "get_expiry_risk": get_expiry_risk,
    "get_donor_pool": get_donor_pool,
    "find_compatible_inventory": find_compatible_inventory,
    "score_donors": score_donors,
    "query_graph": query_graph,
    "simulate_intervention": simulate_intervention,
    "search_sops": search_sops,
    "propose_recommendation": propose_recommendation,
}

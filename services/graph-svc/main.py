from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
from math import asin, cos, radians, sin, sqrt
import os
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from graph_service import DonorEdge, NetworkGraph, SupplyEdge, TransferEdge
from contracts.auth import Identity, get_identity, require_role
from contracts.location import operational_region

app = FastAPI(
    title="BloodNet Graph Service",
    version="0.2.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

NETWORK_BRIEFING_VERSION = 2


class GraphRequest(BaseModel):
    analysis_type: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    # Contract compatibility only. Live role-scoped data is authoritative.
    supply_edges: list[SupplyEdge] = Field(default_factory=list)
    transfer_edges: list[TransferEdge] = Field(default_factory=list)
    donor_edges: list[DonorEdge] = Field(default_factory=list)
    demand_by_region: dict[str, float] = Field(default_factory=dict)


def _geo(metadata: dict[str, Any] | None) -> tuple[float, float] | None:
    value = (metadata or {}).get("geo")
    if not isinstance(value, dict) or "lat" not in value or "lng" not in value:
        return None
    try:
        return float(value["lat"]), float(value["lng"])
    except (TypeError, ValueError):
        return None


def _distance_km(left: tuple[float, float] | None, right: tuple[float, float] | None) -> float:
    if not left or not right:
        return 0.0
    lat1, lon1, lat2, lon2 = map(radians, (left[0], left[1], right[0], right[1]))
    delta_lat, delta_lon = lat2 - lat1, lon2 - lon1
    value = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    return 6371.0088 * 2 * asin(sqrt(value))


def _analysis_region(identity: Identity, params: dict[str, Any]) -> str:
    requested = str(params.get("region") or "").strip()
    if identity.region_id and requested and requested.casefold() != identity.region_id.casefold():
        raise HTTPException(status_code=403, detail="Graph region is outside your authenticated scope")
    return str(identity.region_id or requested or "all")


def _load_live_graph_inputs(
    identity: Identity,
    region: str,
) -> tuple[list[SupplyEdge], list[TransferEdge], list[DonorEdge], dict[str, float]]:
    database_url = os.getenv("BLOODNET_DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="Graph data store is unavailable")
    region_filter = "" if region == "all" else region
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        # A refresh must not occupy the request until Cloud Run's gateway
        # timeout. The briefing endpoint serves the last snapshot on timeout.
        connection.execute("SET LOCAL statement_timeout = '120s'")
        organizations = connection.execute(
            "SELECT id::text, name, type, metadata FROM organizations WHERE status = 'active'"
        ).fetchall()
        case_rows = connection.execute(
            """
            SELECT c.case_id, c.payload AS case_payload, r.payload AS request_payload
            FROM workflow_cases c
            JOIN workflow_requests r ON r.request_id = c.payload->>'request_id'
            WHERE (%s = '' OR lower(r.payload->>'region') = lower(%s))
              AND r.updated_at >= NOW() - INTERVAL '30 days'
            ORDER BY jsonb_array_length(COALESCE(c.payload->'inventory_matches', '[]'::jsonb)) DESC,
                     r.updated_at DESC
            LIMIT 5000
            """,
            (region_filter, region_filter),
        ).fetchall()
        case_ids = [row["case_id"] for row in case_rows]
        projection_rows = (
            connection.execute(
                "SELECT case_id, ranked_donors FROM workflow_case_projections WHERE case_id = ANY(%s)",
                (case_ids,),
            ).fetchall()
            if case_ids
            else []
        )
        inventory_rows = connection.execute(
            """
            SELECT payload->>'bank_id' AS bank_id,
                   COALESCE(payload->>'group', '') AS blood_group,
                   COALESCE(payload->>'component', '') AS component,
                   COUNT(*)::int AS units
            FROM inventory_units
            WHERE payload->>'status' = 'available'
            GROUP BY 1, 2, 3
            """
        ).fetchall()
        transfer_rows = connection.execute(
            """
            SELECT payload FROM inventory_transfers
            WHERE status IN ('in_transit', 'received')
            ORDER BY updated_at DESC LIMIT 1000
            """
        ).fetchall()

    organization_by_id: dict[str, dict[str, Any]] = {}
    for organization in organizations:
        metadata = organization["metadata"] or {}
        aliases = {organization["id"]}
        if organization["type"] == "blood_bank" and metadata.get("bank_id"):
            aliases.add(str(metadata["bank_id"]))
        if organization["type"] == "hospital" and metadata.get("hospital_id"):
            aliases.add(str(metadata["hospital_id"]))
        for alias in aliases:
            organization_by_id[alias] = organization

    inventory = {
        (str(row["bank_id"]), str(row["blood_group"]), str(row["component"])): int(row["units"])
        for row in inventory_rows
    }
    banks = [organization for organization in organizations if organization["type"] == "blood_bank"]
    hospitals = [organization for organization in organizations if organization["type"] == "hospital"]
    donors_by_case = {row["case_id"]: row["ranked_donors"] or [] for row in projection_rows}
    supply_metrics: dict[tuple[str, str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"volume": 0.0, "eta_hours": 0.0, "distance_km": 0.0, "observations": 0.0, "region": ""}
    )
    donor_ids: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    demand: dict[str, float] = defaultdict(float)

    for row in case_rows:
        case = row["case_payload"] or {}
        request = row["request_payload"] or {}
        request_region = str(request.get("region") or region)
        hospital_id = str(request.get("hospital_id") or "unknown")
        blood_group = str(request.get("group") or "")
        component = str(request.get("component") or "")
        quantity = max(float(request.get("qty") or 0), 0.0)
        demand[request_region] += quantity
        matches = case.get("inventory_matches") or []
        for match in matches:
            bank_id = str(match.get("bank_id") or "")
            if not bank_id:
                continue
            key = (bank_id, hospital_id, blood_group, component)
            metrics = supply_metrics[key]
            metrics["volume"] += quantity
            metrics["region"] = metrics["region"] or request_region
            distance_km = _distance_km(
                _geo((organization_by_id.get(bank_id) or {}).get("metadata")),
                _geo((organization_by_id.get(hospital_id) or {}).get("metadata")),
            )
            metrics["distance_km"] += distance_km
            eta_hours = max(float(match.get("eta_min") or 0), 0.0) / 60.0
            metrics["eta_hours"] += eta_hours or (distance_km / 45.0 if distance_km else 0.0)
            metrics["observations"] += 1
            for donor in donors_by_case.get(row["case_id"], []):
                donor_id = str(donor.get("donor_id") or "")
                if donor_id:
                    donor_ids[(bank_id, request_region, str(donor.get("blood_group") or blood_group))].add(donor_id)

    # Current inventory is a valid supply relationship even when no recent
    # workflow case has produced a historical inventory match.
    for (bank_id, blood_group, component), units in inventory.items():
        bank = organization_by_id.get(bank_id)
        if not bank or not units:
            continue
        bank_metadata = bank["metadata"] or {}
        bank_region = operational_region(bank_metadata)
        bank_regions = {bank_region.casefold()} if bank_region else set()
        if region != "all" and str(region).strip().casefold() not in bank_regions:
            continue
        for hospital in hospitals:
            hospital_metadata = hospital["metadata"] or {}
            hospital_region = operational_region(hospital_metadata)
            hospital_regions = {hospital_region.casefold()} if hospital_region else set()
            if region != "all" and str(region).strip().casefold() not in hospital_regions:
                continue
            hospital_id = str(hospital["id"])
            key = (bank_id, hospital_id, blood_group, component)
            metrics = supply_metrics[key]
            metrics["inventory_units"] = max(metrics.get("inventory_units", 0.0), float(units))
            metrics["distance_km"] = _distance_km(_geo(bank_metadata), _geo(hospital_metadata))
            metrics["region"] = metrics["region"] or bank_region or region

    supply_edges = []
    for (bank_id, hospital_id, blood_group, component), metrics in supply_metrics.items():
        observations = max(metrics["observations"], 1.0)
        supply_edges.append(SupplyEdge(
            bank_id=bank_id,
            hospital_id=hospital_id,
            volume_30d=metrics["volume"],
            avg_lead_time_hours=metrics["eta_hours"] / observations,
            distance_km=metrics["distance_km"] / observations,
            inventory_units=int(metrics.get("inventory_units", inventory.get((bank_id, blood_group, component), 0))),
            blood_group=blood_group,
            component=component,
            region=operational_region((organization_by_id.get(bank_id) or {}).get("metadata") or {}) or str(metrics.get("region") or ""),
        ))

    donor_edges = [
        DonorEdge(
            donor_id=f"catchment:{bank_id}:{blood_group}",
            bank_id=bank_id,
            region=donor_region,
            group=blood_group,
            count=len(values),
        )
        for (bank_id, donor_region, blood_group), values in donor_ids.items()
    ]

    transfer_edges = []
    for row in transfer_rows:
        payload = row["payload"] or {}
        from_bank, to_bank = str(payload.get("from_bank") or ""), str(payload.get("to_bank") or "")
        if not from_bank or not to_bank:
            continue
        distance = float(payload.get("distance_km") or _distance_km(
            _geo((organization_by_id.get(from_bank) or {}).get("metadata")),
            _geo((organization_by_id.get(to_bank) or {}).get("metadata")),
        ))
        hours = float(payload.get("travel_time_hours") or payload.get("avg_hours") or (distance / 45.0 if distance else 0.0))
        transfer_edges.append(TransferEdge(
            from_bank=from_bank,
            to_bank=to_bank,
            avg_hours=hours,
            distance_km=distance,
            volume_30d=float(len(payload.get("units") or payload.get("unit_ids") or [])),
        ))
    return supply_edges, transfer_edges, donor_edges, dict(demand)


def _persist_analysis(
    region: str,
    analysis_type: str,
    params: dict[str, Any],
    result: dict[str, Any],
    topology: dict[str, Any],
) -> str | None:
    database_url = os.getenv("BLOODNET_DATABASE_URL")
    if not database_url:
        return None
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        for edge in topology.get("edges", []):
            edge_type = str(edge.get("type") or "relationship")
            source_type = "bank" if edge_type in {"supplies", "transfer"} else "entity"
            target_type = "hospital" if edge_type == "supplies" else "bank"
            edge_id = "EDGE-" + hashlib.sha256(
                f"{region}|{edge_type}|{edge.get('source')}|{edge.get('target')}|{edge.get('blood_group')}|{edge.get('component')}".encode("utf-8")
            ).hexdigest()[:24]
            connection.execute(
                """
                INSERT INTO network_graph_edges (
                    edge_id, region, source_type, source_id, target_type,
                    target_id, edge_type, blood_group, component, metrics,
                    observed_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (edge_id) DO UPDATE SET
                    metrics = EXCLUDED.metrics, observed_at = NOW(), updated_at = NOW()
                """,
                (
                    edge_id, region, source_type, str(edge.get("source")), target_type,
                    str(edge.get("target")), edge_type, edge.get("blood_group"),
                    edge.get("component"), Jsonb(edge),
                ),
            )
        row = connection.execute(
            """
            INSERT INTO network_analysis_snapshots (region, analysis_type, parameters, result)
            VALUES (%s, %s, %s, %s)
            RETURNING snapshot_id::text
            """,
            (region, analysis_type, Jsonb(params), Jsonb(result)),
        ).fetchone()
    return str(row["snapshot_id"]) if row else None


def _run_analysis(payload: GraphRequest, identity: Identity) -> dict[str, Any]:
    region = _analysis_region(identity, payload.params)
    supply_edges, transfer_edges, donor_edges, demand = _load_live_graph_inputs(identity, region)
    graph = NetworkGraph(supply_edges, transfer_edges, donor_edges, demand)
    data = graph.analyze(payload.analysis_type, payload.params)
    if payload.analysis_type == "network_briefing":
        data["metadata"] = {"version": NETWORK_BRIEFING_VERSION}
    topology = graph.topology()
    snapshot_id = _persist_analysis(region, payload.analysis_type, payload.params, data, topology)
    return {
        "status": "success",
        "snapshot_id": snapshot_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
        "nodes": topology["nodes"],
        "edges": topology["edges"],
    }


def _latest_snapshot(identity: Identity, analysis_type: str) -> dict[str, Any] | None:
    database_url = os.getenv("BLOODNET_DATABASE_URL")
    if not database_url:
        return None
    region = str(identity.region_id or "")
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        row = connection.execute(
            """
            SELECT snapshot_id::text, result, created_at
            FROM network_analysis_snapshots
            WHERE region = %s AND analysis_type = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (region, analysis_type),
        ).fetchone()
    if not row:
        return None
    result = row["result"] or {}
    if analysis_type == "network_briefing" and (result.get("metadata") or {}).get("version") != NETWORK_BRIEFING_VERSION:
        return None
    return {
        "status": "stale",
        "snapshot_id": str(row["snapshot_id"]),
        "generated_at": row["created_at"].isoformat(),
        "data": result,
        "stale": True,
    }


@app.post("/api/v1/graph/analysis")
def analyze(payload: GraphRequest, identity: Identity = Depends(get_identity)) -> dict:
    require_role(identity, "regional_admin", "auditor")
    try:
        return _run_analysis(payload, identity)
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Graph data store is temporarily unavailable") from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/graph/briefing")
def briefing(payload: GraphRequest, identity: Identity = Depends(get_identity)) -> dict:
    require_role(identity, "regional_admin", "auditor")
    # Normal dashboard loads should be fast and deterministic. A persisted
    # snapshot is sufficient until the user explicitly requests a refresh.
    if not payload.params.get("refresh"):
        cached = _latest_snapshot(identity, "network_briefing")
        if cached:
            return cached
    try:
        return _run_analysis(payload.model_copy(update={"analysis_type": "network_briefing"}), identity)
    except psycopg.Error as exc:
        cached = _latest_snapshot(identity, "network_briefing")
        if cached:
            return cached
        raise HTTPException(status_code=503, detail="Graph data store is temporarily unavailable") from exc


@app.get("/api/v1/graph/snapshots")
def snapshots(limit: int = 20, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    require_role(identity, "regional_admin", "auditor")
    database_url = os.getenv("BLOODNET_DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="Graph data store is unavailable")
    bounded = max(1, min(limit, 100))
    region = str(identity.region_id or "")
    where = "WHERE region = %s" if region else ""
    params: tuple[Any, ...] = (region, bounded) if region else (bounded,)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            f"""
            SELECT snapshot_id::text, region, analysis_type, parameters, result, created_at
            FROM network_analysis_snapshots
            {where}
            ORDER BY created_at DESC LIMIT %s
            """,
            params,
        ).fetchall()
    return {"snapshots": [dict(row) for row in rows]}

"""FastAPI boundary for bounded BloodNet investigations."""

import os
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
import psycopg
from psycopg.rows import dict_row

from agent_service import AgentService
from contracts.auth import Identity, get_identity, require_role


app = FastAPI(
    title="BloodNet Agent Service",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
agent_service = AgentService()


class ToolCall(BaseModel):
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class InvestigationRequest(BaseModel):
    calls: list[ToolCall] = Field(max_length=3)


class RecommendationRequest(InvestigationRequest):
    request_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=128)


class GeminiInvestigationRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class GeminiRecommendationRequest(GeminiInvestigationRequest):
    request_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=128)


class SopSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)


def _validate_regional_scope(payload: InvestigationRequest, identity: Identity) -> None:
    if identity.role == "regional_admin" and identity.region_id:
        for call in payload.calls:
            requested_region = call.arguments.get("region")
            if requested_region and requested_region != identity.region_id:
                raise HTTPException(status_code=403, detail="Investigation is outside your regional scope")


@app.post("/api/v1/investigations")
def investigate(payload: InvestigationRequest, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    require_role(identity, "regional_admin", "auditor")
    _validate_regional_scope(payload, identity)
    return agent_service.investigate([call.model_dump() for call in payload.calls])


@app.post("/api/v1/investigations/gemini")
def investigate_with_gemini(payload: GeminiInvestigationRequest, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    require_role(identity, "regional_admin", "auditor")
    return agent_service.investigate_with_gemini(
        payload.question,
        region_id=identity.region_id,
    )


@app.get("/api/v1/sops/status")
def sop_corpus_status(identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """Report corpus readiness without exposing connection or model secrets."""
    require_role(identity, "hospital_coordinator", "bank_admin", "regional_admin", "auditor")
    enabled = os.getenv("BLOODNET_RAG_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    database_url = os.getenv("BLOODNET_DATABASE_URL")
    document_count = 0
    embedded_count = 0
    last_updated = None
    if database_url:
        try:
            with psycopg.connect(database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS document_count,
                           COUNT(embedding) AS embedded_count,
                           MAX(updated_at) AS last_updated
                    FROM sop_documents
                    """
                ).fetchone()
                if row:
                    document_count = int(row["document_count"])
                    embedded_count = int(row["embedded_count"])
                    last_updated = row["last_updated"]
        except psycopg.Error:
            pass
    ready = enabled and document_count > 0 and embedded_count == document_count
    return {
        "ready": ready,
        "enabled": enabled,
        "document_count": document_count,
        "embedded_count": embedded_count,
        "last_updated": last_updated,
        "retrieval": "pgvector",
        "access": "read_only",
        "citations_required": True,
    }


@app.post("/api/v1/sops/search")
def search_sop_evidence(payload: SopSearchRequest, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    """Search only the bootstrapped SOP corpus and return citation-bearing passages."""
    require_role(identity, "hospital_coordinator", "bank_admin", "regional_admin", "auditor")
    status = sop_corpus_status(identity)
    if not status["ready"]:
        raise HTTPException(status_code=503, detail="The approved SOP corpus is not ready")
    result = agent_service.investigate([
        {"tool": "search_sops", "arguments": {"query": payload.query.strip()}},
    ])
    passages = result.get("findings", [{}])[0].get("data", {}).get("passages", []) if result.get("findings") else []
    if any(not item.get("document_id") or not item.get("citation") for item in passages):
        raise HTTPException(status_code=502, detail="SOP retrieval returned uncited evidence")
    return {
        "investigation_id": result["investigation_id"],
        "query": payload.query.strip(),
        "passages": passages,
        "citations": [item["citation"] for item in passages],
        "read_only": True,
    }


@app.post("/api/v1/recommendations")
def recommend(payload: RecommendationRequest, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    require_role(identity, "regional_admin")
    _validate_regional_scope(payload, identity)
    return agent_service.recommend(
        request_id=payload.request_id,
        case_id=payload.case_id,
        calls=[call.model_dump() for call in payload.calls],
    )


@app.post("/api/v1/recommendations/gemini")
def recommend_with_gemini(payload: GeminiRecommendationRequest, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    require_role(identity, "regional_admin")
    return agent_service.recommend_with_gemini(
        request_id=payload.request_id,
        case_id=payload.case_id,
        question=payload.question,
        region_id=identity.region_id,
    )

"""HTTP boundary for raw emergency request intake."""

from datetime import datetime

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from typing import Literal

from parser import create_intake_provider, extract_request_details
from document_parser import extract_document_text
from contracts.auth import Identity, get_identity, require_role

app = FastAPI(
    title="BloodNet Intake Service",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class ExtractRequest(BaseModel):
    raw_text: str = Field(min_length=1)
    hospital_id: str = Field(min_length=1)
    source_channel: str = "text"
    request_id: str | None = None
    required_by: datetime | None = None
    provider: Literal["local", "gemini", "vertex_ai_gemini", "vertexai_gemini"] | None = None


@app.post("/api/v1/extract")
def extract(payload: ExtractRequest, identity: Identity = Depends(get_identity)):
    require_role(identity, "hospital_coordinator")
    try:
        request, confidence = extract_request_details(
            payload.raw_text,
            hospital_id=payload.hospital_id,
            source_channel=payload.source_channel,
            request_id=payload.request_id,
            required_by=payload.required_by,
            provider=create_intake_provider(provider=payload.provider),
        )
        return {"request": request.model_dump(mode="json"), "confidence": confidence}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/extract/document")
async def extract_document(
    file: UploadFile = File(...),
    hospital_id: str = Form(..., min_length=1),
    source_channel: str = Form("document"),
    request_id: str | None = Form(None),
    required_by: datetime | None = Form(None),
    provider: Literal["local", "gemini"] | None = Form(None),
    identity: Identity = Depends(get_identity),
):
    """Extract and validate a request from a PDF or UTF-8 text upload."""
    require_role(identity, "hospital_coordinator")
    if file.content_type not in {"application/pdf", "text/plain"}:
        raise HTTPException(status_code=415, detail="Only PDF and plain-text documents are supported")
    content = await file.read(1 * 1024 * 1024 + 1)
    try:
        text = extract_document_text(content, file.content_type)
        request, confidence = extract_request_details(
            text,
            hospital_id=hospital_id,
            source_channel=source_channel,
            request_id=request_id,
            required_by=required_by,
            provider=create_intake_provider(provider=provider),
        )
        return {"request": request.model_dump(mode="json"), "confidence": confidence, "document": {"filename": file.filename, "content_type": file.content_type, "bytes": len(content)}}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/health")
def health_check():
    return {"status": "ok"}
"""Deploy-time bootstrap for the BloodNet pgvector SOP knowledge base."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
AGENT_SVC = ROOT / "services" / "agent-svc"
if str(AGENT_SVC) not in sys.path:
    sys.path.insert(0, str(AGENT_SVC))

from gemini_client import GeminiClient

SOP_DOCUMENTS = [
    {
        "document_id": "SOP-TRANSFUSION-07",
        "title": "Transfusion Safety and Release SOP",
        "content": (
            "Before blood product release, verify donor identity, confirm ABO/Rh compatibility, "
            "screen for infectious disease risk, review crossmatch compatibility, and ensure the "
            "component is still within approved storage time before distribution."
        ),
        "citation": "SOP-TRANSFUSION-07 section 4.2",
    },
    {
        "document_id": "SOP-DONOR-SCREEN-03",
        "title": "Donor Screening and Eligibility SOP",
        "content": (
            "Eligible donors must meet minimum hemoglobin, deferral, recent donation interval, and "
            "temporary-risk criteria. Confirm travel, medication, and symptom status before contact and "
            "donation scheduling."
        ),
        "citation": "SOP-DONOR-SCREEN-03 section 2.1",
    },
    {
        "document_id": "SOP-INVENTORY-12",
        "title": "Inventory Allocation and Release SOP",
        "content": (
            "Inventory releases must account for component expiry, cold-chain continuity, traceability, "
            "and clinical urgency. Reserve stock before donor mobilization when local supply is sufficient."
        ),
        "citation": "SOP-INVENTORY-12 section 6.4",
    },
]


def load_corpus(path: str | None, inline_json: str | None = None) -> list[dict[str, str]]:
    """Load and validate an operator-approved corpus, or local demo fixtures."""
    if path and inline_json:
        raise ValueError("Configure only one SOP corpus source")
    if path:
        corpus_path = Path(path).resolve()
        if corpus_path.stat().st_size > 5 * 1024 * 1024:
            raise ValueError("SOP corpus exceeds the 5 MB bootstrap limit")
        documents = json.loads(corpus_path.read_text(encoding="utf-8"))
    elif inline_json:
        if len(inline_json.encode("utf-8")) > 5 * 1024 * 1024:
            raise ValueError("SOP corpus exceeds the 5 MB bootstrap limit")
        documents = json.loads(inline_json)
    else:
        if os.getenv("BLOODNET_ENV", "local").lower() in {"production", "prod", "demo"}:
            raise RuntimeError("Production RAG bootstrap requires an approved file or BLOODNET_SOP_CORPUS_JSON")
        documents = SOP_DOCUMENTS
    if not isinstance(documents, list) or not 1 <= len(documents) <= 1_000:
        raise ValueError("SOP corpus must contain between 1 and 1,000 documents")
    required = {"document_id", "title", "content", "citation"}
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, document in enumerate(documents):
        if not isinstance(document, dict) or not required.issubset(document):
            raise ValueError(f"SOP document {index} is missing required fields")
        item = {field: str(document[field]).strip() for field in required}
        if not all(item.values()) or len(item["content"]) > 100_000:
            raise ValueError(f"SOP document {index} is empty or exceeds the content limit")
        if item["document_id"] in seen:
            raise ValueError(f"Duplicate SOP document_id: {item['document_id']}")
        seen.add(item["document_id"])
        normalized.append(item)
    return normalized


def main(corpus_path: str | None = None) -> None:
    database_url = os.environ.get("BLOODNET_DATABASE_URL")
    if not database_url:
        raise RuntimeError("BLOODNET_DATABASE_URL is required")
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT_ID")
    if not project_id:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for Gemini embeddings")

    documents = load_corpus(
        corpus_path or os.getenv("BLOODNET_SOP_CORPUS_PATH"),
        os.getenv("BLOODNET_SOP_CORPUS_JSON"),
    )
    embeddings = GeminiClient().embed_texts([item["content"] for item in documents])

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sop_documents (
                    document_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    citation TEXT NOT NULL,
                    embedding vector(768),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_name = 'sop_documents' AND column_name = 'embedding'"
            )
            if cursor.fetchone() is None:
                cursor.execute("ALTER TABLE sop_documents ADD COLUMN IF NOT EXISTS embedding vector(768)")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS sop_documents_embedding_idx ON sop_documents USING hnsw (embedding vector_cosine_ops)"
            )
            for item, vector in zip(documents, embeddings):
                cursor.execute(
                    """
                    INSERT INTO sop_documents (document_id, title, content, citation, embedding, updated_at)
                    VALUES (%s, %s, %s, %s, %s::vector, NOW())
                    ON CONFLICT (document_id) DO UPDATE
                    SET title = EXCLUDED.title,
                        content = EXCLUDED.content,
                        citation = EXCLUDED.citation,
                        embedding = EXCLUDED.embedding,
                        updated_at = NOW()
                    """,
                    (
                        item["document_id"], item["title"], item["content"], item["citation"],
                        "[" + ",".join(str(float(value)) for value in vector) + "]",
                    ),
                )

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            row = cursor.execute("SELECT COUNT(*) FROM sop_documents WHERE embedding IS NOT NULL").fetchone()
            print(f"SOP rows with embeddings: {row[0]}")
            query = cursor.execute("SELECT document_id, citation FROM sop_documents ORDER BY document_id").fetchall()
            print("SOP docs:")
            for document_id, citation in query:
                print(f" - {document_id}: {citation}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load an approved SOP JSON corpus into BloodNet RAG")
    parser.add_argument("--corpus", help="JSON array containing document_id, title, content, and citation")
    arguments = parser.parse_args()
    main(arguments.corpus)

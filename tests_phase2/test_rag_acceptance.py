"""Deterministic RAG retrieval and recommendation grounding acceptance tests."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT_SVC = ROOT / "services" / "agent-svc"
if str(AGENT_SVC) not in sys.path:
    sys.path.insert(0, str(AGENT_SVC))

import tools
from agent_service import AgentService


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "rag"


def _fixture_documents() -> list[dict[str, str]]:
    documents = []
    for path in sorted(FIXTURE_DIR.glob("*.md")):
        fields: dict[str, str] = {}
        content: list[str] = []
        in_content = False
        for line in path.read_text(encoding="ascii").splitlines():
            if line == "content:":
                in_content = True
            elif in_content:
                content.append(line.strip())
            elif ": " in line:
                key, value = line.split(": ", 1)
                fields[key] = value
        fields["content"] = " ".join(content)
        documents.append(fields)
    return documents


def test_known_rag_question_retrieves_expected_source_and_evidence(monkeypatch):
    documents = _fixture_documents()

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, sql, params=None):
            if "ORDER BY embedding" in sql:
                return SimpleNamespace(fetchall=lambda: [{
                    "document_id": documents[0]["document_id"],
                    "title": "Blood compatibility",
                    "content": documents[0]["content"],
                    "citation": documents[0]["document_id"],
                    "similarity": 0.97,
                }])
            return SimpleNamespace(fetchall=lambda: [])

    monkeypatch.setenv("BLOODNET_DATABASE_URL", "postgresql://rag-test")
    monkeypatch.setattr(tools.psycopg, "connect", lambda *args, **kwargs: FakeConnection())
    monkeypatch.setattr(tools.GeminiClient, "embed_texts", lambda self, texts: [[0.1, 0.2, 0.3]])

    result = tools.search_sops("ABO Rh compatibility emergency red cells")

    assert result["data"]["passages"][0]["document_id"] == "RAG-BLOOD-COMPATIBILITY"
    assert result["data"]["passages"][0]["similarity"] == 0.97
    assert result["data"]["passages"][0]["citation"] in result["data"]["passages"][0]["content"] or result["data"]["passages"][0]["citation"] == "RAG-BLOOD-COMPATIBILITY"


def test_recommendation_cannot_claim_unretrieved_rag_evidence():
    retrieved = {"status": "success", "data": {"passages": [{
        "document_id": "RAG-INVENTORY-POLICY",
        "citation": "RAG-INVENTORY-POLICY",
        "content": "Reserve compatible available units before consumption.",
    }]}}
    class FakeGemini:
        def generate_with_tools(self, *, contents, tool_declarations, tool_executor):
            result = tool_executor("search_sops", {"query": "reserve compatible units"})
            return "grounded investigation", [{"tool": "search_sops", "result": result}]

        def generate(self, *, contents, config=None):
            return '{"request_id":"REQ-RAG","case_id":"CASE-RAG","recommendation_type":"MOBILIZE_DONORS","rationale":"Use policy.","evidence":[{"source":"GEMINI","reference":"RAG-TRANSFER-POLICY","summary":"Fabricated source."}],"proposed_actions":[{"action_type":"MOBILIZE_DONORS","parameters":{}}],"expected_effect":{},"confidence":0.5,"provenance":{"model":"gemini","model_version":"test","data_snapshot_id":"snapshot-rag","tools_called":["search_sops"],"citations":["RAG-TRANSFER-POLICY"]}}'

    service = AgentService({
        "search_sops": lambda query: retrieved,
    }, gemini_client=FakeGemini())
    investigation = service.investigate([{"tool": "search_sops", "arguments": {"query": "reserve compatible units"}}])
    assert investigation["findings"][0]["data"]["passages"][0]["document_id"] == "RAG-INVENTORY-POLICY"

    with pytest.raises(ValueError, match="not retrieved"):
        service.recommend_with_gemini(
            request_id="REQ-RAG",
            case_id="CASE-RAG",
            question="Use the retrieved policy and recommend a safe action.",
        )


def test_retrieved_rag_source_is_attached_to_recommendation():
    class FakeGemini:
        def generate_with_tools(self, *, contents, tool_declarations, tool_executor):
            result = tool_executor("search_sops", {"query": "reserve compatible units"})
            return "grounded", [{"tool": "search_sops", "result": result}]

        def generate(self, *, contents, config=None):
            return '{"request_id":"REQ-RAG-POSITIVE","case_id":"CASE-RAG-POSITIVE","recommendation_type":"MOBILIZE_DONORS","rationale":"Use the retrieved policy.","evidence":[{"source":"GEMINI","reference":"RAG-INVENTORY-POLICY","summary":"Retrieved inventory policy."}],"proposed_actions":[{"action_type":"MOBILIZE_DONORS","parameters":{}}],"expected_effect":{},"confidence":0.8,"provenance":{"model":"gemini","model_version":"test","data_snapshot_id":"snapshot-rag","tools_called":["search_sops"],"citations":["RAG-INVENTORY-POLICY"]}}'

    retrieved = {"status": "success", "data": {"passages": [{"document_id": "RAG-INVENTORY-POLICY", "citation": "RAG-INVENTORY-POLICY", "content": "Reserve compatible available units before consumption."}]}}
    service = AgentService({"search_sops": lambda query: retrieved}, gemini_client=FakeGemini())
    result = service.recommend_with_gemini(request_id="REQ-RAG-POSITIVE", case_id="CASE-RAG-POSITIVE", question="Use the retrieved policy.")

    assert result["state"] == "AWAITING_APPROVAL"
    assert result["provenance"]["citations"] == ["call-1:search_sops"]
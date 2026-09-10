from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
import os

sys.path.insert(0, str(Path(__file__).parents[1]))

from gemini_client import GeminiClient, GeminiSettings, GeminiUnavailable  # noqa: E402
import tools  # noqa: E402


class FakeModels:
    def __init__(self) -> None:
        self.kwargs = None

    def generate_content(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(text="grounded response")


class FakeGemini:
    def __init__(self) -> None:
        self.models = FakeModels()


def test_gemini_client_uses_configured_model_and_output_budget():
    gemini = FakeGemini()
    client = GeminiClient(
        client=gemini,
        settings=GeminiSettings(model="test-model", max_output_tokens=123),
    )

    assert client.generate(contents="investigate") == "grounded response"
    assert gemini.models.kwargs["model"] == "test-model"
    assert gemini.models.kwargs["config"].max_output_tokens == 123


def test_vertex_agent_engine_runtime_is_configured_and_falls_back_locally(monkeypatch):
    monkeypatch.setenv("BLOODNET_AGENT_RUNTIME", "vertex_agent_engine")
    monkeypatch.setenv("BLOODNET_AGENT_ENGINE_ID", "projects/demo/locations/asia-south1/reasoningEngines/demo")
    client = GeminiClient(client=FakeGemini(), settings=GeminiSettings(model="test-model"))

    assert client.runtime_mode == "vertex_agent_engine"
    assert client.managed_runtime_configured is True

    monkeypatch.delenv("BLOODNET_AGENT_ENGINE_ID", raising=False)
    monkeypatch.delenv("BLOODNET_AGENT_ENGINE_ENDPOINT", raising=False)
    fallback_client = GeminiClient(client=FakeGemini(), settings=GeminiSettings(model="test-model"))
    assert fallback_client.runtime_mode == "vertex_agent_engine"
    assert fallback_client.managed_runtime_configured is False
    assert fallback_client.generate(contents="investigate") == "grounded response"


def test_managed_vertex_agent_engine_uses_configured_endpoint(monkeypatch):
    class FakeRequests:
        def __init__(self):
            self.calls = []

        def post(self, url, json, headers, timeout):
            self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
            return type("Response", (), {
                "raise_for_status": lambda self: None,
                "json": lambda self: {"answer": "managed answer", "trace": [{"tool": "get_case_status"}]},
            })()

    requests = FakeRequests()
    monkeypatch.setenv("BLOODNET_AGENT_RUNTIME", "vertex_agent_engine")
    monkeypatch.setenv("BLOODNET_AGENT_ENGINE_ENDPOINT", "https://example.invalid/agent")
    monkeypatch.setitem(__import__("sys").modules, "requests", type("Requests", (), {"post": requests.post, "RequestException": RuntimeError}))

    client = GeminiClient(client=FakeGemini(), settings=GeminiSettings(model="test-model"))
    result = client.generate_with_tools(
        contents="investigate",
        tool_declarations=[{"name": "get_case_status", "description": "status", "parameters": {}}],
        tool_executor=lambda **kwargs: {"status": "success"},
    )

    assert result == ("managed answer", [{"tool": "get_case_status"}])


def test_gemini_client_retries_empty_vertex_response():
    class IntermittentModels:
        def __init__(self) -> None:
            self.calls = 0

        def generate_content(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(text=None if self.calls == 1 else "vertex response")

    models = IntermittentModels()
    client = GeminiClient(client=SimpleNamespace(models=models))

    assert client.generate(contents="investigate") == "vertex response"
    assert models.calls == 2


def test_gemini_client_reads_vertex_candidate_text():
    response = SimpleNamespace(
        text=None,
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="candidate text")]))],
    )
    client = GeminiClient(client=SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kwargs: response
    )))

    assert client.generate(contents="investigate") == "candidate text"


def test_gemini_client_returns_deterministic_fallback_without_vertex_project(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    client = GeminiClient(settings=GeminiSettings())

    assert client.generate_or_fallback(
        contents="investigate", fallback="deterministic result"
    ) == ("deterministic result", False)


def test_gemini_client_classifies_authentication_failures():
    class FailingModels:
        def generate_content(self, **kwargs):
            raise RuntimeError("401 UNAUTHENTICATED: invalid API key")

    client = GeminiClient(
        client=SimpleNamespace(models=FailingModels()),
        settings=GeminiSettings(model="test-model"),
    )

    try:
        client.generate(contents="test")
    except RuntimeError as error:
        assert "Vertex AI authentication" in str(error)
    else:
        raise AssertionError("authentication failure was not reported")


def test_vertex_ai_client_uses_project_adc_without_api_key(monkeypatch):
    class FakeGenai:
        def Client(self, **kwargs):
            self.kwargs = kwargs
            return "vertex-client"

    fake_genai = FakeGenai()
    monkeypatch.setenv("BLOODNET_GEMINI_USE_VERTEX_AI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    client = GeminiClient(settings=GeminiSettings(vertex_location="asia-south1"))

    import sys
    monkeypatch.setitem(sys.modules, "google", type("Google", (), {"genai": fake_genai})())
    class HttpOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
    monkeypatch.setitem(sys.modules, "google.genai", type("Genai", (), {"types": type("Types", (), {"HttpOptions": HttpOptions})()})())

    assert client._get_client() == "vertex-client"
    assert fake_genai.kwargs["vertexai"] is True
    assert fake_genai.kwargs["project"] == "demo-project"
    assert "api_key" not in fake_genai.kwargs
    assert fake_genai.kwargs["http_options"].kwargs["api_version"] == "v1"


def test_gemini_client_embeds_texts_with_vertex_api():
    class EmbeddingModels:
        def __init__(self):
            self.kwargs = None

        def embed_content(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1, 0.2, 0.3])])

    gemini = SimpleNamespace(models=EmbeddingModels())
    client = GeminiClient(client=gemini, settings=GeminiSettings(model="text-embedding-004"))

    assert client.embed_texts(["blood safety guidance"]) == [[0.1, 0.2, 0.3]]
    assert gemini.models.kwargs["model"] == "text-embedding-004"


def test_search_sops_uses_vector_similarity_when_available(monkeypatch):
    calls = {}

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=None):
            calls["sql"] = sql
            calls["params"] = params
            return SimpleNamespace(fetchall=lambda: [{
                "document_id": "SOP-001",
                "title": "Blood safety SOP",
                "content": "Follow transfusion screening before release.",
                "citation": "SOP-TRANSFUSION-07 §4.2",
                "similarity": 0.87,
            }])

    monkeypatch.setenv("BLOODNET_DATABASE_URL", "postgresql://example")
    monkeypatch.setattr(tools.psycopg, "connect", lambda *args, **kwargs: FakeConnection())
    monkeypatch.setattr(tools.GeminiClient, "embed_texts", lambda self, texts: [[0.1, 0.2, 0.3]])

    result = tools.search_sops("blood safety guidance")

    assert result["status"] == "success"
    assert result["data"]["passages"][0]["document_id"] == "SOP-001"
    assert "ORDER BY embedding" in calls["sql"]
    assert calls["params"][0] == "[0.1,0.2,0.3]"

"""Small, bounded Gemini boundary for agent-svc."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


class GeminiUnavailable(RuntimeError):
    """Raised when Gemini cannot be reached and the caller should use fallback."""


def _safe_generation_error(exc: Exception, model: str) -> str:
    message = str(exc).lower()
    if "401" in message or "unauthenticated" in message:
        reason = "Vertex AI authentication was rejected"
    elif "403" in message or "permission" in message or "forbidden" in message:
        reason = "the Gemini API or model is not enabled for this credential"
    elif "404" in message or "not found" in message:
        reason = f"model '{model}' is unavailable for this API"
    elif "429" in message or "quota" in message or "resource exhausted" in message:
        reason = "the Gemini quota or rate limit was exceeded"
    elif "timeout" in message or "timed out" in message:
        reason = "the Gemini request timed out"
    else:
        reason = "the Gemini API request failed"
    return f"{reason}; verify Vertex AI access, model, quota, and network"


def _response_text(response: Any) -> str | None:
    text = getattr(response, "text", None)
    if text:
        return text
    parts: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                parts.append(part_text)
    return "".join(parts) or None


@dataclass(frozen=True)
class GeminiSettings:
    model: str = "gemini-2.5-flash"
    embedding_model: str = "text-embedding-004"
    max_output_tokens: int = 4096
    timeout_seconds: float = 45.0
    max_tool_calls: int = 3
    vertex_location: str = "asia-south1"

    @classmethod
    def from_env(cls) -> "GeminiSettings":
        return cls(
            model=os.getenv("BLOODNET_GEMINI_MODEL", cls.model),
            embedding_model=os.getenv("BLOODNET_GEMINI_EMBEDDING_MODEL", cls.embedding_model),
            max_output_tokens=int(os.getenv("BLOODNET_GEMINI_MAX_OUTPUT_TOKENS", cls.max_output_tokens)),
            timeout_seconds=float(os.getenv("BLOODNET_GEMINI_TIMEOUT_SECONDS", cls.timeout_seconds)),
            max_tool_calls=int(os.getenv("BLOODNET_GEMINI_MAX_TOOL_CALLS", cls.max_tool_calls)),
            vertex_location=os.getenv("BLOODNET_GEMINI_VERTEX_LOCATION", cls.vertex_location),
        )


class GeminiClient:
    """Agent-only Gemini client using Vertex AI Agent Engine as the managed runtime."""

    def __init__(self, *, client: Any | None = None,
                 settings: GeminiSettings | None = None) -> None:
        self.settings = settings or GeminiSettings.from_env()
        self._client = client
        self.runtime_mode = os.getenv("BLOODNET_AGENT_RUNTIME", "vertex_agent_engine").strip().lower()
        self.managed_runtime_configured = bool(
            os.getenv("BLOODNET_AGENT_ENGINE_ENDPOINT") or os.getenv("BLOODNET_AGENT_ENGINE_ID")
        )

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise GeminiUnavailable("Gemini support requires google-genai") from exc
        http_options = types.HttpOptions(
            # Pin the stable Vertex AI surface.  The SDK's default may move to
            # a beta endpoint between releases, which has caused otherwise
            # valid Gemini requests to fail before they reach the model.
            api_version="v1",
            timeout=int(self.settings.timeout_seconds * 1000),
        )
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID")
        if not project_id:
            raise GeminiUnavailable("GOOGLE_CLOUD_PROJECT is required for Vertex AI")
        self._client = genai.Client(
            vertexai=True,
            project=project_id,
            location=self.settings.vertex_location,
            http_options=http_options,
        )
        return self._client

    def generate(self, *, contents: str, config: Any | None = None) -> str:
        """Generate text or raise GeminiUnavailable so callers can use fallback."""
        try:
            client = self._get_client()
            if config is None:
                try:
                    from google.genai import types
                    config = types.GenerateContentConfig(
                        temperature=0,
                        max_output_tokens=self.settings.max_output_tokens,
                    )
                except ImportError:
                    config = type(
                        "GenerationConfig",
                        (),
                        {
                            "temperature": 0,
                            "max_output_tokens": self.settings.max_output_tokens,
                        },
                    )()
            for attempt in range(2):
                response = client.models.generate_content(
                    model=self.settings.model,
                    contents=contents,
                    config=config,
                )
                text = _response_text(response)
                if text:
                    return text
                if attempt == 1:
                    raise GeminiUnavailable("Vertex AI returned an empty response")
            raise GeminiUnavailable("Vertex AI generation did not return text")
        except GeminiUnavailable:
            raise
        except Exception as exc:  # Normalize SDK timeout, rate-limit, and transport errors.
            raise GeminiUnavailable(_safe_generation_error(exc, self.settings.model)) from exc

    def _managed_runtime_generate_with_tools(
        self,
        *,
        contents: str,
        tool_declarations: list[dict[str, Any]],
        tool_executor: Any,
    ) -> tuple[str, list[dict[str, Any]]] | None:
        """Use a deployed Vertex AI Agent Engine endpoint when configured.

        The engine endpoint is opt-in and fully optional; if it is absent or unreachable,
        the service falls back to the local Gemini tool loop for prototype compatibility.
        """
        if not self.managed_runtime_configured or self.runtime_mode != "vertex_agent_engine":
            return None

        endpoint = os.getenv("BLOODNET_AGENT_ENGINE_ENDPOINT")
        if not endpoint:
            return None

        try:
            import requests
        except ImportError as exc:
            raise GeminiUnavailable("requests is required to reach a managed Agent Engine endpoint") from exc

        try:
            payload = {
                "contents": contents,
                "tool_declarations": tool_declarations,
                "tool_executor": "local-proxy",
                "tools": [
                    {
                        "name": declaration.get("name"),
                        "description": declaration.get("description"),
                        "parameters": declaration.get("parameters"),
                    }
                    for declaration in tool_declarations
                ],
            }

            headers = {"Content-Type": "application/json"}
            try:
                import google.auth
                import google.auth.transport.requests
                credentials, _ = google.auth.default()
                auth_request = google.auth.transport.requests.Request()
                credentials.refresh(auth_request)
                headers["Authorization"] = f"Bearer {credentials.token}"
            except Exception:
                pass

            response = requests.post(endpoint, json=payload, headers=headers, timeout=self.settings.timeout_seconds)
            response.raise_for_status()
            data = response.json()
            answer = data.get("answer") or data.get("response") or data.get("text")
            trace = data.get("trace") or []
            if not answer:
                return None
            return str(answer), list(trace)
        except requests.RequestException as exc:
            raise GeminiUnavailable(f"Vertex AI Agent Engine request failed: {exc}") from exc
        except ValueError as exc:
            raise GeminiUnavailable(f"Vertex AI Agent Engine returned invalid JSON: {exc}") from exc
        except GeminiUnavailable:
            raise
        except Exception as exc:
            raise GeminiUnavailable(_safe_generation_error(exc, self.settings.model)) from exc

    def generate_with_tools(
        self,
        *,
        contents: str,
        tool_declarations: list[dict[str, Any]],
        tool_executor: Any,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Run the managed Agent Engine runtime when configured; otherwise use the prototype loop."""
        try:
            agent_result = self._managed_runtime_generate_with_tools(
                contents=contents,
                tool_declarations=tool_declarations,
                tool_executor=tool_executor,
            )
            if agent_result is not None:
                return agent_result
        except GeminiUnavailable:
            # Fall through to the local prototype loop so the service remains usable
            # while the managed Cloud runtime is still being wired up.
            pass

        try:
            client = self._get_client()
            from google.genai import types

            conversation: list[Any] = [contents]
            trace: list[dict[str, Any]] = []
            for _ in range(self.settings.max_tool_calls):
                config = types.GenerateContentConfig(
                    temperature=0,
                    max_output_tokens=self.settings.max_output_tokens,
                    tools=[types.Tool(function_declarations=tool_declarations)],
                    tool_config=types.ToolConfig(
                        function_calling_config=types.FunctionCallingConfig(
                            mode="ANY" if not trace else "AUTO"
                        )
                    ),
                )
                response = client.models.generate_content(
                    model=self.settings.model,
                    contents=conversation,
                    config=config,
                )
                calls = list(getattr(response, "function_calls", None) or [])
                if not calls:
                    text = _response_text(response)
                    if not text:
                        raise GeminiUnavailable("Gemini returned neither a tool call nor text")
                    return text, trace
                response_content = getattr(getattr(response, "candidates", [None])[0], "content", None)
                if response_content is not None:
                    conversation.append(response_content)
                tool_responses = []
                for call in calls:
                    name = getattr(call, "name", None)
                    arguments = getattr(call, "args", {}) or {}
                    if not name or not isinstance(arguments, dict):
                        raise GeminiUnavailable("Gemini returned an invalid function call")
                    result = tool_executor(name, arguments)
                    trace.append({"tool": name, "arguments": arguments, "result": result})
                    tool_responses.append({
                        "function_response": {"name": name, "response": result}
                    })
                conversation.append(types.Content(role="tool", parts=[
                    types.Part.from_function_response(name=item["function_response"]["name"],
                                                      response=item["function_response"]["response"])
                    for item in tool_responses
                ]))
            raise GeminiUnavailable("Gemini tool-call budget exceeded")
        except GeminiUnavailable:
            raise
        except Exception as exc:
            raise GeminiUnavailable(_safe_generation_error(exc, self.settings.model)) from exc

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts for pgvector similarity search."""
        clean_texts = [text for text in texts if isinstance(text, str) and text.strip()]
        if not clean_texts:
            return []
        try:
            client = self._get_client()
            response = client.models.embed_content(
                model=self.settings.embedding_model,
                contents=clean_texts,
            )
            embeddings = getattr(response, "embeddings", None) or []
            if not embeddings:
                raise GeminiUnavailable("Vertex AI embedding response did not contain embeddings")
            vectors: list[list[float]] = []
            for item in embeddings:
                if isinstance(item, dict):
                    values = item.get("values")
                else:
                    values = getattr(item, "values", None)
                if values is None:
                    raise GeminiUnavailable("Vertex AI embedding payload did not include vector values")
                vectors.append(list(values))
            return vectors
        except GeminiUnavailable:
            raise
        except Exception as exc:
            raise GeminiUnavailable(_safe_generation_error(exc, self.settings.embedding_model)) from exc

    def generate_or_fallback(self, *, contents: str, fallback: str,
                             config: Any | None = None) -> tuple[str, bool]:
        try:
            return self.generate(contents=contents, config=config), True
        except GeminiUnavailable:
            return fallback, False

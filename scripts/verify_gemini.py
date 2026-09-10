"""Verify the configured Gemini runtime without printing credentials."""

from __future__ import annotations

import os
import sys
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "agent-svc"))

from gemini_client import GeminiClient, GeminiUnavailable  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vertex-ai",
        action="store_true",
        help="Deprecated compatibility flag; Vertex AI is always used",
    )
    parser.add_argument(
        "--end-to-end",
        action="store_true",
        help="Run a live read-only investigation and queue its cited proposal",
    )
    parser.add_argument("--request-id", default="VERIFY-REQUEST")
    parser.add_argument("--case-id", default="VERIFY-CASE")
    parser.add_argument(
        "--question",
        default="Use get_forecast for region Pune and horizon_days 7, then recommend the next safe operational step.",
    )
    args = parser.parse_args()
    if args.vertex_ai:
        os.environ["BLOODNET_GEMINI_USE_VERTEX_AI"] = "true"
    os.environ["BLOODNET_GEMINI_USE_VERTEX_AI"] = "true"
    project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID")
    model = os.getenv("BLOODNET_GEMINI_MODEL", "gemini-2.5-flash")
    location = os.getenv("BLOODNET_GEMINI_VERTEX_LOCATION", "asia-south1")
    source = "Vertex AI / Application Default Credentials"

    print(f"Project: {project or '(not set)'}")
    print(f"Model: {model}")
    print(f"Vertex location: {location}")
    print(f"Credential source: {source}")
    try:
        client = GeminiClient()
        if args.end_to_end:
            try:
                from agent_service import AgentService
            except ImportError as exc:
                print(f"End-to-end check requires the project dependencies: {exc}", file=sys.stderr)
                return 1
            result = AgentService(gemini_client=client).recommend_with_gemini(
                request_id=args.request_id,
                case_id=args.case_id,
                question=args.question,
            )
        else:
            result = client.generate(contents="Reply with exactly LIVE_OK")
    except GeminiUnavailable as exc:
        print(f"Gemini check failed: {exc}", file=sys.stderr)
        print("Verify GOOGLE_CLOUD_PROJECT and Vertex AI Application Default Credentials.", file=sys.stderr)
        return 1
    if args.end_to_end:
        if result["provenance"].get("model") == "deterministic-fallback" or not result["provenance"].get("citations"):
            print("End-to-end check failed: Gemini fallback or no tool citations observed.", file=sys.stderr)
            return 1
        print(json.dumps({
            "recommendation_id": result["recommendation_id"],
            "state": result["state"],
            "source": result["source"],
            "provenance": result["provenance"],
            "persisted": result["persisted"],
        }, indent=2, default=str))
    else:
        print(f"Gemini response: {result.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
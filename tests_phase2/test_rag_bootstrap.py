import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_bootstrap_module():
    spec = importlib.util.spec_from_file_location("bloodnet_rag_bootstrap", ROOT / "scripts" / "bootstrap_sop_rag.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_production_bootstrap_requires_an_approved_corpus(monkeypatch):
    module = load_bootstrap_module()
    monkeypatch.setenv("BLOODNET_ENV", "production")
    with pytest.raises(RuntimeError, match="requires an approved file"):
        module.load_corpus(None)


def test_bootstrap_loads_validated_json_corpus(tmp_path):
    module = load_bootstrap_module()
    path = tmp_path / "approved-sops.json"
    path.write_text(json.dumps([{
        "document_id": "SOP-APPROVED-1",
        "title": "Approved SOP",
        "content": "Approved operational guidance.",
        "citation": "SOP-APPROVED-1 section 1",
    }]), encoding="utf-8")

    assert module.load_corpus(str(path))[0]["document_id"] == "SOP-APPROVED-1"

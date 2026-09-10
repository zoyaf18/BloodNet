import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "integration-svc"))

from clinical_adapters import clinical_request_text  # noqa: E402


def test_fhir_service_request_is_normalized():
    text = clinical_request_text("fhir", {
        "resourceType": "ServiceRequest",
        "code": {"text": "O positive RBC"},
        "quantityQuantity": {"value": 2, "unit": "units"},
    })
    assert text == "Need 2 units O positive RBC"


def test_hl7_order_is_normalized():
    text = clinical_request_text("hl7v2", "MSH|^~\\&\rORC|NW|123\rOBR|1|123|456|O positive RBC|STAT\r")
    assert "O positive RBC" in text
    assert "STAT" in text


def test_hl7_order_is_normalized_from_lf_lines():
    text = clinical_request_text("hl7v2", "MSH|^~\\&\nORC|NW|123\nOBR|1|123|456|O positive RBC|STAT\n")
    assert "O positive RBC" in text
    assert "STAT" in text


def test_clinical_adapter_rejects_wrong_resource():
    with pytest.raises(ValueError, match="ServiceRequest"):
        clinical_request_text("fhir", {"resourceType": "Observation"})

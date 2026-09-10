import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "intake-svc"))

from document_parser import extract_document_text  # noqa: E402
from parser import FallbackIntakeProvider, GeminiFlashProvider, extract_request  # noqa: E402


def test_local_intake_extracts_validated_request():
    request = extract_request(
        "Need 2 units of O positive blood urgent at Ruby Hall Clinic",
        hospital_id="H1",
        request_id="REQ-INTAKE-001",
        required_by=datetime(2026, 8, 23, 14, 0, tzinfo=timezone.utc),
    )

    assert request.request_id == "REQ-INTAKE-001"
    assert request.group.value == "O+"
    assert request.qty == 2
    assert request.urgency.value == "Critical"
    assert request.hospital_id == "H1"


def test_local_intake_rejects_missing_blood_group():
    try:
        extract_request("Need blood urgently", hospital_id="H1")
    except ValueError as exc:
        assert "blood group" in str(exc)
    else:
        raise AssertionError("missing blood group should fail validation")


class FakeResponse:
    text = '{"blood_group":"A+","component":"RBC","qty":3,"urgency":"High","confidence":0.95}'


class FakeModels:
    def __init__(self):
        self.kwargs = None

    def generate_content(self, **kwargs):
        self.kwargs = kwargs
        return FakeResponse()


class FakeClient:
    def __init__(self):
        self.models = FakeModels()


def test_gemini_flash_uses_structured_json_output():
    client = FakeClient()
    provider = GeminiFlashProvider(client=client, project_id="demo-project")

    extraction = provider.extract("Need 3 units A positive blood")

    assert extraction.blood_group.value == "A+"
    assert extraction.qty == 3
    assert client.models.kwargs["model"] == "gemini-2.5-flash"
    config = client.models.kwargs["config"]
    assert config.response_mime_type == "application/json"
    assert config.temperature == 0


def test_gemini_intake_model_is_configurable(monkeypatch):
    monkeypatch.setenv("BLOODNET_GEMINI_INTAKE_MODEL", "gemini-test-model")
    provider = GeminiFlashProvider(client=FakeClient(), project_id="demo-project")

    provider.extract("Need 3 units A positive blood")

    assert provider.model == "gemini-test-model"


def test_gemini_intake_rejects_unsupported_inference():
    class FakeResponse:
        text = '{"blood_group":"O+","component":"RBC","qty":1,"urgency":"High","confidence":0.95}'

    class FakeModels:
        def generate_content(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    provider = GeminiFlashProvider(client=FakeClient(), project_id="demo-project")
    try:
        provider.extract("Need blood urgently")
    except ValueError as exc:
        assert "not present" in str(exc)
    else:
        raise AssertionError("Gemini must not invent a missing blood group")


def test_gemini_intake_rejects_urgency_downgrade():
    class FakeResponse:
        text = '{"blood_group":"O+","component":"RBC","qty":2,"urgency":"Routine","confidence":0.95}'

    class FakeModels:
        def generate_content(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    provider = GeminiFlashProvider(client=FakeClient(), project_id="demo-project")
    try:
        provider.extract("Need 2 units of O positive blood urgent")
    except ValueError as exc:
        assert "urgent request" in str(exc)
    else:
        raise AssertionError("Gemini must preserve explicit urgency")


def test_gemini_intake_falls_back_when_structured_output_fails():
    class FailingProvider:
        def extract(self, text):
            raise RuntimeError("Gemini unavailable")

    extraction = FallbackIntakeProvider(FailingProvider()).extract(
        "Need 2 units of O positive blood urgent"
    )

    assert extraction.blood_group.value == "O+"
    assert extraction.qty == 2
    assert extraction.urgency.value == "Critical"


def test_default_fallback_is_lenient_and_deterministic():
    class FailingProvider:
        def extract(self, text):
            raise RuntimeError("Gemini unavailable")

    extraction = FallbackIntakeProvider(FailingProvider()).extract(
        "Emergency request: 3 bags of A positive packed cells at City Care Center"
    )

    assert extraction.blood_group.value == "A+"
    assert extraction.component.value == "RBC"
    assert extraction.qty == 3
    assert extraction.urgency.value == "Critical"


def test_document_parser_extracts_plain_text():
    assert extract_document_text(
        b"Need 2 units of O positive blood urgently", "text/plain"
    ).startswith("Need 2 units")


def test_document_parser_extracts_pdf_text(monkeypatch):
    import document_parser

    class FakePage:
        def extract_text(self):
            return "Need 2 units of O positive blood urgently"

    class FakeReader:
        pages = [FakePage()]

    monkeypatch.setattr("pypdf.PdfReader", lambda *_args, **_kwargs: FakeReader())
    text = document_parser.extract_document_text(b"pdf bytes", "application/pdf")
    assert "Need 2 units" in text


def test_document_parser_rejects_unsupported_type():
    try:
        extract_document_text(b"request", "application/zip")
    except ValueError as exc:
        assert "PDF and plain-text" in str(exc)
    else:
        raise AssertionError("unsupported document type should fail")


def test_document_parser_rejects_oversized_document():
    try:
        extract_document_text(b"x" * (1 * 1024 * 1024 + 1), "text/plain")
    except ValueError as exc:
        assert "1 MB" in str(exc)
    else:
        raise AssertionError("oversized document should fail")


def test_document_parser_rejects_excessive_extracted_text(monkeypatch):
    import document_parser

    class FakePage:
        def extract_text(self):
            return "x" * 100_001

    class FakeReader:
        def __init__(self, *_args, **_kwargs):
            self.pages = [FakePage()]

    monkeypatch.setattr("pypdf.PdfReader", FakeReader)
    try:
        document_parser.extract_document_text(b"pdf bytes", "application/pdf")
    except ValueError as exc:
        assert "100,000-character" in str(exc)
    else:
        raise AssertionError("excessive extracted text should fail")

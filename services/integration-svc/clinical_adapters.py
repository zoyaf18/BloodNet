"""Provider-neutral FHIR and HL7v2 clinical request adapters."""

import re
from typing import Any


def fhir_service_request_text(resource: dict[str, Any]) -> str:
    """Convert a FHIR ServiceRequest into extraction text without storing PHI."""
    code = resource.get("code") or {}
    quantity = resource.get("quantityQuantity") or resource.get("quantity") or {}
    code_text = code.get("text") or next(iter(code.get("coding", [])), {}).get("display", "")
    quantity_value = quantity.get("value", "") if isinstance(quantity, dict) else ""
    quantity_unit = quantity.get("unit", "units") if isinstance(quantity, dict) else "units"
    return f"Need {quantity_value} {quantity_unit} {code_text}".strip()


def hl7_order_text(message: str) -> str:
    """Extract a minimal request phrase from an HL7 ORU/ORM message.

    Accepts LF, CRLF, or CR-only delimiters and returns a comparable text
    phrase for the request extractor. If the standard OBR field tuple is not
    present, fall back to a lenient regex extract from the segment text.
    """
    segments = [seg.strip() for seg in re.sub(r"\r\n?", "\n", message).split("\n") if seg.strip()]
    fields = {}
    order = ""
    priority = ""

    for segment in segments:
        parts = segment.split("|")
        if not parts:
            continue
        if parts[0] == "OBR":
            fields["order"] = parts[4] if len(parts) > 4 else ""
            fields["priority"] = parts[5] if len(parts) > 5 else ""
            if fields.get("order"):
                order = fields["order"]
                priority = fields.get("priority", "")
        elif parts[0] == "ORC":
            fields["status"] = parts[1] if len(parts) > 1 else ""

    if not order:
        obr_match = re.search(r"OBR\|[^\|]*\|[^\|]*\|[^\|]*\|([^\|]+)\|([^\|]*)", message)
        if obr_match:
            order = obr_match.group(1).strip()
            priority = obr_match.group(2).strip()

    if not order:
        raise ValueError("HL7 message does not contain an OBR order")

    return f"Need {order} {priority}".strip()


def clinical_request_text(format_name: str, payload: dict[str, Any] | str) -> str:
    if format_name.lower() == "fhir":
        if not isinstance(payload, dict) or payload.get("resourceType") != "ServiceRequest":
            raise ValueError("FHIR payload must be a ServiceRequest resource")
        return fhir_service_request_text(payload)
    if format_name.lower() == "hl7v2":
        if not isinstance(payload, str):
            raise ValueError("HL7v2 payload must be a string")
        return hl7_order_text(payload)
    raise ValueError("Unsupported clinical integration format")

"""Edge and compliance security policy validation for BloodNet."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

SENSITIVE_DATA_KEYS = {
    "phone",
    "phone_number",
    "mobile",
    "email",
    "address",
    "dob",
    "date_of_birth",
    "mrn",
    "medical_record_number",
    "donor_phone",
    "contact_tokens",
    "notes",
    "message",
    "diagnosis",
    "medical_history",
    "patient_name",
    "name",
    "full_name",
    "first_name",
    "last_name",
    "contact",
    "contact_details",
    "ssn",
    "national_id",
    "insurance_id",
}

PHI_PATTERNS = (
    ("email", r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
    ("phone", r"(?<!\d)(?:\+?\d[\d .()-]{8,}\d)(?!\d)"),
)


@dataclass(frozen=True)
class EdgeSecurityConfig:
    environment: str
    project_id: str | None
    region: str | None
    allowed_origins: tuple[str, ...]
    secret_manager_name: str | None
    secret_rotation_schedule: str | None
    cmek_key: str | None
    vpc_service_perimeter: str | None
    dlp_template: str | None
    cloud_armor_enabled: bool
    api_gateway_enabled: bool


def redact_minimal_data(payload: Any) -> Any:
    """Return the payload with protected fields masked to reduce PHI exposure."""
    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            if str(key).lower() in SENSITIVE_DATA_KEYS:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_minimal_data(value)
        return redacted
    if isinstance(payload, list):
        return [redact_minimal_data(item) for item in payload]
    if isinstance(payload, tuple):
        return tuple(redact_minimal_data(item) for item in payload)
    return payload


def validate_minimal_data_handling(payload: Any) -> tuple[bool, list[str], Any]:
    """Log compliance issues for PHI exposures and return a sanitized copy."""
    issues: list[str] = []
    safe_payload = redact_minimal_data(payload)

    def walk(value: Any, path: str = "root") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                lowered = str(key).lower()
                if lowered in SENSITIVE_DATA_KEYS:
                    issues.append(f"Potential PHI field '{path}.{key}' was redacted")
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, str):
            for label, pattern in PHI_PATTERNS:
                if __import__("re").search(pattern, value):
                    issues.append(f"Potential PHI pattern '{label}' found at '{path}'")

    walk(payload)
    return (not issues, issues, safe_payload)


def load_edge_security_config() -> EdgeSecurityConfig:
    """Validate the production edge/compliance controls for the deployment."""
    environment = (os.getenv("BLOODNET_ENV", "local") or "local").lower()
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID")
    region = os.getenv("GCP_REGION") or os.getenv("GOOGLE_CLOUD_LOCATION")
    allowed_origins = tuple(
        origin.strip()
        for origin in os.getenv("BLOODNET_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    )
    secret_manager_name = os.getenv("BLOODNET_SECRET_MANAGER_NAME") or os.getenv("BLOODNET_JWT_SECRET_NAME")
    secret_rotation_schedule = os.getenv("BLOODNET_SECRET_ROTATION_SCHEDULE")
    cmek_key = os.getenv("BLOODNET_CMEK_KEY") or os.getenv("BLOODNET_CMEK_KEY_ID")
    vpc_service_perimeter = os.getenv("BLOODNET_VPC_SERVICE_CONTROLS_PERIMETER")
    dlp_template = os.getenv("BLOODNET_DLP_TEMPLATE")

    cloud_armor_enabled = bool(project_id and region and allowed_origins)
    api_gateway_enabled = bool(project_id and allowed_origins and secret_manager_name)

    if environment != "production":
        return EdgeSecurityConfig(
            environment=environment,
            project_id=project_id,
            region=region,
            allowed_origins=allowed_origins,
            secret_manager_name=secret_manager_name,
            secret_rotation_schedule=secret_rotation_schedule,
            cmek_key=cmek_key,
            vpc_service_perimeter=vpc_service_perimeter,
            dlp_template=dlp_template,
            cloud_armor_enabled=cloud_armor_enabled,
            api_gateway_enabled=api_gateway_enabled,
        )

    required = {
        "project_id": project_id,
        "region": region,
        "BLOODNET_ALLOWED_ORIGINS": allowed_origins,
        "BLOODNET_SECRET_MANAGER_NAME": secret_manager_name,
        "BLOODNET_SECRET_ROTATION_SCHEDULE": secret_rotation_schedule,
        "BLOODNET_CMEK_KEY": cmek_key,
        "BLOODNET_VPC_SERVICE_CONTROLS_PERIMETER": vpc_service_perimeter,
        "BLOODNET_DLP_TEMPLATE": dlp_template,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        expanded = []
        for name in missing:
            if name == "BLOODNET_SECRET_ROTATION_SCHEDULE":
                expanded.append("BLOODNET_SECRET_ROTATION_SCHEDULE (secret rotation schedule)")
            else:
                expanded.append(name)
        raise ValueError(
            "Production edge/compliance controls are incomplete: " + ", ".join(expanded)
        )

    if any(not origin.startswith("https://") for origin in allowed_origins):
        raise ValueError("Production origins must use https://")

    return EdgeSecurityConfig(
        environment=environment,
        project_id=project_id,
        region=region,
        allowed_origins=allowed_origins,
        secret_manager_name=secret_manager_name,
        secret_rotation_schedule=secret_rotation_schedule,
        cmek_key=cmek_key,
        vpc_service_perimeter=vpc_service_perimeter,
        dlp_template=dlp_template,
        cloud_armor_enabled=True,
        api_gateway_enabled=True,
    )

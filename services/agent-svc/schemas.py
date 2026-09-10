"""Validated argument contracts for agent read tools."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InventoryStatusInput(ToolArguments):
    blood_group: str | None = None
    component: str | None = None
    bank_id: str | None = None
    region: str | None = None


class DemandForecastInput(ToolArguments):
    region: str = Field(min_length=1, max_length=100)
    blood_group: str | None = None
    component: str | None = None
    horizon_days: int = Field(default=7, ge=1, le=14)


class WeatherForecastInput(ToolArguments):
    region: str = Field(min_length=1, max_length=100)
    horizon_days: int = Field(default=7, ge=1, le=14)


class RegionalOverviewInput(ToolArguments):
    region: str = Field(min_length=1, max_length=100)


class SearchSopsInput(ToolArguments):
    query: str = Field(min_length=3, max_length=1000)


class CaseInput(ToolArguments):
    case_id: str = Field(min_length=1, max_length=128)


class DonorMobilizationInput(ToolArguments):
    case_id: str | None = Field(default=None, min_length=1, max_length=128)
    region: str = Field(min_length=1, max_length=100)
    blood_group: str = Field(min_length=1, max_length=10)
    radius_km: float = Field(default=5.0, gt=0, le=100)


class DonorPoolInput(ToolArguments):
    region: str = Field(min_length=1, max_length=100)
    group: str = Field(min_length=1, max_length=10)
    radius_km: float = Field(default=5.0, gt=0, le=100)


class FindCompatibleInventoryInput(ToolArguments):
    region: str = Field(min_length=1, max_length=100)
    blood_group: str = Field(min_length=1, max_length=10)
    component: str = Field(min_length=1, max_length=50)
    required_qty: int = Field(ge=1, le=100000)


class SimulationInput(ToolArguments):
    intervention_spec: dict[str, Any]


class RecommendationAction(ToolArguments):
    action_type: Literal[
        "RESERVE_INVENTORY",
        "MOBILIZE_DONORS",
        "SEND_DONOR_NOTIFICATION",
        "TRANSFER_INVENTORY",
        "CREATE_DONATION_DRIVE",
    ]
    parameters: dict[str, Any] = Field(default_factory=dict)


class RecommendationEvidence(ToolArguments):
    source: Literal["DETERMINISTIC", "ML", "GEMINI"]
    reference: str = Field(min_length=1, max_length=256)
    summary: str = Field(min_length=1, max_length=2000)


class RecommendationProvenance(ToolArguments):
    model: str = Field(min_length=1, max_length=100)
    model_version: str = Field(min_length=1, max_length=100)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tools_called: list[str] = Field(default_factory=list, max_length=12)
    data_snapshot_id: str = Field(min_length=1, max_length=256)
    citations: list[str] = Field(default_factory=list, max_length=50)
    evidence_digests: list[str] = Field(default_factory=list, max_length=50)


class RecommendationProposal(ToolArguments):
    request_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=128)
    recommendation_type: Literal[
        "RESERVE_INVENTORY",
        "MOBILIZE_DONORS",
        "SEND_DONOR_NOTIFICATION",
        "TRANSFER_INVENTORY",
        "CREATE_DONATION_DRIVE",
    ]
    rationale: str = Field(min_length=1, max_length=4000)
    evidence: list[RecommendationEvidence] = Field(min_length=1, max_length=20)
    proposed_actions: list[RecommendationAction] = Field(min_length=1, max_length=5)
    expected_effect: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    provenance: RecommendationProvenance


READ_TOOL_SCHEMAS: dict[str, Type[BaseModel]] = {
    "get_regional_overview": RegionalOverviewInput,
    "get_inventory_status": InventoryStatusInput,
    "get_demand_forecast": DemandForecastInput,
    "get_forecast": WeatherForecastInput,
    "search_sops": SearchSopsInput,
    "get_case_shortfall": CaseInput,
    "get_case_status": CaseInput,
    "get_donor_mobilization_options": DonorMobilizationInput,
    "get_donor_pool": DonorPoolInput,
    "find_compatible_inventory": FindCompatibleInventoryInput,
    "simulate_intervention": SimulationInput,
}


def validate_tool_arguments(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    schema = READ_TOOL_SCHEMAS.get(tool)
    if schema is None:
        return arguments
    try:
        return schema.model_validate(arguments).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise ValueError(f"Invalid arguments for tool '{tool}': {exc}") from exc

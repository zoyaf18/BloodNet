import pytest

from schemas import validate_tool_arguments
from tools import (
    get_case_shortfall,
    get_donor_mobilization_options,
    get_inventory_status,
    propose_recommendation,
)


def test_read_tool_arguments_are_schema_validated():
    arguments = validate_tool_arguments(
        "get_demand_forecast", {"region": "Pune", "horizon_days": 7}
    )

    assert arguments == {"region": "Pune", "horizon_days": 7}

    assert validate_tool_arguments(
        "get_forecast", {"region": "Pune", "horizon_days": 14}
    ) == {"region": "Pune", "horizon_days": 14}

    with pytest.raises(ValueError, match="Invalid arguments"):
        validate_tool_arguments("get_demand_forecast", {"region": "Pune", "horizon_days": 30})

    with pytest.raises(ValueError, match="Invalid arguments"):
        validate_tool_arguments("get_forecast", {"region": "Pune", "horizon_days": 15})

    with pytest.raises(ValueError, match="Invalid arguments"):
        validate_tool_arguments("get_case_status", {"case_id": "CASE-1", "write": True})


def test_mvp_read_tools_are_empty_safe_and_non_mutating():
    inventory = get_inventory_status(blood_group="O-", component="RBC")
    options = get_donor_mobilization_options("Pune", "O-", case_id="CASE-1")
    shortfall = get_case_shortfall("CASE-1")

    assert inventory["status"] == "success"
    assert inventory["data"]["reserved_quantity"] == 0
    assert options["data"]["recipient_selection"] == "deterministic_matching_service"
    assert shortfall["data"]["remaining_shortfall"] == 0


def test_recommendation_requires_validated_actions_and_provenance():
    proposal = {
        "request_id": "REQ-1",
        "case_id": "CASE-1",
        "recommendation_type": "MOBILIZE_DONORS",
        "rationale": "Inventory does not cover the shortfall.",
        "evidence": [{
            "source": "DETERMINISTIC",
            "reference": "inventory-status-1",
            "summary": "Two units available; four required.",
        }],
        "proposed_actions": [{
            "action_type": "MOBILIZE_DONORS",
            "parameters": {"requested_units": 4},
        }],
        "expected_effect": {"remaining_shortfall": 0},
        "confidence": 0.91,
        "provenance": {
            "model": "gemini",
            "model_version": "test-model",
            "data_snapshot_id": "snapshot-1",
            "tools_called": ["get_case_shortfall"],
            "citations": ["inventory-status-1"],
        },
    }

    result = propose_recommendation(rec_payload=proposal, region_id="Pune")

    assert result["state"] == "AWAITING_APPROVAL"
    assert result["recommendation_id"].startswith("REC-")

    invalid = {**proposal, "proposed_actions": [{"action_type": "DELETE_DATA"}]}
    with pytest.raises(ValueError):
        propose_recommendation(rec_payload=invalid)

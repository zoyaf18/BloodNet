"""Privacy-safe, read-only projections for the unauthenticated dashboard.

The public surface deliberately consumes the same repositories used by the
operational application.  When a development database has no meaningful data,
the response falls back to an explicitly labelled deterministic simulation so
the landing page remains useful without presenting demo numbers as live facts.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from contracts.location import operational_region


BLOOD_GROUPS = ("O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-")

# These are the operational regions already offered by the registration flow.
# Coordinates are map metadata only; supply and demand values still come from
# BloodNet repositories.
REGIONS: tuple[dict[str, Any], ...] = (
    {"id": "north", "name": "North", "state": "India region", "lat": 28.1, "lng": 77.2, "zoom": 5},
    {"id": "south", "name": "South", "state": "India region", "lat": 13.2, "lng": 78.4, "zoom": 5},
    {"id": "east", "name": "East", "state": "India region", "lat": 23.2, "lng": 87.4, "zoom": 5},
    {"id": "west", "name": "West", "state": "India region", "lat": 21.0, "lng": 73.2, "zoom": 5},
    {"id": "central", "name": "Central", "state": "India region", "lat": 23.5, "lng": 79.4, "zoom": 5},
    {"id": "pune", "name": "Pune", "state": "Maharashtra", "lat": 18.5204, "lng": 73.8567, "zoom": 9},
    {"id": "mumbai", "name": "Mumbai", "state": "Maharashtra", "lat": 19.0760, "lng": 72.8777, "zoom": 9},
    {"id": "delhi-ncr", "name": "Delhi NCR", "state": "Delhi", "lat": 28.6139, "lng": 77.2090, "zoom": 8},
    {"id": "bengaluru", "name": "Bengaluru", "state": "Karnataka", "lat": 12.9716, "lng": 77.5946, "zoom": 9},
    {"id": "hyderabad", "name": "Hyderabad", "state": "Telangana", "lat": 17.3850, "lng": 78.4867, "zoom": 9},
    {"id": "chennai", "name": "Chennai", "state": "Tamil Nadu", "lat": 13.0827, "lng": 80.2707, "zoom": 9},
)

REGION_BY_ID = {region["id"]: region for region in REGIONS}
CITIES: tuple[dict[str, Any], ...] = (
    {"id": "delhi", "name": "Delhi", "zone": "north", "lat": 28.6139, "lng": 77.2090, "zoom": 9},
    {"id": "jaipur", "name": "Jaipur", "zone": "north", "lat": 26.9124, "lng": 75.7873, "zoom": 9},
    {"id": "lucknow", "name": "Lucknow", "zone": "north", "lat": 26.8467, "lng": 80.9462, "zoom": 9},
    {"id": "chandigarh", "name": "Chandigarh", "zone": "north", "lat": 30.7333, "lng": 76.7794, "zoom": 9},
    {"id": "dehradun", "name": "Dehradun", "zone": "north", "lat": 30.3165, "lng": 78.0322, "zoom": 9},
    {"id": "bengaluru", "name": "Bengaluru", "zone": "south", "lat": 12.9716, "lng": 77.5946, "zoom": 9},
    {"id": "chennai", "name": "Chennai", "zone": "south", "lat": 13.0827, "lng": 80.2707, "zoom": 9},
    {"id": "hyderabad", "name": "Hyderabad", "zone": "south", "lat": 17.3850, "lng": 78.4867, "zoom": 9},
    {"id": "kochi", "name": "Kochi", "zone": "south", "lat": 9.9312, "lng": 76.2673, "zoom": 9},
    {"id": "coimbatore", "name": "Coimbatore", "zone": "south", "lat": 11.0168, "lng": 76.9558, "zoom": 9},
    {"id": "kolkata", "name": "Kolkata", "zone": "east", "lat": 22.5726, "lng": 88.3639, "zoom": 9},
    {"id": "bhubaneswar", "name": "Bhubaneswar", "zone": "east", "lat": 20.2961, "lng": 85.8245, "zoom": 9},
    {"id": "guwahati", "name": "Guwahati", "zone": "east", "lat": 26.1445, "lng": 91.7362, "zoom": 9},
    {"id": "ranchi", "name": "Ranchi", "zone": "east", "lat": 23.3441, "lng": 85.3096, "zoom": 9},
    {"id": "patna", "name": "Patna", "zone": "east", "lat": 25.5941, "lng": 85.1376, "zoom": 9},
    {"id": "mumbai", "name": "Mumbai", "zone": "west", "lat": 19.0760, "lng": 72.8777, "zoom": 9},
    {"id": "pune", "name": "Pune", "zone": "west", "lat": 18.5204, "lng": 73.8567, "zoom": 9},
    {"id": "ahmedabad", "name": "Ahmedabad", "zone": "west", "lat": 23.0225, "lng": 72.5714, "zoom": 9},
    {"id": "surat", "name": "Surat", "zone": "west", "lat": 21.1702, "lng": 72.8311, "zoom": 9},
    {"id": "goa", "name": "Goa", "zone": "west", "lat": 15.2993, "lng": 74.1240, "zoom": 9},
    {"id": "bhopal", "name": "Bhopal", "zone": "central", "lat": 23.2599, "lng": 77.4126, "zoom": 9},
    {"id": "indore", "name": "Indore", "zone": "central", "lat": 22.7196, "lng": 75.8577, "zoom": 9},
    {"id": "nagpur", "name": "Nagpur", "zone": "central", "lat": 21.1458, "lng": 79.0882, "zoom": 9},
    {"id": "raipur", "name": "Raipur", "zone": "central", "lat": 21.2514, "lng": 81.6296, "zoom": 9},
    {"id": "varanasi", "name": "Varanasi", "zone": "central", "lat": 25.3176, "lng": 82.9739, "zoom": 9},
)
CITY_BY_NAME = {city["name"].lower(): city for city in CITIES}
REGION_ALIASES = {
    "delhi": "delhi-ncr",
    "delhi ncr": "delhi-ncr",
    "new delhi": "delhi-ncr",
    "bangalore": "bengaluru",
}

def _region_id(value: Any) -> str | None:
    if not value:
        return None
    normalized = "-".join(str(value).strip().lower().replace("_", " ").split())
    normalized = REGION_ALIASES.get(normalized.replace("-", " "), normalized)
    return normalized if normalized in REGION_BY_ID else None


def _status(percent: float) -> str:
    if percent >= 70:
        return "GOOD"
    if percent >= 40:
        return "MODERATE"
    if percent >= 20:
        return "LOW"
    return "CRITICAL"


def _selected_region_ids(region: str) -> list[str]:
    selected = _region_id(region)
    return [selected] if selected else list(REGION_BY_ID)


def _safe_live_snapshot(workflow_store: Any, auth_repo: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "units": [],
        "requests": {},
        "cases": {},
        "organizations": [],
        "donors": [],
        "audit": [],
    }
    if workflow_store is None:
        return snapshot
    try:
        snapshot["units"] = workflow_store.repository.get_all_units()
        snapshot["requests"] = dict(workflow_store.requests)
        snapshot["cases"] = dict(workflow_store.approval_service.cases)
        snapshot["audit"] = workflow_store.audit.all()
    except Exception:
        # Public availability must not affect the operational hot path.
        pass
    try:
        snapshot["organizations"] = auth_repo.list_organizations("active")
        snapshot["donors"] = auth_repo.list_public_donor_counts()
    except Exception:
        pass
    return snapshot


def _organization_maps(organizations: list[Any]) -> tuple[dict[str, str], Counter, Counter]:
    bank_regions: dict[str, str] = {}
    hospitals: Counter = Counter()
    banks: Counter = Counter()
    for organization in organizations:
        metadata = organization.metadata or {}
        city = operational_region(metadata)
        city_record = CITY_BY_NAME.get(city.casefold()) if city else None
        # The landing page is the one public surface that intentionally
        # aggregates city data into map clusters.
        region_id = city_record["zone"] if city_record else _region_id(metadata.get("region"))
        if not region_id:
            continue
        if organization.type.value == "blood_bank":
            # Operational units may use either the organization UUID or the
            # bank's external/canonical ID. Support both representations.
            bank_regions[str(organization.id)] = region_id
            if metadata.get("bank_id"):
                bank_regions[str(metadata["bank_id"])] = region_id
            banks[region_id] += 1
        elif organization.type.value == "hospital":
            hospitals[region_id] += 1
    return bank_regions, hospitals, banks


def _request_region(request: dict[str, Any]) -> str | None:
    return _region_id(request.get("region"))


def _live_region_metrics(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    bank_regions, hospitals, banks = _organization_maps(snapshot["organizations"])
    result = {
        region["id"]: {
            "inventory": Counter(),
            "hospitals": hospitals[region["id"]],
            "banks": banks[region["id"]],
            "donors": 0,
            "requests": 0,
            "critical": 0,
            "fulfilled": 0,
        }
        for region in REGIONS
    }
    for unit in snapshot["units"]:
        region_id = bank_regions.get(unit.bank_id)
        status = getattr(unit.status, "value", unit.status)
        if region_id and status == "available":
            result[region_id]["inventory"][str(getattr(unit.group, "value", unit.group))] += 1

    for donor_row in snapshot["donors"]:
        region_id = _region_id(donor_row.get("region"))
        if region_id:
            result[region_id]["donors"] += int(donor_row.get("count", 0))

    for case in snapshot["cases"].values():
        request = snapshot["requests"].get(case.request_id, {})
        region_id = _request_region(request)
        if not region_id:
            continue
        request_status = str(request.get("status", "")).lower()
        if request_status == "fulfilled":
            result[region_id]["fulfilled"] += 1
            continue
        if request_status in {"cancelled", "unfulfilled"}:
            continue
        outcome = str(getattr(case.outcome, "value", case.outcome))
        if outcome == "fulfilled":
            result[region_id]["fulfilled"] += 1
            continue
        if outcome in {"cancelled", "unfulfilled"}:
            continue
        result[region_id]["requests"] += 1
        urgency = str(request.get("urgency", "")).lower()
        if urgency == "critical" or int(case.units_from_donors_remaining or 0) > 0:
            result[region_id]["critical"] += 1
    return result


def _live_city_metrics(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {city["id"]: {"inventory": Counter(), "hospitals": 0, "banks": 0, "donors": 0, "requests": 0, "critical": 0, "fulfilled": 0} for city in CITIES}
    bank_cities: dict[str, str] = {}
    for organization in snapshot["organizations"]:
        metadata = organization.metadata or {}
        city = CITY_BY_NAME.get(str(metadata.get("city", "")).lower())
        if not city:
            continue
        if organization.type.value == "blood_bank":
            bank_cities[str(organization.id)] = city["id"]
            if metadata.get("bank_id"):
                bank_cities[str(metadata["bank_id"])] = city["id"]
            result[city["id"]]["banks"] += 1
        elif organization.type.value == "hospital":
            result[city["id"]]["hospitals"] += 1
    for unit in snapshot["units"]:
        city_id = bank_cities.get(unit.bank_id)
        if city_id and getattr(unit.status, "value", unit.status) == "available":
            result[city_id]["inventory"][str(getattr(unit.group, "value", unit.group))] += 1
    for donor in snapshot["donors"]:
        city = CITY_BY_NAME.get(str(donor.get("city", "")).lower())
        if city:
            result[city["id"]]["donors"] += int(donor.get("count", 0))
    for case in snapshot["cases"].values():
        request = snapshot["requests"].get(case.request_id, {})
        city = CITY_BY_NAME.get(str(request.get("city", "")).lower())
        if not city:
            continue
        status = str(request.get("status", "")).lower()
        outcome = str(getattr(case.outcome, "value", case.outcome))
        if status == "fulfilled" or outcome == "fulfilled":
            result[city["id"]]["fulfilled"] += 1
        elif status not in {"cancelled", "unfulfilled"} and outcome not in {"cancelled", "unfulfilled"}:
            result[city["id"]]["requests"] += 1
            if str(request.get("urgency", "")).lower() == "critical" or int(case.units_from_donors_remaining or 0) > 0:
                result[city["id"]]["critical"] += 1
    return result


def _has_live_data(metrics: dict[str, Any]) -> bool:
    return bool(
        sum(metrics["inventory"].values())
        or metrics["banks"]
        or metrics["hospitals"]
        or metrics["requests"]
        or metrics["donors"]
        or metrics["critical"]
        or metrics["fulfilled"]
    )


def _forecast_projection(forecast_repository: Any, region: str) -> tuple[list[dict[str, Any]], Counter]:
    if forecast_repository is None:
        return [], Counter()
    try:
        result = forecast_repository.get_forecast(region, 7)
        if result is None:
            return [], Counter()
        by_date: defaultdict[str, dict[str, float]] = defaultdict(lambda: {"demand": 0.0, "supply": 0.0})
        demand_by_group: Counter = Counter()
        for point in result.points:
            day = point.target_date.isoformat()
            demand = float(point.predicted_demand)
            by_date[day]["demand"] += demand
            by_date[day]["supply"] += float(point.projected_supply)
            demand_by_group[point.blood_group] += demand
        return [
            {"date": day, "demand": round(values["demand"], 1), "supply": round(values["supply"], 1)}
            for day, values in sorted(by_date.items())
        ][:7], demand_by_group
    except Exception:
        return [], Counter()


def _activity(snapshot: dict[str, Any], selected_ids: list[str], city_name: str | None = None) -> list[dict[str, str]]:
    request_regions = {
        request_id: _request_region(request)
        for request_id, request in snapshot["requests"].items()
    }
    messages = {
        "inventory_allocation_completed": "Inventory allocation completed",
        "case_fulfilled": "A blood request was fulfilled",
        "case_escalated": "A supply shortfall was escalated",
        "recommendation_approved": "A fulfillment plan received human approval",
        "donor_notification_sent": "Eligible donors were mobilized",
        "blood_bank_inventory_synchronized": "Blood-bank inventory was synchronized",
    }
    sanitized: list[dict[str, str]] = []
    for record in sorted(snapshot["audit"], key=lambda item: item.at, reverse=True):
        region_id = request_regions.get(record.request_id)
        if region_id not in selected_ids or record.action not in messages:
            continue
        request = snapshot["requests"].get(record.request_id, {})
        if city_name and str(request.get("city", "")).strip().lower() != city_name.lower():
            continue
        sanitized.append({
            "message": messages[record.action],
            "region": city_name or REGION_BY_ID[region_id]["name"],
            "at": record.at.isoformat(),
        })
        if len(sanitized) == 4:
            break
    return sanitized


def build_public_dashboard(
    workflow_store: Any,
    forecast_repository: Any,
    auth_repo: Any,
    selected_region: str = "all",
) -> dict[str, Any]:
    """Build a safe aggregate projection without exposing record identifiers."""
    snapshot = _safe_live_snapshot(workflow_store, auth_repo)
    live_metrics = _live_region_metrics(snapshot)
    city_metrics = _live_city_metrics(snapshot)
    live_region_ids = {
        region_id for region_id, metrics in live_metrics.items() if _has_live_data(metrics)
    }
    available_region_ids = [region["id"] for region in REGIONS]
    requested_city = next((city for city in CITIES if city["id"] == str(selected_region).lower()), None)
    requested_region_id = requested_city["zone"] if requested_city else _region_id(selected_region)
    selected_ids = (
        [requested_region_id]
        if requested_region_id in available_region_ids
        else available_region_ids
    )
    displayed = {region_id: live_metrics[region_id] for region_id in available_region_ids}

    selected_metrics = [city_metrics[requested_city["id"]]] if requested_city else [displayed[region_id] for region_id in selected_ids]
    inventory = Counter()
    for metrics in selected_metrics:
        inventory.update(metrics["inventory"])

    forecast_live = False
    forecast: list[dict[str, Any]] = []
    live_forecast_demand_by_group: Counter = Counter()
    # The all-network landing view must stay within the API Gateway timeout.
    # A selected city still gets its single regional forecast query.
    forecast_targets = [(requested_city["zone"], requested_city["name"])] if requested_city else []
    for region_id, region_name in forecast_targets:
        points, demand_by_group = _forecast_projection(forecast_repository, region_name)
        if points:
            forecast_live = True
            forecast.extend(points)
            live_forecast_demand_by_group.update(demand_by_group)
    forecast_by_day: defaultdict[str, dict[str, float]] = defaultdict(lambda: {"demand": 0.0, "supply": 0.0})
    for point in forecast:
        forecast_by_day[point["date"]]["demand"] += point["demand"]
        forecast_by_day[point["date"]]["supply"] += point["supply"]
    forecast = [
        {"date": day, "demand": round(values["demand"], 1), "supply": round(values["supply"], 1)}
        for day, values in sorted(forecast_by_day.items())
    ][:7]

    expected_by_group = Counter()
    active_requests_by_group = Counter()
    active_request_ids = {
        case.request_id
        for case in snapshot["cases"].values()
        if str(getattr(case.outcome, "value", case.outcome)) not in {"fulfilled", "cancelled", "unfulfilled"}
        and str(snapshot["requests"].get(case.request_id, {}).get("status", "")).lower()
        not in {"fulfilled", "cancelled", "unfulfilled"}
    }
    for request_id, request in snapshot["requests"].items():
        if _request_region(request) not in selected_ids:
            continue
        if requested_city and str(request.get("city", "")).strip().lower() != requested_city["name"].lower():
            continue
        if snapshot["cases"] and request_id not in active_request_ids:
            continue
        active_requests_by_group[str(request.get("group", ""))] += max(int(request.get("qty", 0)), 0)
    for group in BLOOD_GROUPS:
        expected_by_group[group] = max(
            active_requests_by_group[group],
            round(live_forecast_demand_by_group[group]),
        )
    availability = []
    for group in BLOOD_GROUPS:
        available = inventory[group]
        required = expected_by_group[group]
        if available == 0 and required == 0:
            continue
        percent = 100 if required == 0 else min(round(available / required * 100), 100)
        availability.append({
            "type": group,
            "available_units": available,
            "required_units": required,
            "availability_percent": percent,
            "status": _status(percent),
        })

    region_rows = []
    for region in (REGION_BY_ID[region_id] for region_id in available_region_ids):
        metrics = displayed[region["id"]]
        region_units = sum(metrics["inventory"].values())
        # Map risk is driven by the same open-case shortfall signal used in the
        # KPI projection.  The percentage is a presentation of that risk band,
        # not an invented absolute inventory threshold.
        if region["id"] in live_region_ids and region_units == 0:
            percent = 0
        elif metrics["critical"] >= 3:
            percent = 16
        elif metrics["critical"] == 2:
            percent = 34
        elif metrics["critical"] == 1:
            percent = 58
        else:
            percent = 82
        region_rows.append({
            **region,
            "status": _status(percent),
            "availability_percent": percent,
            "units": region_units,
            "banks": metrics["banks"],
            "hospitals": metrics["hospitals"],
            "active_requests": metrics["requests"],
            "eligible_donors": metrics["donors"],
            "critical_shortages": metrics["critical"],
            "fulfilled": metrics["fulfilled"],
            "data_mode": "live" if _has_live_data(metrics) else "unavailable",
        })

    city_zone_max = {
        zone: max(1, max(sum(city_metrics[city["id"]]["inventory"].values()) for city in CITIES if city["zone"] == zone))
        for zone in {city["zone"] for city in CITIES}
    }
    city_rows = []
    for city in CITIES:
        metrics = city_metrics[city["id"]]
        city_units = sum(metrics["inventory"].values())
        percent = round(35 + 65 * city_units / city_zone_max[city["zone"]]) if city_units else 0
        if metrics["critical"] >= 3:
            percent = min(percent, 16)
        elif metrics["critical"] == 2:
            percent = min(percent, 34)
        elif metrics["critical"] == 1:
            percent = min(percent, 58)
        city_rows.append({
            **city,
            "state": f"{REGION_BY_ID[city['zone']]['name']} zone",
            "status": _status(percent),
            "availability_percent": percent,
            "units": city_units,
            "banks": metrics["banks"],
            "hospitals": metrics["hospitals"],
            "active_requests": metrics["requests"],
            "eligible_donors": metrics["donors"],
            "critical_shortages": metrics["critical"],
            "fulfilled": metrics["fulfilled"],
            "data_mode": "live" if _has_live_data(metrics) else "unavailable",
        })

    critical_groups = [item for item in availability if item["status"] in {"LOW", "CRITICAL"}]
    constrained = min(availability, key=lambda item: item["availability_percent"]) if availability else None
    peak = max(forecast, key=lambda item: item["demand"]) if forecast else None
    selection_name = requested_city["name"] if requested_city else ("All India network" if len(selected_ids) > 1 else REGION_BY_ID[selected_ids[0]]["name"])
    city_has_live_data = bool(requested_city and _has_live_data(city_metrics[requested_city["id"]]))
    any_metrics_live = city_has_live_data if requested_city else bool(set(selected_ids) & live_region_ids)
    data_mode = "live" if any_metrics_live or forecast_live else "unavailable"
    data_note = "Live aggregates from BloodNet's operational data services." if any_metrics_live or forecast_live else "No live operational data is available for this selection."

    activity = _activity(snapshot, selected_ids, requested_city["name"] if requested_city else None)

    return {
        "selected_region": requested_city["id"] if requested_city else (selected_ids[0] if len(selected_ids) == 1 else "all"),
        "selected_region_name": selection_name,
        "data_mode": data_mode,
        "data_note": data_note,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regions": region_rows,
        "cities": city_rows,
        "overview": {
            "total_units": sum(inventory.values()),
            "hospitals": sum(metrics["hospitals"] for metrics in selected_metrics),
            "blood_banks": sum(metrics["banks"] for metrics in selected_metrics),
            "eligible_donors": sum(metrics["donors"] for metrics in selected_metrics),
            "active_requests": sum(metrics["requests"] for metrics in selected_metrics),
            "critical_shortages": sum(metrics["critical"] for metrics in selected_metrics),
            "fulfilled": sum(metrics["fulfilled"] for metrics in selected_metrics),
        },
        "availability": availability,
        "forecast": forecast,
        "alerts": ([{
                "severity": "critical" if constrained["status"] == "CRITICAL" else "warning",
                "title": f"{constrained['type']} supply risk",
                "message": f"{constrained['type']} availability is {constrained['availability_percent']}% of projected requirement in {selection_name}.",
            }] if constrained else []) + ([{
                "severity": "warning",
                "title": "Peak demand window",
                "message": f"Demand is forecast to peak at {round(peak['demand'])} units on {peak['date']}.",
            }] if peak else []) + [{
                "severity": "info",
                "title": "Plans ready for review",
                "message": f"{sum(metrics['critical'] for metrics in selected_metrics)} high-risk supply signals are ready for regional teams to review.",
            },
        ],
        "activity": activity,
        "insight": {
            "blood_group": constrained["type"] if constrained else "—",
            "risk": constrained["status"] if constrained else "GOOD",
            "message": f"{constrained['type']} is the most constrained blood group across {selection_name}." if constrained else "No live supply constraint can be calculated for this selection.",
        },
        "impact": {
            "requests_coordinated": sum(metrics["requests"] + metrics["fulfilled"] for metrics in selected_metrics),
            "units_available": sum(inventory.values()),
            "requests_fulfilled": sum(metrics["fulfilled"] for metrics in selected_metrics),
            "shortages_detected": len(critical_groups),
        },
    }


def public_demo_scenarios(region: str = "all") -> list[dict[str, Any]]:
    city = next((item for item in CITIES if item["id"] == str(region).lower()), None)
    if city:
        name = city["name"]
    elif str(region).lower() == "all":
        name = "All India network"
    else:
        region_id = _region_id(region)
        name = REGION_BY_ID[region_id]["name"] if region_id else "All India network"
    return [
        {"id": "emergency-o-negative", "title": "Emergency shortage", "region": name, "blood_group": "O-", "units": 4, "urgency": "Critical"},
        {"id": "cross-region-ab-negative", "title": "Cross-region supply", "region": name, "blood_group": "AB-", "units": 3, "urgency": "High"},
        {"id": "inventory-first", "title": "Inventory-first fulfillment", "region": name, "blood_group": "A+", "units": 2, "urgency": "Routine"},
    ]


def run_public_demo_scenario(scenario_id: str, region: str = "all") -> dict[str, Any]:
    scenarios = {scenario["id"]: scenario for scenario in public_demo_scenarios(region)}
    if scenario_id not in scenarios:
        raise KeyError(scenario_id)
    scenario = scenarios[scenario_id]
    inventory_units = scenario["units"] if scenario_id == "inventory-first" else max(scenario["units"] - 1, 1)
    donor_units = scenario["units"] - inventory_units
    return {
        "scenario": scenario,
        "simulation": True,
        "steps": [
            {"title": "Request received", "detail": f"{scenario['urgency']} {scenario['blood_group']} request received for {scenario['units']} units."},
            {"title": "Inventory searched", "detail": f"Existing inventory can safely cover {inventory_units} units."},
            {"title": "Compatibility evaluated", "detail": "BloodNet applied deterministic component and blood-compatibility rules."},
            {"title": "Donors matched", "detail": "Eligible donors were ranked using the registered information needed for matching." if donor_units else "Inventory covers the request, so no donors need to be contacted."},
            {"title": "Plan recommended", "detail": f"{inventory_units} inventory units + {donor_units} donor mobilizations."},
            {"title": "Plan prepared", "detail": "The sample fulfillment route is ready for the next step."},
        ],
        "outcome": {
            "secured_units": scenario["units"],
            "inventory_units": inventory_units,
            "donor_units": donor_units,
            "estimated_minutes": 38 if donor_units == 0 else 67,
            "confidence": "High",
        },
    }

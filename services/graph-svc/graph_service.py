"""Deterministic in-memory network graph analyses for the local/demo runtime."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class SupplyEdge:
    bank_id: str
    hospital_id: str
    volume_30d: float = 0.0
    avg_lead_time_hours: float = 0.0
    distance_km: float = 0.0
    inventory_units: int = 0
    blood_group: str = ""
    component: str = ""
    region: str = ""


@dataclass(frozen=True)
class TransferEdge:
    from_bank: str
    to_bank: str
    avg_hours: float = 0.0
    distance_km: float = 0.0
    volume_30d: float = 0.0


@dataclass(frozen=True)
class DonorEdge:
    donor_id: str
    bank_id: str
    region: str
    group: str
    count: int = 1


class NetworkGraph:
    def __init__(
        self,
        supply_edges: Iterable[SupplyEdge] = (),
        transfer_edges: Iterable[TransferEdge] = (),
        donor_edges: Iterable[DonorEdge] = (),
        demand_by_region: dict[str, float] | None = None,
    ) -> None:
        self.supply_edges = list(supply_edges)
        self.transfer_edges = list(transfer_edges)
        self.donor_edges = list(donor_edges)
        self.demand_by_region = demand_by_region or {}

    def supplier_concentration(self, hospital_id: str) -> dict:
        volumes = [edge.volume_30d for edge in self.supply_edges if edge.hospital_id == hospital_id]
        total = sum(volumes)
        shares = [volume / total for volume in volumes] if total else []
        return {
            "hospital_id": hospital_id,
            "supplier_count": len(volumes),
            "hhi": round(sum(share * share for share in shares), 6),
            "single_supplier": len(volumes) == 1,
            "suppliers": [
                {"bank_id": edge.bank_id, "volume_30d": edge.volume_30d,
                 "share": round(edge.volume_30d / total, 6) if total else 0.0}
                for edge in self.supply_edges if edge.hospital_id == hospital_id
            ],
        }

    def coverage_zones(self) -> list[dict]:
        donors_by_region: dict[str, int] = defaultdict(int)
        for donor in self.donor_edges:
            donors_by_region[donor.region] += max(int(donor.count), 0)
        regions = set(donors_by_region) | set(self.demand_by_region)
        return [
            {
                "region": region,
                "donor_count": donors_by_region[region],
                "demand": self.demand_by_region.get(region, 0.0),
                "coverage_ratio": round(
                    donors_by_region[region] / self.demand_by_region[region], 6
                ) if self.demand_by_region.get(region, 0.0) > 0 else None,
            }
            for region in sorted(regions)
        ]

    def weak_coverage_zones(self) -> dict:
        zones = self.coverage_zones()
        weak = [
            zone for zone in zones
            if zone["coverage_ratio"] is not None and zone["coverage_ratio"] < 1.0
        ]
        return {
            "zones": sorted(weak, key=lambda zone: (zone["coverage_ratio"] or 1.0, zone["region"])),
            "at_risk_count": len(weak),
        }

    def load_bearing_donors(self) -> dict:
        catchments: dict[tuple[str, str, str], int] = defaultdict(int)
        for donor in self.donor_edges:
            catchments[(donor.bank_id, donor.region, donor.group)] += max(int(donor.count), 0)
        return {
            "donor_catchments": sorted(
                [
                    {"bank_id": bank_id, "region": region, "group": group, "donor_count": count}
                    for (bank_id, region, group), count in catchments.items()
                ],
                key=lambda item: (-item["donor_count"], item["bank_id"], item["group"]),
            )[:10]
        }

    def critical_supply_nodes(self) -> dict:
        """Rank banks by operational dependency, reach, and transfer connectivity."""
        hospitals_by_bank: dict[str, set[str]] = defaultdict(set)
        suppliers_by_hospital: dict[str, set[str]] = defaultdict(set)
        volume_by_bank: dict[str, float] = defaultdict(float)
        inventory_by_bank: dict[str, int] = defaultdict(int)
        for edge in self.supply_edges:
            hospitals_by_bank[edge.bank_id].add(edge.hospital_id)
            suppliers_by_hospital[edge.hospital_id].add(edge.bank_id)
            volume_by_bank[edge.bank_id] += max(edge.volume_30d, 0.0)
            inventory_by_bank[edge.bank_id] = max(inventory_by_bank[edge.bank_id], edge.inventory_units)
        transfer_degree: dict[str, int] = defaultdict(int)
        for edge in self.transfer_edges:
            transfer_degree[edge.from_bank] += 1
            transfer_degree[edge.to_bank] += 1
        banks = set(hospitals_by_bank) | set(transfer_degree)
        regions_by_bank: dict[str, set[str]] = defaultdict(set)
        for edge in self.supply_edges:
            if edge.region:
                regions_by_bank[edge.bank_id].add(edge.region)
        for edge in self.donor_edges:
            if edge.region:
                regions_by_bank[edge.bank_id].add(edge.region)
        ranked = []
        for bank_id in banks:
            dependent = sorted(
                hospital_id
                for hospital_id in hospitals_by_bank[bank_id]
                if len(suppliers_by_hospital[hospital_id]) == 1
            )
            score = (
                len(dependent) * 4.0
                + len(hospitals_by_bank[bank_id]) * 1.5
                + transfer_degree[bank_id] * 0.75
                + min(volume_by_bank[bank_id] / 10.0, 5.0)
            )
            ranked.append({
                "bank_id": bank_id,
                "region": ", ".join(sorted(regions_by_bank[bank_id])) or "Unknown region",
                "importance_score": round(score, 2),
                "served_hospitals": len(hospitals_by_bank[bank_id]),
                "dependent_hospitals": dependent,
                "transfer_connections": transfer_degree[bank_id],
                "volume_30d": round(volume_by_bank[bank_id], 2),
                "available_units": inventory_by_bank[bank_id],
            })
        return {"critical_nodes": sorted(ranked, key=lambda item: (-item["importance_score"], item["bank_id"]))}

    def bank_failure_impact(
        self,
        bank_id: str,
        *,
        blood_group: str = "",
        component: str = "",
        max_hours: float = 4.0,
    ) -> dict:
        def matches(edge: SupplyEdge) -> bool:
            return (
                (not blood_group or edge.blood_group == blood_group)
                and (not component or edge.component == component)
            )

        affected = sorted({edge.hospital_id for edge in self.supply_edges if edge.bank_id == bank_id and matches(edge)})
        alternatives: dict[str, list[dict]] = {}
        vulnerable: list[str] = []
        for hospital_id in affected:
            candidates = [
                edge for edge in self.supply_edges
                if edge.hospital_id == hospital_id
                and edge.bank_id != bank_id
                and matches(edge)
                and edge.inventory_units > 0
                and edge.avg_lead_time_hours <= max_hours
            ]
            alternatives[hospital_id] = [
                {
                    "bank_id": edge.bank_id,
                    "available_units": edge.inventory_units,
                    "travel_time_hours": round(edge.avg_lead_time_hours, 2),
                }
                for edge in sorted(candidates, key=lambda item: (item.avg_lead_time_hours, -item.inventory_units))
            ]
            if not candidates:
                vulnerable.append(hospital_id)
        return {
            "unavailable_bank_id": bank_id,
            "blood_group": blood_group or None,
            "component": component or None,
            "affected_hospitals": affected,
            "vulnerable_hospitals": vulnerable,
            "alternatives": alternatives,
            "lead_time_budget_hours": max_hours,
        }

    def redistribution_opportunities(self, reserve_units: int = 2) -> dict:
        """Suggest approval-gated balancing moves from surplus to exposed nodes."""
        inventory: dict[tuple[str, str, str], int] = defaultdict(int)
        demand: dict[tuple[str, str, str], float] = defaultdict(float)
        for edge in self.supply_edges:
            key = (edge.bank_id, edge.blood_group, edge.component)
            inventory[key] = max(inventory[key], edge.inventory_units)
            demand[key] += max(edge.volume_30d, 0.0)
        deficits = []
        surpluses = []
        for key in set(inventory) | set(demand):
            available = inventory[key]
            expected = demand[key]
            balance = available - expected - reserve_units
            item = {"bank_id": key[0], "blood_group": key[1], "component": key[2], "units": abs(int(round(balance)))}
            if balance < 0:
                deficits.append(item)
            elif balance >= 1:
                surpluses.append(item)
        routes = {(edge.from_bank, edge.to_bank): edge for edge in self.transfer_edges}
        opportunities = []
        for deficit in sorted(deficits, key=lambda item: -item["units"]):
            candidates = [
                source for source in surpluses
                if source["blood_group"] == deficit["blood_group"]
                and source["component"] == deficit["component"]
                and source["bank_id"] != deficit["bank_id"]
            ]
            if not candidates:
                continue
            source = max(candidates, key=lambda item: item["units"])
            route = routes.get((source["bank_id"], deficit["bank_id"])) or routes.get((deficit["bank_id"], source["bank_id"]))
            units = min(source["units"], deficit["units"])
            if units <= 0:
                continue
            opportunities.append({
                "from_bank": source["bank_id"],
                "to_bank": deficit["bank_id"],
                "blood_group": deficit["blood_group"],
                "component": deficit["component"],
                "suggested_units": units,
                "travel_time_hours": round(route.avg_hours, 2) if route else None,
                "reason": "Projected demand exceeds available stock plus the configured reserve.",
                "requires_approval": True,
            })
            source["units"] -= units
        return {"opportunities": opportunities[:10], "reserve_units": reserve_units}

    def topology(self) -> dict:
        nodes: dict[tuple[str, str], dict] = {}
        edges: list[dict] = []
        for edge in self.supply_edges:
            nodes[("bank", edge.bank_id)] = {"id": edge.bank_id, "type": "bank", "label": edge.bank_id}
            nodes[("hospital", edge.hospital_id)] = {"id": edge.hospital_id, "type": "hospital", "label": edge.hospital_id}
            edges.append({
                "source": edge.bank_id,
                "target": edge.hospital_id,
                "type": "supplies",
                "distance_km": round(edge.distance_km, 2),
                "travel_time_min": round(edge.avg_lead_time_hours * 60.0, 1),
                "inventory_units": edge.inventory_units,
                "volume_30d": round(edge.volume_30d, 2),
                "blood_group": edge.blood_group or None,
                "component": edge.component or None,
            })
        for edge in self.transfer_edges:
            nodes[("bank", edge.from_bank)] = {"id": edge.from_bank, "type": "bank", "label": edge.from_bank}
            nodes[("bank", edge.to_bank)] = {"id": edge.to_bank, "type": "bank", "label": edge.to_bank}
            edges.append({"source": edge.from_bank, "target": edge.to_bank, "type": "transfer", "travel_time_min": round(edge.avg_hours * 60.0, 1), "distance_km": round(edge.distance_km, 2)})
        return {"nodes": list(nodes.values()), "edges": edges}

    def network_briefing(self) -> dict:
        critical = self.critical_supply_nodes()["critical_nodes"]
        vulnerabilities = [self.bank_failure_impact(item["bank_id"]) for item in critical[:3]]
        redistribution = self.redistribution_opportunities()["opportunities"]
        return {
            "summary": {
                "banks": len({edge.bank_id for edge in self.supply_edges}),
                "hospitals": len({edge.hospital_id for edge in self.supply_edges}),
                "relationships": len(self.supply_edges) + len(self.transfer_edges),
                "vulnerable_hospitals": len({hospital for item in vulnerabilities for hospital in item["vulnerable_hospitals"]}),
                "redistribution_opportunities": len(redistribution),
            },
            "critical_nodes": critical[:5],
            "vulnerabilities": vulnerabilities,
            "redistribution_opportunities": redistribution,
            "topology": self.topology(),
        }

    def camp_placement(self, demand_by_region: dict[str, float] | None = None) -> dict:
        region_demand = demand_by_region or self.demand_by_region
        donors_by_region: dict[str, int] = defaultdict(int)
        for donor in self.donor_edges:
            donors_by_region[donor.region] += 1
        scored_regions = []
        for region in sorted(set(region_demand) | set(donors_by_region)):
            demand = float(region_demand.get(region, 0.0))
            donors = donors_by_region.get(region, 0)
            score = (demand / max(donors, 1)) if demand > 0 else 0.0
            scored_regions.append({"region": region, "demand": demand, "donor_count": donors, "coverage_score": score})
        selected = max(scored_regions, key=lambda item: item["coverage_score"], default={"region": None, "coverage_score": 0.0})
        return {
            "selected_region": selected["region"],
            "coverage_score": selected["coverage_score"],
            "candidate_regions": sorted(scored_regions, key=lambda item: (-item["coverage_score"], item["region"])),
        }

    def cascade_reachability(self, unavailable_bank_id: str, max_hours: float) -> dict:
        adjacency: dict[str, list[TransferEdge]] = defaultdict(list)
        for edge in self.transfer_edges:
            if edge.from_bank != unavailable_bank_id and edge.to_bank != unavailable_bank_id:
                adjacency[edge.from_bank].append(edge)
        reachable: set[str] = set()
        for edge in self.supply_edges:
            if edge.bank_id == unavailable_bank_id:
                continue
            if edge.avg_lead_time_hours <= max_hours:
                reachable.add(edge.hospital_id)
        return {
            "removed_bank": unavailable_bank_id,
            "lead_time_budget_hours": max_hours,
            "reachable_hospitals": sorted(reachable),
            "unreachable_hospitals": sorted({edge.hospital_id for edge in self.supply_edges} - reachable),
            "remaining_transfer_edges": sum(len(edges) for edges in adjacency.values()),
        }

    def analyze(self, analysis_type: str, params: dict | None = None) -> dict:
        params = params or {}
        if analysis_type in {"supplier_concentration", "dependency_analysis"}:
            return self.supplier_concentration(str(params.get("hospital_id", params.get("hospital", ""))))
        if analysis_type in {"coverage_zones", "weak_coverage_zones"}:
            if analysis_type == "weak_coverage_zones":
                return self.weak_coverage_zones()
            return {"zones": self.coverage_zones()}
        if analysis_type in {"camp", "camp_placement"}:
            return self.camp_placement(params.get("demand_by_region") or self.demand_by_region)
        if analysis_type in {"cascade", "cascade_simulation"}:
            return self.cascade_reachability(
                str(params["unavailable_bank_id"]), float(params.get("max_hours", 4))
            )
        if analysis_type == "load_bearing_donors":
            return self.load_bearing_donors()
        if analysis_type in {"critical_supply_nodes", "critical_nodes"}:
            return self.critical_supply_nodes()
        if analysis_type in {"bank_failure_impact", "failure_impact"}:
            return self.bank_failure_impact(
                str(params["bank_id"]),
                blood_group=str(params.get("blood_group", "")),
                component=str(params.get("component", "")),
                max_hours=float(params.get("max_hours", 4)),
            )
        if analysis_type in {"redistribution", "redistribution_opportunities"}:
            return self.redistribution_opportunities(int(params.get("reserve_units", 2)))
        if analysis_type in {"network_briefing", "network_intelligence"}:
            return self.network_briefing()
        raise ValueError(f"Unsupported graph analysis '{analysis_type}'")

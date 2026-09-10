"""
BloodNet Phase 1 - End-to-End Test Suite
=========================================
Covers every implemented module:
  1.  contracts/models       - domain types round-trip
  2.  intake-svc/parser      - text->struct parsing
  3.  match-svc/compatibility - blood-group rules
  4.  match-svc/eligibility   - donor gate
  5.  match-svc/inventory_match - inventory branch
  6.  swarm-svc/poisson_binomial - probability math
  7.  agent-svc/tools         - stub tools smoke-test
  8.  sim/generator           - synthetic data generation
  9.  sim/engine              - digital-twin integration (short run)
  10. Integration flow        - parser to match to swarm pipeline
"""

import sys
import os
import math as _math
import random
import numpy as np
from datetime import datetime, timedelta

# -- path setup ---------------------------------------------------------------
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "contracts"))
sys.path.insert(0, os.path.join(ROOT, "services", "match-svc"))
sys.path.insert(0, os.path.join(ROOT, "services", "intake-svc"))
sys.path.insert(0, os.path.join(ROOT, "services", "swarm-svc"))
sys.path.insert(0, os.path.join(ROOT, "services", "agent-svc"))
sys.path.insert(0, os.path.join(ROOT, "sim"))

# -- imports ------------------------------------------------------------------
from contracts.models import (
    BloodGroup, Component, Urgency, InventoryStatus, GeoPoint,
    Donor, DeferralFlag, BloodBank, Hospital, InventoryUnit,
    Request, RequestStatus, VerificationState, InventoryMatch,
    Case, CaseOutcome, Outreach, OutreachResponse,
)
from parser import parse_intake_text
from compatibility import (
    rbc_acceptable_donors, plasma_acceptable_donors, acceptable_donor_groups,
)
from eligibility import check_eligibility, RULE_VERSION
from inventory_match import find_inventory_matches, haversine_km, InventoryMatchResult
from poisson_binomial import poisson_binomial_pmf, calculate_cohort_size
from tools import (
    AGENT_TOOLS, get_network_inventory, get_swarm_status,
    get_weather_forecast, propose_recommendation,
)
from generator import generate_banks, generate_donors, generate_hospitals
import simpy

NOW = datetime(2026, 8, 22, 12, 0, 0)
W = 62
passed = 0
failed = 0
failures = []


def section(title):
    print(f"\n{'='*W}\n  {title}\n{'='*W}")


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        tag = f" [{detail}]" if detail else ""
        print(f"  FAIL  {name}{tag}")
        failures.append(f"{name}{tag}")


# =============================================================================
# 1. CONTRACTS / DOMAIN MODELS
# =============================================================================
section("1. contracts/models - Domain Types")

check("BloodGroup has 8 members", len(list(BloodGroup)) == 8)
check("O_NEG value is 'O-'", BloodGroup.O_NEG == "O-")
check("AB_POS value is 'AB+'", BloodGroup.AB_POS == "AB+")
check("Component has 6 members", len(list(Component)) == 6)

gp = GeoPoint(lat=18.52, lng=73.85)
check("GeoPoint round-trips via JSON", GeoPoint(**gp.model_dump()) == gp)

donor_base = Donor(
    donor_id="D001",
    blood_group=BloodGroup.O_POS,
    geo=gp,
    last_donation_at=NOW - timedelta(days=100),
    consent_scopes=["contactable"],
)
check("Donor round-trips via model_dump", Donor(**donor_base.model_dump()) == donor_base)
check("Donor default deferral_flags is []", donor_base.deferral_flags == [])

req_base = Request(
    request_id="R001",
    group=BloodGroup.A_POS,
    component=Component.RBC,
    qty=3,
    hospital_id="H1",
    urgency=Urgency.CRITICAL,
    required_by=NOW + timedelta(hours=4),
    source_channel="whatsapp",
    verification_state=VerificationState.VERIFIED,
    status=RequestStatus.OPEN,
)
check("Request round-trips via model_dump", Request(**req_base.model_dump()) == req_base)

case = Case(
    case_id="C001",
    request_id="R001",
    inventory_matches=[InventoryMatch(bank_id="B1", units_available=3, distance_km=2.1, eta_min=4.2)],
    units_from_inventory=3,
    units_from_donors_remaining=0,
)
check("Case carries inventory_matches", len(case.inventory_matches) == 1)
check("Case default outcome is PENDING", case.outcome == CaseOutcome.PENDING)

# =============================================================================
# 2. INTAKE-SVC / PARSER
# =============================================================================
section("2. intake-svc/parser - Text Parsing")

r = parse_intake_text("Need 2 units of O positive blood urgent at Ruby Hall Clinic")
check("Parser: extracts blood group O+", r["blood_group"] == "O+|RBC")
check("Parser: extracts unit count 2", r["units"] == 2)
check("Parser: detects urgency=emergency", r["urgency"] == "emergency")
check("Parser: extracts hospital name", r["hospital"] is not None)
check("Parser: confidence > 0.5", r["confidence"] > 0.5)

r2 = parse_intake_text("Patient requires 3 bags A negative blood")
check("Parser: extracts A-", r2["blood_group"] == "A-|RBC")
check("Parser: extracts 3 units", r2["units"] == 3)
check("Parser: routine default when no urgency keyword", r2["urgency"] == "routine")

r3 = parse_intake_text("stat need 5 units b positive")
check("Parser: stat triggers emergency urgency", r3["urgency"] == "emergency")
check("Parser: extracts B+", r3["blood_group"] == "B+|RBC")
check("Parser: extracts 5 units", r3["units"] == 5)

r4 = parse_intake_text("random gibberish text without blood info")
check("Parser: no blood group on garbage input", r4["blood_group"] is None)
check("Parser: confidence=0 on garbage input", r4["confidence"] == 0.0)

# =============================================================================
# 3. MATCH-SVC / COMPATIBILITY
# =============================================================================
section("3. match-svc/compatibility - Blood-Group Rules")

check("O-NEG is universal RBC donor (all 8 groups)",
      all(BloodGroup.O_NEG in rbc_acceptable_donors(g) for g in BloodGroup))
check("AB+ is universal RBC recipient (all 8 donors)",
      rbc_acceptable_donors(BloodGroup.AB_POS) == set(BloodGroup))
check("O- only accepts O- for RBC",
      rbc_acceptable_donors(BloodGroup.O_NEG) == {BloodGroup.O_NEG})
check("A+ accepts O-, O+, A-, A+",
      rbc_acceptable_donors(BloodGroup.A_POS) == {
          BloodGroup.O_NEG, BloodGroup.O_POS, BloodGroup.A_NEG, BloodGroup.A_POS
      })

check("AB is universal plasma donor",
      all(BloodGroup.AB_POS in plasma_acceptable_donors(g) for g in BloodGroup))
check("O- is universal plasma recipient (all donors)",
      plasma_acceptable_donors(BloodGroup.O_NEG) == set(BloodGroup))
check("AB+ plasma recipient: only AB+ donor",
      plasma_acceptable_donors(BloodGroup.AB_POS) == {BloodGroup.AB_POS})

check("FFP routes to plasma matching",
      acceptable_donor_groups(BloodGroup.A_POS, Component.FFP) == plasma_acceptable_donors(BloodGroup.A_POS))
check("RBC routes to rbc matching",
      acceptable_donor_groups(BloodGroup.A_POS, Component.RBC) == rbc_acceptable_donors(BloodGroup.A_POS))
check("PLATELETS_RDP uses RBC-style",
      acceptable_donor_groups(BloodGroup.B_POS, Component.PLATELETS_RDP) == rbc_acceptable_donors(BloodGroup.B_POS))
check("CRYOPRECIPITATE uses plasma-style",
      acceptable_donor_groups(BloodGroup.B_POS, Component.CRYOPRECIPITATE) == plasma_acceptable_donors(BloodGroup.B_POS))

# =============================================================================
# 4. MATCH-SVC / ELIGIBILITY
# =============================================================================
section("4. match-svc/eligibility - Donor Gate")


def make_donor(**overrides):
    defaults = dict(
        donor_id="d1",
        blood_group=BloodGroup.O_POS,
        geo=GeoPoint(lat=18.52, lng=73.85),
        last_donation_at=NOW - timedelta(days=200),
        deferral_flags=[],
        consent_scopes=["contactable"],
    )
    defaults.update(overrides)
    return Donor(**defaults)


r_v = check_eligibility(make_donor(), Component.RBC, age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW)
check("Eligibility result carries rule_version", bool(r_v.rule_version))
check("Healthy donor is eligible", r_v.eligible)

r_under = check_eligibility(make_donor(), Component.RBC, age_years=17, weight_kg=65, hb_g_dl=13.5, as_of=NOW)
check("Age 17 rejected", not r_under.eligible and any("age" in x for x in r_under.reasons))
r_over = check_eligibility(make_donor(), Component.RBC, age_years=66, weight_kg=65, hb_g_dl=13.5, as_of=NOW)
check("Age 66 rejected", not r_over.eligible)
r_18 = check_eligibility(make_donor(), Component.RBC, age_years=18, weight_kg=65, hb_g_dl=13.5, as_of=NOW)
check("Age 18 accepted (boundary)", r_18.eligible)
r_65 = check_eligibility(make_donor(), Component.RBC, age_years=65, weight_kg=65, hb_g_dl=13.5, as_of=NOW)
check("Age 65 accepted (boundary)", r_65.eligible)

r_w = check_eligibility(make_donor(), Component.RBC, age_years=30, weight_kg=49.9, hb_g_dl=13.5, as_of=NOW)
check("Weight 49.9 kg rejected", not r_w.eligible and any("weight" in x for x in r_w.reasons))

r_hb = check_eligibility(make_donor(), Component.RBC, age_years=30, weight_kg=65, hb_g_dl=12.4, as_of=NOW)
check("Hb 12.4 g/dL rejected", not r_hb.eligible and any("hemoglobin" in x for x in r_hb.reasons))

r_rbc_soon = check_eligibility(
    make_donor(last_donation_at=NOW - timedelta(days=89)), Component.RBC,
    age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW)
check("RBC interval: 89 days rejected (needs 90)", not r_rbc_soon.eligible)

r_rbc_ok = check_eligibility(
    make_donor(last_donation_at=NOW - timedelta(days=90)), Component.RBC,
    age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW)
check("RBC interval: 90 days accepted", r_rbc_ok.eligible)

r_plt_ok = check_eligibility(
    make_donor(last_donation_at=NOW - timedelta(days=14)), Component.PLATELETS_RDP,
    age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW)
check("Platelets interval: 14 days accepted", r_plt_ok.eligible)

d_perm = make_donor(deferral_flags=[DeferralFlag(reason="HIV risk", deferred_until=None)])
check("Permanent deferral rejected",
      not check_eligibility(d_perm, Component.RBC, age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW).eligible)

d_tmp_active = make_donor(deferral_flags=[
    DeferralFlag(reason="travel", deferred_until=(NOW + timedelta(days=5)).date())
])
check("Active temp deferral rejected",
      not check_eligibility(d_tmp_active, Component.RBC, age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW).eligible)

d_tmp_expired = make_donor(deferral_flags=[
    DeferralFlag(reason="travel", deferred_until=(NOW - timedelta(days=1)).date())
])
check("Expired temp deferral accepted",
      check_eligibility(d_tmp_expired, Component.RBC, age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW).eligible)

d_no_consent = make_donor(consent_scopes=[])
check("No contactable consent rejected",
      not check_eligibility(d_no_consent, Component.RBC, age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW).eligible)

d_multi = make_donor(
    deferral_flags=[DeferralFlag(reason="x", deferred_until=None)],
    consent_scopes=[],
)
r_multi = check_eligibility(d_multi, Component.RBC, age_years=17, weight_kg=40, hb_g_dl=10.0, as_of=NOW)
check("Multiple failures accumulate (>=4 reasons)", len(r_multi.reasons) >= 4)

r1 = check_eligibility(make_donor(), Component.RBC, age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW)
r2 = check_eligibility(make_donor(), Component.RBC, age_years=30, weight_kg=65, hb_g_dl=13.5, as_of=NOW)
check("Eligibility is deterministic", r1 == r2)

# =============================================================================
# 5. MATCH-SVC / INVENTORY MATCH
# =============================================================================
section("5. match-svc/inventory_match - Inventory Branch")

HOSPITAL_GEO = GeoPoint(lat=18.5204, lng=73.8567)
HOSPITAL = Hospital(hospital_id="H1", name="Ruby Hall", geo=HOSPITAL_GEO, tier="tier1")


def make_request(**overrides):
    defaults = dict(
        request_id="R1", group=BloodGroup.A_POS, component=Component.RBC,
        qty=5, hospital_id="H1", urgency=Urgency.CRITICAL,
        required_by=NOW + timedelta(hours=4),
        source_channel="whatsapp",
        verification_state=VerificationState.VERIFIED,
        status=RequestStatus.OPEN,
    )
    defaults.update(overrides)
    return Request(**defaults)


def make_bank(bank_id, lat, lng):
    return BloodBank(bank_id=bank_id, name=bank_id, geo=GeoPoint(lat=lat, lng=lng), licence_id="L1")


def make_unit(uid, bank_id, group, component=Component.RBC, days_to_expiry=10, status=InventoryStatus.AVAILABLE):
    return InventoryUnit(
        unit_id=uid, bank_id=bank_id, group=group, component=component,
        collected_at=NOW - timedelta(days=5),
        expires_at=NOW + timedelta(days=days_to_expiry),
        status=status,
    )


h0 = haversine_km(GeoPoint(lat=18.5204, lng=73.8567), GeoPoint(lat=18.5204, lng=73.8567))
check("Haversine: same point = 0 km", abs(h0) < 1e-6)
h1 = haversine_km(GeoPoint(lat=0, lng=0), GeoPoint(lat=0, lng=1))
check("Haversine: 1 degree longitude approx 111 km at equator", 109 < h1 < 113)

near = make_bank("NEAR", 18.53, 73.86)
far = make_bank("FAR", 19.0, 74.5)
units_nf = [make_unit("U1", "FAR", BloodGroup.A_POS), make_unit("U2", "NEAR", BloodGroup.A_POS)]
res_nf = find_inventory_matches(make_request(), HOSPITAL, [near, far], units_nf)
check("Nearest bank comes first", [m.bank_id for m in res_nf.matches] == ["NEAR", "FAR"])
check("Distance ascending", res_nf.matches[0].distance_km < res_nf.matches[1].distance_km)

bank = make_bank("B1", 18.53, 73.86)
units3 = [make_unit(f"U{i}", "B1", BloodGroup.A_POS) for i in range(3)]
res3 = find_inventory_matches(make_request(qty=5), HOSPITAL, [bank], units3)
check("Partial fill: units_from_inventory=3", res3.units_from_inventory == 3)
check("Partial fill: units_remaining=2", res3.units_remaining == 2)

units8 = [make_unit(f"U{i}", "B1", BloodGroup.A_POS) for i in range(8)]
res8 = find_inventory_matches(make_request(qty=5), HOSPITAL, [bank], units8)
check("Full fill: units_remaining=0", res8.units_remaining == 0)
check("Full fill: units_from_inventory=5", res8.units_from_inventory == 5)

bad_unit = make_unit("U1", "B1", BloodGroup.B_POS)
res_bad = find_inventory_matches(make_request(group=BloodGroup.A_POS), HOSPITAL, [bank], [bad_unit])
check("Incompatible group excluded", res_bad.units_from_inventory == 0)

o_neg_unit = make_unit("U1", "B1", BloodGroup.O_NEG)
res_oneg = find_inventory_matches(make_request(group=BloodGroup.AB_POS), HOSPITAL, [bank], [o_neg_unit])
check("O- unit valid for AB+ request", res_oneg.units_from_inventory == 1)

reserved = make_unit("U1", "B1", BloodGroup.A_POS, status=InventoryStatus.RESERVED)
issued = make_unit("U2", "B1", BloodGroup.A_POS, status=InventoryStatus.ISSUED)
res_st = find_inventory_matches(make_request(), HOSPITAL, [bank], [reserved, issued])
check("Reserved/issued units excluded", res_st.units_from_inventory == 0)

exp_unit = make_unit("U1", "B1", BloodGroup.A_POS, days_to_expiry=0)
res_exp = find_inventory_matches(make_request(), HOSPITAL, [bank], [exp_unit])
check("Expiring unit excluded by cold-chain buffer", res_exp.units_from_inventory == 0)

plt_unit = make_unit("U1", "B1", BloodGroup.A_POS, component=Component.PLATELETS_RDP)
res_comp = find_inventory_matches(make_request(component=Component.RBC), HOSPITAL, [bank], [plt_unit])
check("Wrong component excluded", res_comp.units_from_inventory == 0)

res_empty = find_inventory_matches(make_request(), HOSPITAL, [], [])
check("Empty banks: empty matches", res_empty.matches == [])
check("Empty banks: units_remaining = qty", res_empty.units_remaining == 5)

# =============================================================================
# 6. SWARM-SVC / POISSON BINOMIAL
# =============================================================================
section("6. swarm-svc/poisson_binomial - Probability Math")

pmf_empty = poisson_binomial_pmf([])
check("PMF([]) = [1.0]", np.allclose(pmf_empty, [1.0]))

pmf_one = poisson_binomial_pmf([1.0])
check("PMF([1.0])[1] = 1.0", np.isclose(pmf_one[1], 1.0, atol=1e-6))

pmf_zero = poisson_binomial_pmf([0.0])
check("PMF([0.0])[0] = 1.0", np.isclose(pmf_zero[0], 1.0, atol=1e-6))

pmf_n = poisson_binomial_pmf([0.3, 0.5, 0.7, 0.9])
check("PMF sums to 1.0", np.isclose(pmf_n.sum(), 1.0, atol=1e-6))

n, p = 5, 0.6
pmf_bin = poisson_binomial_pmf([p] * n)
binom_3 = _math.comb(n, 3) * (p ** 3) * ((1 - p) ** 2)
check("PMF approx Binomial when all probs equal", np.isclose(pmf_bin[3], binom_3, atol=1e-4))

pmf_allz = poisson_binomial_pmf([0.0] * 5)
check("PMF all-zero probs: P(0)=1", np.isclose(pmf_allz[0], 1.0, atol=1e-6))
check("PMF values all >= 0", all(x >= 0 for x in pmf_n))

probs_sorted = [0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.55, 0.5, 0.45]
cs = calculate_cohort_size(probs_sorted, target_units=3, confidence=0.95)
check("Cohort size is positive int", isinstance(cs, int) and cs > 0)
check("Cohort size <= total donors", cs <= len(probs_sorted))

pmf_cohort = poisson_binomial_pmf(probs_sorted[:cs])
p_success = pmf_cohort[3:].sum()
check("Cohort achieves >=0.95 confidence", p_success >= 0.95)

cs_empty = calculate_cohort_size([], target_units=3, confidence=0.95)
check("Cohort of empty pool = 0", cs_empty == 0)

cs_one = calculate_cohort_size([0.99], target_units=1, confidence=0.95)
check("Single 0.99-prob donor satisfies target=1", cs_one == 1)

# =============================================================================
# 7. AGENT-SVC / TOOLS
# =============================================================================
section("7. agent-svc/tools - Stub Tool Smoke Tests")

inv = get_network_inventory()
check("get_network_inventory returns status=success", inv["status"] == "success")
check("Inventory data has O+|RBC key", "O+|RBC" in inv["data"])
check("Inventory O+|RBC count is int", isinstance(inv["data"]["O+|RBC"], int))

sw = get_swarm_status("SW-001")
check("get_swarm_status returns status=success", sw["status"] == "success")
check("Swarm status has confirmed_donors", "confirmed_donors" in sw["data"])

wf = get_weather_forecast("pune")
check("get_weather_forecast returns status=success", wf["status"] == "success")
check("Forecast has expected_shortfall", "expected_shortfall" in wf["data"])

pr = propose_recommendation("PREEMPTIVE_SWARM", "Dengue spike: trigger platelet swarm")
check("propose_recommendation returns success", pr["status"] == "success")
check("Recommendation response has approval_id", "approval_id" in pr)

required_agent_tools = {
    "get_inventory_status",
    "get_demand_forecast",
    "get_case_shortfall",
    "get_donor_mobilization_options",
    "get_case_status",
}
check("AGENT_TOOLS includes MVP read tools", required_agent_tools <= AGENT_TOOLS.keys())
check("All AGENT_TOOLS are callable", all(callable(v) for v in AGENT_TOOLS.values()))

# =============================================================================
# 8. SIM / GENERATOR
# =============================================================================
section("8. sim/generator - Synthetic Data Generation")

banks_g = generate_banks(5)
check("generate_banks returns 5 banks", len(banks_g) == 5)
check("Banks have unique IDs", len({b.bank_id for b in banks_g}) == 5)
check("Banks have valid geo (Pune lat range)", all(18.45 <= b.geo.lat <= 18.60 for b in banks_g))
check("Banks have storage_capacity", all(b.storage_capacity for b in banks_g))

hospitals_g = generate_hospitals(18, bank_ids=[b.bank_id for b in banks_g])
check("generate_hospitals returns 18", len(hospitals_g) == 18)
check("Hospitals have tier set", all(h.tier in ("tier1", "tier2", "tier3") for h in hospitals_g))
check("H1 is tier1", hospitals_g[0].tier == "tier1")
check("H10 is tier2", hospitals_g[9].tier == "tier2")
check("H11 is tier3", hospitals_g[10].tier == "tier3")

donors_g = generate_donors(200)
check("generate_donors returns 200", len(donors_g) == 200)
check("Donors have blood_group", all(d.blood_group in BloodGroup for d in donors_g))
check("All donors are contactable", all("contactable" in d.consent_scopes for d in donors_g))
check("Donors have reliability_features", all(d.reliability_features for d in donors_g))

group_counts = {}
for d in donors_g:
    group_counts[d.blood_group] = group_counts.get(d.blood_group, 0) + 1
check("O+ most common, AB- least (approx India distribution)",
      group_counts.get(BloodGroup.O_POS, 0) > group_counts.get(BloodGroup.AB_NEG, 0))

# =============================================================================
# 9. SIM / ENGINE - Digital Twin
# =============================================================================
section("9. sim/engine - Digital Twin (1-day simulation)")

from engine import BloodNetSimulation

banks_sim = generate_banks(5)
donors_sim = generate_donors(50)

env = simpy.Environment()
sim = BloodNetSimulation(env, banks_sim, donors_sim)
env.process(sim.request_generator(arrival_rate_minutes=10))
env.run(until=60 * 24)   # 1 simulated day

check("Simulation ran without crashing", True)
check("At least 1 request generated", sim.stats["requests"] >= 1)
accounted = sim.stats["fulfilled_locally"] + sim.stats["fulfilled_network"] + sim.stats["escalated"]
check("All requests are accounted for in stats", accounted >= sim.stats["requests"])
print(f"     1-day sim stats: {sim.stats}")

# =============================================================================
# 10. INTEGRATION FLOW: Parser -> Match -> Swarm Cohort
# =============================================================================
section("10. Integration - Parser -> Inventory Match -> Cohort Sizing")

raw_text = "Need 3 units of A positive blood URGENT at Ruby Hall Hospital"
parsed = parse_intake_text(raw_text)

bg_str = parsed["blood_group"]         # "A+|RBC"
group_part = bg_str.split("|")[0]      # "A+"
bg_map = {g.value: g for g in BloodGroup}
blood_group = bg_map.get(group_part)
check("Integration: parsed blood group is valid enum", blood_group is not None)

int_req = Request(
    request_id="INT-001",
    group=blood_group,
    component=Component.RBC,
    qty=parsed["units"],
    hospital_id="H1",
    urgency=Urgency.CRITICAL if parsed["urgency"] == "emergency" else Urgency.ROUTINE,
    required_by=NOW + timedelta(hours=4),
    source_channel="whatsapp",
    verification_state=VerificationState.VERIFIED,
    status=RequestStatus.OPEN,
)
check("Integration: Request built from parsed text (qty=3)", int_req.qty == 3)
check("Integration: Urgency is CRITICAL (emergency input)", int_req.urgency == Urgency.CRITICAL)

int_banks = [
    make_bank("INT_BANK_A", 18.525, 73.857),
    make_bank("INT_BANK_B", 18.600, 74.000),
]
int_units = [make_unit(f"IU{i}", "INT_BANK_A", BloodGroup.A_POS) for i in range(2)]
int_match = find_inventory_matches(int_req, HOSPITAL, int_banks, int_units)
check("Integration: inventory match ran successfully", isinstance(int_match, InventoryMatchResult))
check("Integration: 2 units covered from inventory", int_match.units_from_inventory == 2)
check("Integration: 1 unit remaining for swarm", int_match.units_remaining == 1)

donor_probs = [0.85, 0.80, 0.75, 0.70, 0.65, 0.60]
cohort = calculate_cohort_size(donor_probs, target_units=int_match.units_remaining, confidence=0.95)
check("Integration: cohort size computed for shortfall", cohort > 0)
pmf_v = poisson_binomial_pmf(donor_probs[:cohort])
p_v = pmf_v[int_match.units_remaining:].sum()
check("Integration: cohort achieves >=95% confidence for shortfall", p_v >= 0.95)

print(f"\n     Flow: '{raw_text[:45]}...'")
print(f"     -> Blood group: {blood_group}, Units: {parsed['units']}, Urgency: {int_req.urgency}")
print(f"     -> Inventory covered: {int_match.units_from_inventory}, Shortfall: {int_match.units_remaining}")
print(f"     -> Donors to contact: {cohort} (P(success)={p_v:.3f})")

# =============================================================================
# SUMMARY
# =============================================================================
total_tests = passed + failed
print(f"\n{'='*W}")
print(f"  PHASE 1 E2E RESULTS")
print(f"{'='*W}")
print(f"  Total  : {total_tests}")
print(f"  Passed : {passed}")
print(f"  Failed : {failed}")

if failures:
    print(f"\n  Failed tests:")
    for f in failures:
        print(f"    FAIL {f}")

print(f"{'='*W}\n")
sys.exit(0 if failed == 0 else 1)

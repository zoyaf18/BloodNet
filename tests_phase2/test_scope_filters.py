from types import SimpleNamespace
import sys
from pathlib import Path

from contracts.auth import Identity
from contracts.models import Case, InventoryMatch

ROOT = Path(__file__).resolve().parents[1]
MATCH_DIR = ROOT / "services" / "match-svc"
sys.path.insert(0, str(MATCH_DIR))
import main as match_main
sys.path.remove(str(MATCH_DIR))


def test_regional_scope_resolves_only_matching_cases_and_banks(monkeypatch):
    monkeypatch.setattr(match_main, "_bank_ids_for_region", lambda region: {"BANK-PUNE"} if region == "Pune" else set())
    pune_case = Case(case_id="CASE-PUNE", request_id="REQ-PUNE")
    pune_case.inventory_matches = [InventoryMatch(bank_id="BANK-PUNE", units_available=1, distance_km=1, eta_min=1)]
    mumbai_case = Case(case_id="CASE-MUMBAI", request_id="REQ-MUMBAI")
    mumbai_case.inventory_matches = [InventoryMatch(bank_id="BANK-MUMBAI", units_available=1, distance_km=1, eta_min=1)]
    monkeypatch.setattr(
        match_main,
        "workflow_store",
        SimpleNamespace(
            approval_service=SimpleNamespace(cases={pune_case.case_id: pune_case, mumbai_case.case_id: mumbai_case}),
            requests={"REQ-PUNE": {"region": "Pune"}, "REQ-MUMBAI": {"region": "Mumbai"}},
        ),
    )

    identity = Identity("REGION-USER", "regional_admin", region_id="Pune")

    assert match_main._scoped_case_ids(identity) == {"CASE-PUNE"}
    assert match_main._scoped_bank_ids(identity) == {"BANK-PUNE"}


def test_unscoped_auditor_can_view_all_cases_but_scoped_auditor_cannot(monkeypatch):
    monkeypatch.setattr(match_main, "_bank_ids_for_region", lambda region: {"BANK-PUNE"} if region == "Pune" else set())
    pune_case = Case(case_id="CASE-PUNE", request_id="REQ-PUNE")
    pune_case.inventory_matches = [InventoryMatch(bank_id="BANK-PUNE", units_available=1, distance_km=1, eta_min=1)]
    mumbai_case = Case(case_id="CASE-MUMBAI", request_id="REQ-MUMBAI")
    mumbai_case.inventory_matches = [InventoryMatch(bank_id="BANK-MUMBAI", units_available=1, distance_km=1, eta_min=1)]
    monkeypatch.setattr(
        match_main,
        "workflow_store",
        SimpleNamespace(
            approval_service=SimpleNamespace(cases={pune_case.case_id: pune_case, mumbai_case.case_id: mumbai_case}),
            requests={"REQ-PUNE": {"region": "Pune"}, "REQ-MUMBAI": {"region": "Mumbai"}},
        ),
    )

    assert match_main._scoped_case_ids(Identity("AUDITOR", "auditor")) == {"CASE-PUNE", "CASE-MUMBAI"}
    scoped = Identity("AUDITOR-PUNE", "auditor", region_id="Pune")
    assert match_main._scoped_case_ids(scoped) == {"CASE-PUNE"}
    assert match_main._scoped_bank_ids(scoped) == {"BANK-PUNE"}

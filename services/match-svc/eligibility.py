"""
Donor eligibility — deterministic, versioned, auditable rule engine (SPEC C-03).

NEVER model-inferred. The Donor Success Model (S3) only ever scores donors that
have already passed this gate (SPEC §7.1 guardrail: "Eligibility gate runs
*before* scoring; ineligible donors are never scored.").

Age/weight/Hb are intentionally NOT stored on the shared `Donor` contract
(contracts/models.py) — SPEC §3.1 keeps that entity to matching-relevant
fields only. They're passed in here as the outputs of a separate health-
screening record, keeping this module a pure function of its inputs.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from contracts.models import Component, Donor

RULE_VERSION = "eligibility-v1.0"

MIN_AGE_YEARS = 18
MAX_AGE_YEARS = 65
MIN_WEIGHT_KG = 50.0
MIN_HB_G_DL = 12.5

# Minimum days since last donation, per component being requested now.
# (Whole blood / RBC: 90d. Platelets: 14d, up to a higher annual cap not
# modeled here yet. Plasma-rich components: 28d.)
MIN_INTERVAL_DAYS: dict[Component, int] = {
    Component.WHOLE_BLOOD: 90,
    Component.RBC: 90,
    Component.PLATELETS_RDP: 14,
    Component.PLATELETS_SDP: 14,
    Component.FFP: 28,
    Component.CRYOPRECIPITATE: 28,
}


class EligibilityResult(BaseModel):
    eligible: bool
    reasons: list[str]
    rule_version: str = RULE_VERSION


class EligibilityScreening(BaseModel):
    """Trusted health-screening snapshot supplied to the match boundary."""

    age_years: int
    weight_kg: float
    hb_g_dl: float


def screen_donor(
    donor: Donor,
    component_requested: Component,
    screening: EligibilityScreening,
    *,
    as_of: datetime,
) -> EligibilityResult:
    return check_eligibility(
        donor,
        component_requested,
        age_years=screening.age_years,
        weight_kg=screening.weight_kg,
        hb_g_dl=screening.hb_g_dl,
        as_of=as_of,
    )


def check_eligibility(
    donor: Donor,
    component_requested: Component,
    *,
    age_years: int,
    weight_kg: float,
    hb_g_dl: float,
    as_of: datetime,
) -> EligibilityResult:
    """Pure function: same inputs always produce the same result and reason
    list, so every rejection is explainable and reproducible for audit."""
    reasons: list[str] = []

    if age_years < MIN_AGE_YEARS or age_years > MAX_AGE_YEARS:
        reasons.append(
            f"age {age_years} outside allowed range "
            f"[{MIN_AGE_YEARS}, {MAX_AGE_YEARS}]"
        )

    if weight_kg < MIN_WEIGHT_KG:
        reasons.append(f"weight {weight_kg}kg below minimum {MIN_WEIGHT_KG}kg")

    if hb_g_dl < MIN_HB_G_DL:
        reasons.append(f"hemoglobin {hb_g_dl}g/dL below minimum {MIN_HB_G_DL}g/dL")

    min_interval = MIN_INTERVAL_DAYS.get(component_requested)
    if min_interval is not None and donor.last_donation_at is not None:
        days_since = (as_of - donor.last_donation_at).days
        if days_since < min_interval:
            reasons.append(
                f"only {days_since}d since last donation; "
                f"{component_requested.value} requires {min_interval}d"
            )

    for flag in donor.deferral_flags:
        if flag.deferred_until is None:
            reasons.append(f"permanent deferral: {flag.reason}")
        elif flag.deferred_until >= as_of.date():
            reasons.append(
                f"temporary deferral until {flag.deferred_until.isoformat()}: "
                f"{flag.reason}"
            )

    if "contactable" not in donor.consent_scopes:
        reasons.append("donor has not granted 'contactable' consent")

    return EligibilityResult(eligible=len(reasons) == 0, reasons=reasons)

"""
Blood-group compatibility — deterministic, static, unit-tested (SPEC C-04).

This is a rule-engine module by design: NEVER model-inferred, NEVER touched by
ML. Both the donor-matching branch and the inventory-matching branch (SPEC
§6.1, step 4a/4b) import from here so there is exactly one source of truth for
"can group X give to group Y".
"""

from __future__ import annotations

from contracts.models import BloodGroup, Component

# Recipient -> acceptable RBC donor groups. Verbatim from SPEC §3.3.
_RBC_ACCEPTABLE_DONORS: dict[BloodGroup, frozenset[BloodGroup]] = {
    BloodGroup.O_NEG: frozenset({BloodGroup.O_NEG}),
    BloodGroup.O_POS: frozenset({BloodGroup.O_NEG, BloodGroup.O_POS}),
    BloodGroup.A_NEG: frozenset({BloodGroup.O_NEG, BloodGroup.A_NEG}),
    BloodGroup.A_POS: frozenset(
        {BloodGroup.O_NEG, BloodGroup.O_POS, BloodGroup.A_NEG, BloodGroup.A_POS}
    ),
    BloodGroup.B_NEG: frozenset({BloodGroup.O_NEG, BloodGroup.B_NEG}),
    BloodGroup.B_POS: frozenset(
        {BloodGroup.O_NEG, BloodGroup.O_POS, BloodGroup.B_NEG, BloodGroup.B_POS}
    ),
    BloodGroup.AB_NEG: frozenset(
        {BloodGroup.O_NEG, BloodGroup.A_NEG, BloodGroup.B_NEG, BloodGroup.AB_NEG}
    ),
    BloodGroup.AB_POS: frozenset(BloodGroup),  # universal recipient
}


def rbc_acceptable_donors(recipient_group: BloodGroup) -> frozenset[BloodGroup]:
    """Donor groups whose RBC can safely be given to `recipient_group`."""
    return _RBC_ACCEPTABLE_DONORS[recipient_group]


# Plasma compatibility is the mathematical inverse of the RBC matrix (SPEC
# §3.3: "Plasma compatibility is the inverse."). Donor D's plasma is safe for
# recipient R exactly when R's RBC would be safe to give to D — i.e. D is a
# valid plasma donor for R iff R appears in D's RBC-acceptable-donor set.
# Derived once at import time rather than hand-maintained, so it can never
# drift from the RBC table above.
def _build_plasma_table() -> dict[BloodGroup, frozenset[BloodGroup]]:
    table: dict[BloodGroup, frozenset[BloodGroup]] = {}
    for recipient in BloodGroup:
        # Recipient R accepts plasma from donor D iff R is in
        # RBC-acceptable-donors(D) — i.e. iff R's own RBC would be safe to
        # give to D. This yields the known invariants: AB is the universal
        # plasma donor, O is the universal plasma recipient.
        table[recipient] = frozenset(
            donor
            for donor in BloodGroup
            if recipient in _RBC_ACCEPTABLE_DONORS[donor]
        )
    return table


_PLASMA_ACCEPTABLE_DONORS: dict[BloodGroup, frozenset[BloodGroup]] = _build_plasma_table()


def plasma_acceptable_donors(recipient_group: BloodGroup) -> frozenset[BloodGroup]:
    """Donor groups whose plasma can safely be given to `recipient_group`."""
    return _PLASMA_ACCEPTABLE_DONORS[recipient_group]


# Components that follow RBC-style matching vs plasma-style matching.
_PLASMA_LIKE_COMPONENTS = frozenset({Component.FFP, Component.CRYOPRECIPITATE})


def acceptable_donor_groups(
    recipient_group: BloodGroup, component: Component
) -> frozenset[BloodGroup]:
    """Single entry point used by both the donor branch and the inventory
    branch of match-svc (SPEC §6.1 step 4a/4b) so compatibility logic is
    computed exactly once per request, not duplicated per branch."""
    if component in _PLASMA_LIKE_COMPONENTS:
        return plasma_acceptable_donors(recipient_group)
    return rbc_acceptable_donors(recipient_group)

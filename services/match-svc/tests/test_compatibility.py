from compatibility import (
    acceptable_donor_groups,
    plasma_acceptable_donors,
    rbc_acceptable_donors,
)
from contracts.models import BloodGroup, Component


def test_rbc_matrix_matches_spec_exactly():
    # SPEC §3.3, transcribed verbatim
    assert rbc_acceptable_donors(BloodGroup.O_NEG) == {BloodGroup.O_NEG}
    assert rbc_acceptable_donors(BloodGroup.O_POS) == {BloodGroup.O_NEG, BloodGroup.O_POS}
    assert rbc_acceptable_donors(BloodGroup.A_NEG) == {BloodGroup.O_NEG, BloodGroup.A_NEG}
    assert rbc_acceptable_donors(BloodGroup.A_POS) == {
        BloodGroup.O_NEG, BloodGroup.O_POS, BloodGroup.A_NEG, BloodGroup.A_POS,
    }
    assert rbc_acceptable_donors(BloodGroup.B_NEG) == {BloodGroup.O_NEG, BloodGroup.B_NEG}
    assert rbc_acceptable_donors(BloodGroup.B_POS) == {
        BloodGroup.O_NEG, BloodGroup.O_POS, BloodGroup.B_NEG, BloodGroup.B_POS,
    }
    assert rbc_acceptable_donors(BloodGroup.AB_NEG) == {
        BloodGroup.O_NEG, BloodGroup.A_NEG, BloodGroup.B_NEG, BloodGroup.AB_NEG,
    }
    assert rbc_acceptable_donors(BloodGroup.AB_POS) == set(BloodGroup)


def test_o_neg_is_universal_rbc_donor():
    for recipient in BloodGroup:
        assert BloodGroup.O_NEG in rbc_acceptable_donors(recipient)


def test_ab_pos_is_universal_rbc_recipient():
    assert rbc_acceptable_donors(BloodGroup.AB_POS) == set(BloodGroup)


def test_ab_is_universal_plasma_donor():
    for recipient in BloodGroup:
        # both Rh variants of AB should show up as valid plasma donors
        # for every recipient
        assert BloodGroup.AB_POS in plasma_acceptable_donors(recipient)


def test_o_neg_is_universal_plasma_recipient():
    assert plasma_acceptable_donors(BloodGroup.O_NEG) == set(BloodGroup)


def test_ab_pos_recipient_only_accepts_ab_pos_plasma():
    # Strictest case: an AB+ recipient's antigens are only a subset of AB+
    # donors' antigens, so only AB+ plasma is valid under this derivation.
    assert plasma_acceptable_donors(BloodGroup.AB_POS) == {BloodGroup.AB_POS}


def test_acceptable_donor_groups_routes_rbc_vs_plasma_correctly():
    rbc_route = acceptable_donor_groups(BloodGroup.A_POS, Component.RBC)
    plasma_route = acceptable_donor_groups(BloodGroup.A_POS, Component.FFP)
    assert rbc_route == rbc_acceptable_donors(BloodGroup.A_POS)
    assert plasma_route == plasma_acceptable_donors(BloodGroup.A_POS)
    assert rbc_route != plasma_route


def test_whole_blood_and_platelets_use_rbc_style_matching():
    for component in (Component.WHOLE_BLOOD, Component.PLATELETS_RDP, Component.PLATELETS_SDP):
        assert acceptable_donor_groups(BloodGroup.B_POS, component) == rbc_acceptable_donors(
            BloodGroup.B_POS
        )

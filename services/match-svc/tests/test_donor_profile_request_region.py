from contracts.models import BloodGroup, DonorProfileRequest


def test_donor_profile_request_accepts_location_region_field():
    payload = DonorProfileRequest(
        display_name="Test Donor",
        blood_group=BloodGroup.O_POS,
        city="Pune",
        region_id="Pune",
        availability="available",
        consent_contact=False,
        notification_channels=["email"],
    )

    assert payload.city == "Pune"
    assert payload.region_id == "Pune"


def test_organization_onboard_region_is_normalized_for_default_service_preference():
    from auth_api import _normalize_region_for_preference

    assert _normalize_region_for_preference("  Pune City  ") == "Pune City"
    assert _normalize_region_for_preference("Pune") == "Pune"

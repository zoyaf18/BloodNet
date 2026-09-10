"""Hard donor outreach limits, independent of probabilistic ranking."""
import math
import os


def within_outreach_scope(profile, *, group, region, lat, lng):
    """Fail closed for missing region/location; outreach uses the requested group."""
    if not region or str(profile.get('region_id') or '').strip().casefold() != str(region).strip().casefold():
        return False
    if str(profile.get('blood_group')) != str(group):
        return False
    try:
        a, b, c, d = map(float, (profile.get('lat'), profile.get('lng'), lat, lng))
        limit = float(os.getenv('BLOODNET_DONOR_MAX_DISTANCE_KM', '50'))
        if not all(map(math.isfinite, (a,b,c,d,limit))) or limit <= 0 or not (-90<=a<=90 and -90<=c<=90 and -180<=b<=180 and -180<=d<=180):
            return False
        delta_lat, delta_lng = math.radians(c-a), math.radians(d-b)
        h = math.sin(delta_lat/2)**2 + math.cos(math.radians(a))*math.cos(math.radians(c))*math.sin(delta_lng/2)**2
        distance = 6371 * 2 * math.asin(math.sqrt(min(1,max(0,h))))
        return distance <= limit
    except (TypeError, ValueError):
        return False


def current_recipient_allowed(database_url, donor_id, request_id):
    """Revalidate current profile, role and facility at delivery, including queued mail."""
    import psycopg
    from psycopg.rows import dict_row
    from contracts.location import operational_region
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        row = connection.execute("""
          SELECT d.blood_group,p.region_id,l.lat,l.lng,r.payload AS request,o.metadata
          FROM donor_profiles d JOIN users u ON u.id=d.user_id
          JOIN user_locations l ON l.user_id=d.user_id
          JOIN user_region_preferences p ON p.user_id=d.user_id
          JOIN workflow_requests r ON r.request_id=%s
          JOIN organizations o ON (o.id::text=r.payload->>'hospital_id' OR o.metadata->>'hospital_id'=r.payload->>'hospital_id')
          WHERE d.user_id=%s AND u.status='active' AND u.email_verified
          AND d.consent_contact AND d.availability='available' AND d.eligibility_status='eligible'
          AND d.date_of_birth<=CURRENT_DATE-INTERVAL '18 years'
          AND (d.next_eligible_at IS NULL OR d.next_eligible_at<=NOW())
          AND EXISTS (SELECT 1 FROM organization_memberships m WHERE m.user_id=d.user_id AND m.role='donor' AND m.status='active' AND (m.expires_at IS NULL OR m.expires_at>NOW()))
          AND EXISTS (SELECT 1 FROM workflow_cases c WHERE c.payload->>'request_id'=r.request_id AND c.payload->>'outcome' NOT IN ('cancelled','fulfilled'))
        """, (request_id,donor_id)).fetchall()
    if len(row)!=1:
        return False
    item=row[0]; metadata=item['metadata'] or {}; geo=metadata.get('geo') or {}
    return within_outreach_scope(item,group=item['request'].get('group'),region=operational_region(metadata),lat=geo.get('lat'),lng=geo.get('lng'))

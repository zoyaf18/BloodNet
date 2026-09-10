import pytest
from contracts.outreach_policy import within_outreach_scope
from tests_phase2.test_donor_profile_integration import donor_identity, profile_client


@pytest.mark.parametrize('change,allowed', [
    ({},True), ({'region_id':'Mumbai'},False), ({'region_id':None},False),
    ({'lat':19.076,'lng':72.8777},False), ({'blood_group':'A+'},False),
    ({'blood_group':'O-'},False), ({'lat':None},False), ({'lat':float('nan')},False),
    ({'region_id':' PUNE '},True),
])
def test_donor_outreach_hard_limits(change,allowed,monkeypatch):
    monkeypatch.setenv('BLOODNET_DONOR_MAX_DISTANCE_KM','50')
    profile={'blood_group':'O+','region_id':'Pune','lat':18.5204,'lng':73.8567,**change}
    assert within_outreach_scope(profile,group='O+',region='Pune',lat=18.5204,lng=73.8567) is allowed


def test_invalid_distance_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv('BLOODNET_DONOR_MAX_DISTANCE_KM','nan')
    assert not within_outreach_scope({'blood_group':'O+','region_id':'Pune','lat':18.52,'lng':73.85},group='O+',region='Pune',lat=18.52,lng=73.85)


def test_delivery_rechecks_stored_region_distance_group_and_consent(donor_identity):
    import os
    import psycopg
    from psycopg.types.json import Jsonb
    from uuid import uuid4
    from contracts.models import OrganizationType, UserLocation
    from contracts.outreach_policy import current_recipient_allowed
    repository, identity=donor_identity
    assert profile_client(repository,identity).put('/api/v1/auth/donor-profile',json={
        'display_name':'Scope donor','blood_group':'O+','date_of_birth':'1999-10-07',
        'city':'Pune','region_id':'Pune','availability':'available','consent_contact':True,'notification_channels':['email'],
    }).status_code==200
    repository.upsert_user_location(identity.subject_id,UserLocation(lat=18.5204,lng=73.8567))
    hospital=repository.create_organization(name='Scope hospital',org_type=OrganizationType.HOSPITAL,contact_email='scope@example.invalid',metadata={'city':'Pune','geo':{'lat':18.5204,'lng':73.8567}})
    request='SCOPE-'+uuid4().hex
    dsn=os.environ['BLOODNET_DATABASE_URL']
    with psycopg.connect(dsn) as connection:
        connection.execute('INSERT INTO workflow_requests(request_id,payload) VALUES (%s,%s)',(request,Jsonb({'hospital_id':str(hospital.id),'group':'O+'})))
        connection.execute('INSERT INTO workflow_cases(case_id,payload) VALUES (%s,%s)',(request,Jsonb({'case_id':request,'request_id':request,'outcome':'pending'})))
    def allowed(): return current_recipient_allowed(dsn,str(identity.subject_id),request)
    assert allowed()
    repository.upsert_user_region_preference(identity.subject_id,'Mumbai')
    assert not allowed()
    repository.upsert_user_region_preference(identity.subject_id,'Pune')
    assert allowed()
    repository.upsert_user_location(identity.subject_id,UserLocation(lat=19.076,lng=72.8777))
    assert not allowed()
    repository.upsert_user_location(identity.subject_id,UserLocation(lat=18.5204,lng=73.8567))
    for group in ('A+','O-'):
        with psycopg.connect(dsn) as connection:
            connection.execute('UPDATE donor_profiles SET blood_group=%s WHERE user_id=%s',(group,str(identity.subject_id)))
        assert not allowed()
    with psycopg.connect(dsn) as connection:
        connection.execute("UPDATE donor_profiles SET blood_group='O+',consent_contact=false WHERE user_id=%s",(str(identity.subject_id),))
    assert not allowed()
    with psycopg.connect(dsn) as connection:
        connection.execute('DELETE FROM workflow_cases WHERE case_id=%s',(request,))
        connection.execute('DELETE FROM workflow_requests WHERE request_id=%s',(request,))

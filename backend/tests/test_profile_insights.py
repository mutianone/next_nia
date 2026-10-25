from datetime import date

import pytest
from backend.tests.auth_client import TestClient
from backend.app.main import create_app

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.app.profile_insights.business_today', lambda: date(2026, 10, 25))
    with TestClient(create_app(tmp_path)) as c:
        yield c


def person(client, number='INSIGHT-1', **extra):
    result = client.post('/api/people', json={'name':'分析测试人员', 'nationality':'测试国', 'document_number':number, 'source':'合成验收材料', **extra})
    assert result.status_code == 201, result.text
    return result.json()['id']


def stay(client, pid, **extra):
    result = client.post(f'/api/people/{pid}/stays', json={'entry_date':'2026-10-01', 'source':'合成入境材料', **extra})
    assert result.status_code == 201, result.text
    return result.json()['id']


def insights(client, pid):
    response = client.get(f'/api/profiles/{pid}')
    assert response.status_code == 200, response.text
    return response.json()['insights']


def test_missing_data_remains_unknown_and_other_people_are_excluded(client):
    a,b = person(client),person(client, 'INSIGHT-2')
    stay(client, b, permitted_until='2026-10-20')
    client.post('/api/records',json={'person_id':b,'title':'其他人员笔录','scenario':'study'})
    data = insights(client,a)
    assert data['travel'] == data['deadlines'] == data['checks'] == []
    assert data['document'] == {'expiry':None,'days_remaining':None}
    assert data['completeness']['filled'] == 4 and data['completeness']['total'] == 9
    assert all(row['total'] == 0 for row in data['history'])


def test_deadlines_reuse_verified_extension_and_never_substitute_visa_expiry(client):
    pid = person(client, verification='verified', document_expiry='2027-01-01')
    sid = stay(client,pid,permitted_until='2026-10-20')
    data = insights(client,pid)
    assert data['deadlines'][0]['category'] == 'past_due'
    assert data['deadlines'][0]['days_remaining'] == -5
    payload = {'visa_valid_until':'2030-01-01','extension_status':'approved','extension_until':'2026-12-01','verified':True,'source':'合成延期批准材料'}
    response = client.put(f'/api/alerts/stays/{sid}/facts',json=payload)
    assert response.status_code == 200, response.text
    deadline = insights(client,pid)['deadlines'][0]
    assert deadline['category'] == 'outside_window'
    assert deadline['effective_until'] == '2026-12-01'
    assert deadline['days_remaining'] == 37
    assert '合成延期批准材料' in deadline['source']
    response = client.put(f'/api/alerts/stays/{sid}/facts',json={**payload,'extension_status':'pending','extension_until':None})
    assert response.status_code == 200, response.text
    deadline = insights(client,pid)['deadlines'][0]
    assert deadline['category'] == 'needs_review'
    assert deadline['effective_until'] is None and deadline['days_remaining'] is None


def test_travel_date_scopes_overlap_and_fact_source_changes(client):
    pid=person(client,verification='verified')
    sid=stay(client,pid,exit_date='2026-10-10')
    stay(client,pid,entry_date='2026-10-05',exit_date='2026-10-12')
    stay(client,pid,entry_date='2026-11-01',exit_date='2026-11-05')
    facts=f'/api/profiles/{pid}/facts'
    first=client.post(facts,json={'label':'登记住址','value':'甲址','source':'第一份材料','source_type':'stay','source_id':sid}).json()
    client.post(facts,json={'label':'登记住址','value':'乙址','source':'第二份材料'})
    response=client.put(f'/api/people/{pid}/stays/{sid}',json={'entry_date':'2026-10-02','exit_date':'2026-10-10','source':'修订记录'})
    assert response.status_code == 200
    data=insights(client,pid)
    assert data['entries']==2 and data['exits']==2 and data['unclosed']==0
    assert data['travel'][0]['status']=='future' and data['travel'][0]['days'] is None
    assert any(t['days']==8 for t in data['travel'])
    assert {'overlap','values','source_updated'} <= {c['kind'] for c in data['checks']}
    assert any(first['id'] in c['source_ids'] for c in data['checks'])
    assert all(d['category']=='excluded' for d in data['deadlines'])


def test_multiple_unclosed_stays_never_get_a_confirmed_countdown(client):
    pid=person(client,verification='verified')
    stay(client,pid,permitted_until='2026-10-20')
    stay(client,pid,entry_date='2026-10-05',permitted_until='2026-12-01')
    data=insights(client,pid)
    assert data['unclosed']==2
    assert all(d['category']=='needs_review' and d['days_remaining'] is None for d in data['deadlines'])
    assert any(c['kind']=='open_stays' for c in data['checks'])

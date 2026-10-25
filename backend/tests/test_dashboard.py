from datetime import datetime, timedelta, timezone
import json
from zoneinfo import ZoneInfo
from backend.tests.auth_client import TestClient
from sqlalchemy import URL, create_engine, func, select
from sqlalchemy.orm import Session
from backend.app.alerts import Alert, AlertRule
from backend.app.database import AuditEvent
from backend.app.main import create_app


def test_dashboard_uses_actual_records_and_excludes_archived_people(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/api/health').json()['stage'] == 5
        empty = client.get('/api/dashboard').json()
        assert empty['demo'] is False
        assert all(value == 0 for value in empty['stats'].values())
        person = client.post('/api/people', json={'name':'合成测试人员', 'nationality':'示例', 'document_number':'TEST0001', 'source':'自动化测试'}).json()
        today = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
        assert client.post(f"/api/people/{person['id']}/stays", json={'entry_date':today,'source':'自动化测试'}).status_code == 201
        doc = client.post('/api/knowledge/import', data={'title':'自动化测试材料（非法律）','version':'v1','effective_from':today,'source':'合成测试'},files={'file':('test.txt','第一条 测试材料登记。'.encode(),'text/plain')}).json()
        data = client.get('/api/dashboard').json()
        assert data['stats']['people'] == data['stats']['registered_stays'] == data['stats']['pending_reviews'] == 1
        assert data['stats']['knowledge_documents'] == 0
        assert data['trend'][-1]['entries'] == 1
        assert data['alerts'] == []
        assert data['activities']
        assert '合成测试人员' not in str(data['activities'])
        assert client.patch(f"/api/knowledge/{doc['id']}", json={'status':'published'}).status_code == 200
        assert client.post(f"/api/people/{person['id']}/archive",json={'archived':True}).status_code == 200
        data = client.get('/api/dashboard').json()
        assert data['stats']['people'] == data['stats']['registered_stays'] == data['stats']['pending_reviews'] == 0
        assert data['stats']['knowledge_documents'] == 1
        assert data['trend'] == []


def register_stay(client, suffix, today, permitted_until):
    response = client.post('/api/people', json={
        'name': '虚构集成人员' + suffix, 'nationality': '示例国籍',
        'document_number': 'DASHBOARD-TEST-' + suffix,
        'source': '自动化测试合成资料', 'verification': 'verified',
    })
    assert response.status_code == 201, response.text
    person = response.json()
    response = client.post(f"/api/people/{person['id']}/stays", json={
        'entry_date': (today - timedelta(days=10)).isoformat(),
        'permitted_until': permitted_until.isoformat() if permitted_until else None,
        'source': '自动化测试合成停留资料',
    })
    assert response.status_code == 201, response.text
    return person, response.json()


def test_dashboard_tracks_actual_alerts_and_document_states_without_activity_personal_details(tmp_path):
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    with TestClient(create_app(tmp_path)) as client:
        person, stay = register_stay(client, 'TODAY', today, today)
        unknown_person, _ = register_stay(client, 'UNKNOWN', today, None)
        scan = client.post('/api/alerts/scan')
        assert scan.status_code == 200, scan.text
        assert scan.json()['created'] == scan.json()['scanned'] == 2
        dashboard = client.get('/api/dashboard').json()
        assert dashboard['demo'] is False
        assert dashboard['stats']['pending_alerts'] == 2
        assert dashboard['alerts_last_scan_at'] == scan.json()['scanned_at']
        reminders = {item['code']: item for item in dashboard['alerts']}
        due = reminders[person['code']]
        unknown = reminders[unknown_person['code']]
        assert (due['category'], due['days'], due['status']) == ('今日到期', 0, '待核查')
        assert (unknown['category'], unknown['days']) == ('资料待核实', None)
        assert client.post(f"/api/alerts/{unknown['id']}/actions", json={
            'action': 'close', 'note': '自动化测试人工关闭，仍不作违法判断',
        }).status_code == 200
        assert client.get('/api/dashboard').json()['stats']['pending_alerts'] == 1

        templates = client.get('/api/documents/templates').json()['items']
        template = next(item for item in templates if item['kind'] == 'internal' and item['status'] == 'approved')
        facts = '测试专用正文标记：不得进入工作台审计摘要'
        officer = '虚构承办人员标记'
        drafts = []
        for suffix in ('甲', '乙'):
            response = client.post('/api/documents', json={
                'template_id': template['id'], 'person_id': person['id'],
                'stay_id': stay['id'], 'alert_id': due['id'], 'title': '测试文书内容标记' + suffix,
                'fields': {'facts': facts, 'officer': officer, 'organization': '虚构单位', 'document_date': today.isoformat()},
            })
            assert response.status_code == 201, response.text
            drafts.append(response.json())
        assert client.get('/api/dashboard').json()['stats']['draft_records'] == 2
        review_note = '仅测试核对记录，不应进入工作台活动摘要'
        reviewed = client.patch(f"/api/documents/{drafts[0]['id']}", json={
            'expected_revision': drafts[0]['revision'], 'status': 'reviewed',
            'confirmed': True, 'review_note': review_note,
        })
        assert reviewed.status_code == 200, reviewed.text
        assert client.get('/api/dashboard').json()['stats']['draft_records'] == 1
        archived = client.patch(f"/api/documents/{drafts[1]['id']}", json={
            'expected_revision': drafts[1]['revision'], 'status': 'archived',
        })
        assert archived.status_code == 200, archived.text
        dashboard = client.get('/api/dashboard').json()
        assert dashboard['stats']['draft_records'] == 0
        assert dashboard['stats']['pending_alerts'] == 1
        activities = dashboard['activities']
        assert {item['title'] for item in activities} == {'创建文书草稿', '完成人工文书核对', '归档文书'}
        assert all(item['type'] == 'document' for item in activities)
        activity_text = json.dumps(activities, ensure_ascii=False)
        for private_value in (person['name'], unknown_person['name'], person['document_number'], facts, officer, review_note, *(item['title'] for item in drafts)):
            assert private_value not in activity_text


def test_restarting_app_scans_due_persisted_stays_before_serving_dashboard(tmp_path):
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    with TestClient(create_app(tmp_path)) as client:
        person, stay = register_stay(client, 'RESTART', today, today - timedelta(days=1))
        # Startup's initial scan preceded the new local records. No manual scan
        # is used: the next process must discover this persisted stay itself.
        assert client.get('/api/dashboard').json()['stats']['pending_alerts'] == 0

    # Simulate elapsed downtime by changing only the persisted scheduling clock,
    # after the application has shut down; do not wait or invoke scan directly.
    prior_scan = datetime.now(timezone.utc) - timedelta(hours=2)
    engine = create_engine(URL.create('sqlite', database=str(tmp_path / 'test.sqlite3')))
    try:
        with Session(engine) as session, session.begin():
            rule = session.get(AlertRule, 1)
            assert rule is not None
            rule.enabled, rule.scan_interval_minutes, rule.last_scan_at = True, 60, prior_scan
            assert session.scalar(select(func.count()).select_from(Alert)) == 0
            scans_before = session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == 'alerts.scanned'))
    finally:
        engine.dispose()

    with TestClient(create_app(tmp_path)) as reopened:
        dashboard = reopened.get('/api/dashboard').json()
        assert dashboard['stats']['people'] == dashboard['stats']['registered_stays'] == 1
        assert dashboard['stats']['pending_alerts'] == 1
        assert len(dashboard['alerts']) == 1
        reminder = dashboard['alerts'][0]
        assert reminder['code'] == person['code']
        assert (reminder['category'], reminder['days']) == ('登记期限已过', -1)
        assert datetime.fromisoformat(dashboard['alerts_last_scan_at']) > prior_scan
        detail = reopened.get(f"/api/alerts/{reminder['id']}").json()
        assert detail['person_id'] == person['id'] and detail['stay_id'] == stay['id']
        assert detail['history'][0]['action'] == 'created'
        with Session(reopened.app.state.engine) as session:
            assert session.scalar(select(func.count()).select_from(Alert)) == 1
            scans_after = session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == 'alerts.scanned'))
            assert scans_after == scans_before + 1


def test_dashboard_counts_real_transcript_drafts_separately_from_documents(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/api/dashboard').json()['stats']['transcript_drafts'] == 0
        response = client.post('/api/records', json={
            'title': '合成测试笔录标题不得进入动态', 'scenario': 'residence',
            'location': '仅供测试的地点', 'interviewers': '合成询问人',
        })
        assert response.status_code == 201, response.text
        record = response.json()
        data = client.get('/api/dashboard').json()
        assert data['stats']['transcript_drafts'] == 1
        assert data['stats']['draft_records'] == 0
        assert any(item['title'] == '新建询问笔录' for item in data['activities'])
        assert '合成测试笔录标题' not in str(data['activities'])
        assert '合成询问人' not in str(data['activities'])
        archived = client.patch(f"/api/records/{record['id']}", json={'expected_revision': record['revision'], 'status': 'archived'})
        assert archived.status_code == 200, archived.text
        assert client.get('/api/dashboard').json()['stats']['transcript_drafts'] == 0
        restored = client.patch(f"/api/records/{record['id']}", json={'expected_revision': archived.json()['revision'], 'status': 'draft'})
        assert restored.status_code == 200, restored.text
        assert client.get('/api/dashboard').json()['stats']['transcript_drafts'] == 1

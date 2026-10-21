from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient
from backend.app.main import create_app


def test_dashboard_uses_actual_records_and_excludes_archived_people(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/api/health').json()['stage'] == 2
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

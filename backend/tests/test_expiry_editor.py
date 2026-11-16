from datetime import timedelta
from io import BytesIO
from zipfile import ZipFile
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from sqlalchemy import select
from backend.app.main import create_app
from backend.app.people import Person
from backend.app.alerts import Alert, scan_alerts, business_today

def test_business_edits_alert_document_without_document_center(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        with Session(client.app.state.engine) as s:
            s.add(Person(id='test-person',code='TEST',name='测试人',nationality='测试国籍',document_number='TEST001',document_type='passport',source='测试',document_expiry=business_today()-timedelta(days=7)))
            s.commit()
        scan_alerts(client.app.state.engine)
        with Session(client.app.state.engine) as s:
            alert=s.scalar(select(Alert));identifier=alert.id
        assert client.post('/api/auth/login',json={'phone':'root','password':'root'}).status_code==200
        assert client.get(f'/api/alerts/{identifier}/decision').status_code==403
        assert client.get('/api/documents').status_code==404
        client.post('/api/auth/logout')
        assert client.post('/api/auth/login',json={'phone':'15210451737','password':'123456'}).status_code==200
        path=f'/api/alerts/{identifier}/decision'
        doc=client.get(path).json()
        assert doc['fields']['person_name']=='测试人'
        response=client.put(path,json={'expected_revision':doc['revision'],'fields':{'evidence':'测试证据','address':'补充地址'}})
        assert response.status_code==200,response.text
        assert client.put(path,json={'expected_revision':doc['revision'],'fields':{'evidence':'覆盖'}}).status_code==409
        exported=client.get(path+'.docx')
        assert exported.status_code==200
        with ZipFile(BytesIO(exported.content)) as z:
            text=z.read('word/document.xml').decode()
            assert '补充地址' in text and '测试人' in text and '执行要求' in text
        scan_alerts(client.app.state.engine)
        assert client.get(path).json()['fields']['evidence']=='测试证据'

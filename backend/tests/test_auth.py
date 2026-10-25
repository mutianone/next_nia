import hashlib
import time
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app.main import create_app
from backend.app.auth import Account, LoginSession, LoginAttempt, verify_password
from backend.app.maintenance import snapshot, verify_archive, restore_empty
from backend.app.database import application_engine

ADMIN = {'phone':'15210451737','password':'123456'}
@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv('NEXT_NIA_PRESENTATION', raising=False)
    with TestClient(create_app(tmp_path)) as client:
        yield client

def login(client, credentials=ADMIN):
    response=client.post('/api/auth/login',json=credentials)
    assert response.status_code==200,response.text
    return response

def create(client, role='viewer', phone='13800138000'):
    response=client.post('/api/accounts',json={'phone':phone,'password':'abc12345','name':'权限验收','role':role})
    assert response.status_code==201,response.text
    return response.json()

def test_requires_login_and_seeds_hashed_admin(app):
    assert app.get('/api/health').status_code==200
    for path in ['/api/dashboard','/api/settings','/api/people','/api/auth/me','/api/docs','/api/accounts']:
        assert app.get(path).status_code==401
    response=login(app)
    assert response.json()['role']=='admin'
    assert 'password' not in response.text
    cookie=response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie
    with Session(app.app.state.engine) as session:
        user=session.scalar(select(Account))
        assert user.password_hash!='123456' and verify_password('123456',user.password_hash)
        token=app.cookies.get('nia_session')
        assert session.get(LoginSession,hashlib.sha256(token.encode()).hexdigest())
    assert app.get('/api/dashboard').status_code==200
    assert app.post('/api/auth/logout').status_code==200
    assert app.get('/api/dashboard').status_code==401

@pytest.mark.parametrize('role,path,method,expected',[
 ('viewer','/api/people','GET',200),('viewer','/api/knowledge/search','POST',422),
 ('viewer','/api/people','POST',403),('viewer','/api/people/export.csv','GET',403),
 ('viewer','/api/analytics/export','GET',403),('viewer','/api/records/any/export.docx','GET',403),
 ('viewer','/api/records/any/audio/any/file?download=true','GET',403),
 ('viewer','/api/knowledge/ask','POST',403),('viewer','/api/accounts','GET',403),
 ('viewer','/api/maintenance','GET',403),('business','/api/accounts','POST',403),
 ('business','/api/settings','PUT',403),('business','/api/knowledge/import','POST',403),
 ('business','/api/knowledge/any','PATCH',403),('business','/api/documents/templates','POST',403),
 ('business','/api/alerts/rules','PUT',403),('business','/api/people','POST',422),
 ('business','/api/records','POST',422),('business','/api/documents/any','PATCH',422),
])
def test_roles_enforced_at_api(app,role,path,method,expected):
    login(app);create(app,role);app.post('/api/auth/logout')
    login(app,{'phone':'13800138000','password':'abc12345'})
    assert app.request(method,path).status_code==expected
    result=app.get('/api/settings').json()['settings']
    assert 'environment' not in result
    assert 'base_url' not in result['ai']['providers']['siliconflow']

def test_disable_reset_change_role_revoke_other_device(app):
    login(app);account=create(app)
    with TestClient(app.app) as other:
        login(other,{'phone':account['phone'],'password':'abc12345'})
        assert other.get('/api/people').status_code==200
        assert app.patch('/api/accounts/'+account['id'],json={**account,'enabled':False}).status_code==200
        assert other.get('/api/people').status_code==401
        assert other.post('/api/auth/login',json={'phone':account['phone'],'password':'abc12345'}).status_code==401
        assert app.patch('/api/accounts/'+account['id'],json={**account,'enabled':True,'role':'business'}).status_code==200
        login(other,{'phone':account['phone'],'password':'abc12345'})
        assert app.post('/api/accounts/'+account['id']+'/password',json={'password':'new12345'}).status_code==200
        assert other.get('/api/people').status_code==401
        login(other,{'phone':account['phone'],'password':'new12345'})
        assert other.get('/api/auth/me').json()['role']=='business'

def test_last_admin_duplicate_phone_profile_and_password(app):
    user=login(app).json()
    for patch in [{'enabled':False},{'role':'viewer'}]:
        assert app.patch('/api/accounts/'+user['id'],json={**user,**patch}).status_code==409
    assert app.post('/api/accounts',json={**ADMIN,'name':'重复','role':'viewer'}).status_code==409
    assert app.patch('/api/auth/profile',json={'name':'值班管理员','role':'viewer'}).json()['role']=='admin'
    assert app.post('/api/auth/password',json={'current_password':'wrong123','new_password':'abc12345'}).status_code==400
    assert app.post('/api/auth/password',json={'current_password':'123456','new_password':'abc12345'}).status_code==200
    assert app.get('/api/auth/me').status_code==401
    login(app,{'phone':ADMIN['phone'],'password':'abc12345'})

def test_lockout_expiry_and_cross_site(app):
    assert app.post('/api/auth/login',json=ADMIN,headers={'origin':'https://evil.test'}).status_code==403
    for _ in range(5):
        assert app.post('/api/auth/login',json={**ADMIN,'password':'wrong123'}).status_code==401
    assert app.post('/api/auth/login',json=ADMIN).status_code==401
    with Session(app.app.state.engine) as session:
        user=session.scalar(select(Account)); user.locked_until=time.time()-1;session.commit()
    login(app)
    with Session(app.app.state.engine) as session:
        for row in session.scalars(select(LoginSession)):row.expires_at=time.time()-1
        session.commit()
    assert app.get('/api/auth/me').status_code==401

def test_backup_never_contains_live_sessions(app,tmp_path):
    login(app)
    archive=tmp_path/'auth.zip'
    manifest=snapshot(app.app.state.engine,archive)
    assert manifest['tables']['accounts']['rows']==1
    assert manifest['tables']['login_sessions']['rows']==0
    assert manifest['tables']['login_attempts']['rows']==0
    verify_archive(archive)

def test_admin_password_not_reset_on_restart(tmp_path,monkeypatch):
    monkeypatch.delenv('NEXT_NIA_PRESENTATION',raising=False)
    with TestClient(create_app(tmp_path)) as first:
        login(first);first.post('/api/auth/password',json={'current_password':'123456','new_password':'changed123'})
    with TestClient(create_app(tmp_path)) as second:
        assert second.post('/api/auth/login',json=ADMIN).status_code==401
        login(second,{'phone':ADMIN['phone'],'password':'changed123'})

def test_account_department_notes_roundtrip_and_validation(app):
    login(app)
    fields = {'name': ' 张明 ', 'department': ' 出入境管理大队 ', 'notes': '负责窗口业务\n兼任材料复核'}
    response = app.post('/api/accounts', json={**fields, 'phone':'13800138000', 'password':'abc12345', 'role':'business'})
    assert response.status_code == 201
    account = response.json()
    assert account['name'] == '张明' and account['department'] == '出入境管理大队'
    assert account['notes'] == fields['notes']
    assert app.patch('/api/accounts/'+account['id'], json={**account,'department':'综合科','notes':'负责人'}).status_code == 200
    # An older client omitting optional fields must not erase stored data.
    assert app.patch('/api/accounts/'+account['id'], json={key:account[key] for key in ('name','phone','role','enabled')}).status_code == 200
    assert app.post('/api/accounts', json={**fields,'department':'字'*101,'phone':'13800138001','password':'abc12345','role':'viewer'}).status_code == 422
    assert app.patch('/api/accounts/'+account['id'], json={**account,'notes':'字'*1001}).status_code == 422
    app.post('/api/auth/logout')
    login(app, {'phone':'13800138000','password':'abc12345'})
    me = app.get('/api/auth/me').json()
    assert me['department'] == '综合科' and me['notes'] == '负责人'
    me = app.patch('/api/auth/profile', json={'name':'张明','department':'擅自更改','notes':'擅自更改'}).json()
    assert me['department'] == '综合科' and me['notes'] == '负责人'


def test_existing_database_adds_account_fields_without_reset(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text
    monkeypatch.delenv('NEXT_NIA_PRESENTATION', raising=False)
    with TestClient(create_app(tmp_path)) as client:
        original = login(client).json()
        client.patch('/api/auth/profile',json={'name':'原管理员'})
        client.post('/api/auth/password',json={'current_password':'123456','new_password':'kept12345'})
    engine = create_engine(f'sqlite:///{tmp_path / "test.sqlite3"}')
    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE accounts DROP COLUMN department'))
        connection.execute(text('ALTER TABLE accounts DROP COLUMN notes'))
    engine.dispose()
    for _ in range(2):
        with TestClient(create_app(tmp_path)) as client:
            user = login(client,{'phone':ADMIN['phone'],'password':'kept12345'}).json()
            assert user['id'] == original['id'] and user['name'] == '原管理员'
            assert user['department'] == user['notes'] == ''


def test_legacy_account_backup_restores_new_fields_as_empty(app, tmp_path):
    import json
    import zipfile
    from sqlalchemy import create_engine
    login(app)
    current, legacy = tmp_path/'current.zip', tmp_path/'legacy.zip'
    snapshot(app.app.state.engine,current)
    with zipfile.ZipFile(current) as source:
        manifest = json.loads(source.read('manifest.json'))
        rows = [json.loads(line) for line in source.read('accounts.jsonl').splitlines()]
        for row in rows:
            row.pop('department'); row.pop('notes')
        raw = ''.join(json.dumps(row)+'\n' for row in rows).encode()
        manifest['tables']['accounts']['columns'] = [c for c in manifest['tables']['accounts']['columns'] if c not in {'department','notes'}]
        manifest['tables']['accounts']['sha256'] = hashlib.sha256(raw).hexdigest()
        with zipfile.ZipFile(legacy,'w') as target:
            for name in source.namelist():
                target.writestr(name,json.dumps(manifest).encode() if name=='manifest.json' else raw if name=='accounts.jsonl' else source.read(name))
    verify_archive(legacy)
    engine = create_engine(f'sqlite:///{tmp_path / "restored.sqlite3"}')
    restore_empty(engine,legacy)
    with Session(engine) as session:
        user = session.scalar(select(Account))
        assert user.phone == ADMIN['phone'] and verify_password('123456', user.password_hash)
        assert user.department == user.notes == ''
    engine.dispose()

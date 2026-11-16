from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app.main import create_app
from backend.app.auth import Account, seed_admin, verify_password

def test_root_login_seed_and_password_change(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        result = client.post('/api/auth/login', json={'phone':'root','password':'root'})
        assert result.status_code == 200
        assert result.json()['role'] == 'admin'
        assert client.get('/api/accounts').status_code == 200
        assert client.post('/api/auth/password', json={'current_password':'root','new_password':'new-password'}).status_code == 200
        seed_admin(client.app.state.engine)
        with Session(client.app.state.engine) as session:
            roots = list(session.scalars(select(Account).where(Account.phone == 'root')))
            assert len(roots) == 1
            assert verify_password('new-password', roots[0].password_hash)
        assert client.post('/api/auth/login', json={'phone':'root','password':'new-password'}).status_code == 200

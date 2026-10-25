"""Explicit administrator fixture for existing authenticated business tests."""
from fastapi.testclient import TestClient as BaseClient
class TestClient(BaseClient):
    __test__ = False
    def __enter__(self):
        super().__enter__()
        if any(getattr(route, 'path', '') == '/api/auth/login' for route in self.app.routes):
            response = self.post('/api/auth/login', json={'phone':'15210451737','password':'123456'})
            assert response.status_code == 200, response.text
        return self

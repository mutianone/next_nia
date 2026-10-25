"""Local phone/password accounts and revocable, opaque cookie sessions."""
import hashlib
import hmac
import secrets
import time
import uuid
from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Float, Integer, String, delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column
from .database import Base, AuditEvent

COOKIE = 'nia_session'
TTL = 8 * 60 * 60
Role = Literal['admin', 'business', 'viewer']

class Account(Base):
    __tablename__ = 'accounts'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    phone: Mapped[str] = mapped_column(String(11), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    role: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    failures: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    last_login: Mapped[float | None] = mapped_column(Float, nullable=True)

class LoginSession(Base):
    __tablename__ = 'login_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(36), index=True)
    expires_at: Mapped[float] = mapped_column(Float)

class LoginAttempt(Base):
    __tablename__ = 'login_attempts'
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[float] = mapped_column(Float)

@contextmanager
def transaction(engine):
    with Session(engine) as session:
        if engine.dialect.name == 'sqlite':
            session.execute(text('BEGIN IMMEDIATE'))
        else:
            # Serialize administrator changes and bootstrap across workers.
            session.execute(text("SELECT pg_advisory_xact_lock(781926369)"))
        yield session
        session.commit()

def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600000).hex()
    return f'pbkdf2_sha256$600000${salt}${digest}'

def verify_password(password, encoded):
    try:
        _, iterations, salt, expected = encoded.split('$')
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False

def public_account(user):
    return {key: getattr(user, key) for key in ('id', 'phone', 'name', 'role', 'enabled', 'created_at', 'last_login')}

def seed_admin(engine):
    with transaction(engine) as session:
        if session.scalar(select(Account.id).limit(1)) is None:
            session.add(Account(phone='18792636900', name='系统管理员', role='admin', password_hash=password_hash('123456')))

def session_user(request):
    token = request.cookies.get(COOKIE, '')
    if not token or len(token) > 128:
        return None
    with Session(request.app.state.engine) as session:
        entry = session.get(LoginSession, hashlib.sha256(token.encode()).hexdigest())
        if entry and entry.expires_at > time.time():
            user = session.get(Account, entry.account_id)
            if user and user.enabled:
                return public_account(user)
    return None

def permitted(role, method, path, query):
    parts = path.rstrip('/').split('/')
    if parts[2:3] == ['auth']:
        return True
    if role == 'admin':
        return True
    if len(parts) < 3 or parts[2] not in {'dashboard', 'settings', 'people', 'knowledge', 'alerts', 'records', 'documents', 'profiles', 'analytics'}:
        return False
    if parts[2] == 'settings':
        return method == 'GET' and len(parts) == 3
    if role == 'viewer':
        if method == 'POST' and path.rstrip('/') == '/api/knowledge/search':
            return True
        download = any(p.startswith('export') or p == 'template.csv' for p in parts) or ('download' in query and query.get('download', '').lower() not in {'false', '0', 'no', 'off'})
        download = download or (parts[2] == 'knowledge' and parts[-1] == 'file')
        return method == 'GET' and not download
    if parts[2] == 'knowledge':
        return method == 'GET' or path.rstrip('/') in {'/api/knowledge/search', '/api/knowledge/ask'}
    if parts[2] == 'documents' and parts[3:4] == ['templates']:
        return method == 'GET'
    if path.rstrip('/') == '/api/alerts/rules':
        return method == 'GET'
    return True

router = APIRouter(prefix='/api')
class Credentials(BaseModel):
    phone: str = Field(pattern=r'^1[3-9]\d{9}$')
    password: str = Field(min_length=6, max_length=128)
class CreateAccount(Credentials):
    name: str = Field(min_length=1, max_length=60, pattern=r'\S')
    role: Role
class EditAccount(BaseModel):
    name: str = Field(min_length=1, max_length=60, pattern=r'\S')
    phone: str = Field(pattern=r'^1[3-9]\d{9}$')
    role: Role
    enabled: bool
class ProfileUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=60, pattern=r'\S')
class PasswordUpdate(BaseModel):
    current_password: str = Field(min_length=6, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)
class ResetPassword(BaseModel):
    password: str = Field(min_length=6, max_length=128)

def audit(session, request, action, subject=None):
    session.add(AuditEvent(action=action, details={'actor_id': getattr(request.state, 'user', {}).get('id'), 'account_id': subject}))

def revoke(session, account_id):
    session.execute(delete(LoginSession).where(LoginSession.account_id == account_id))

@router.post('/auth/login')
def login(body: Credentials, request: Request, response: Response):
    now = time.time()
    ip = request.client.host if request.client else 'local'
    key = hashlib.sha256(ip.encode()).hexdigest()
    denied = False
    with transaction(request.app.state.engine) as session:
        session.execute(delete(LoginAttempt).where(LoginAttempt.expires_at <= now))
        attempt = session.get(LoginAttempt, key)
        if attempt and attempt.count >= 30:
            raise HTTPException(429, '登录尝试过于频繁，请 15 分钟后重试。')
        if attempt is None:
            attempt = LoginAttempt(key=key, count=0, expires_at=now + 900)
            session.add(attempt)
        attempt.count += 1
        user = session.scalar(select(Account).where(Account.phone == body.phone))
        valid = verify_password(body.password, user.password_hash) if user else verify_password(body.password, 'pbkdf2_sha256$600000$unknown$0')
        if not user or not user.enabled or user.locked_until > now or not valid:
            denied = True
            if user and user.locked_until <= now:
                user.failures += 1
                if user.failures >= 5:
                    user.locked_until = now + 900
                    user.failures = 0
            audit(session, request, 'auth.login_failed', user.id if user else None)
        else:
            attempt.count -= 1
            user.failures = 0
            user.locked_until = 0
            user.last_login = now
            session.execute(delete(LoginSession).where(LoginSession.expires_at <= now))
            old_token = request.cookies.get(COOKIE, '')
            session.execute(delete(LoginSession).where(LoginSession.token_hash == hashlib.sha256(old_token.encode()).hexdigest()))
            token = secrets.token_urlsafe(32)
            session.add(LoginSession(token_hash=hashlib.sha256(token.encode()).hexdigest(), account_id=user.id, expires_at=now+TTL))
            result = public_account(user)
            audit(session, request, 'auth.login', user.id)
    if denied:
        raise HTTPException(401, '手机号或密码不正确，或账号已停用 / 暂时锁定。')
    response.set_cookie(COOKIE, token, max_age=TTL, httponly=True, samesite='strict', secure=request.url.scheme == 'https', path='/')
    return result

@router.get('/auth/me')
def me(request: Request):
    return request.state.user

@router.post('/auth/logout')
def logout(request: Request, response: Response):
    with transaction(request.app.state.engine) as session:
        token = request.cookies.get(COOKIE, '')
        session.execute(delete(LoginSession).where(LoginSession.token_hash == hashlib.sha256(token.encode()).hexdigest()))
        audit(session, request, 'auth.logout', request.state.user['id'])
    response.delete_cookie(COOKIE, path='/')
    return {'ok': True}

@router.patch('/auth/profile')
def profile(body: ProfileUpdate, request: Request):
    with transaction(request.app.state.engine) as session:
        user = session.get(Account, request.state.user['id'])
        user.name = body.name.strip()
        audit(session, request, 'account.profile_updated', user.id)
        return public_account(user)

@router.post('/auth/password')
def change_password(body: PasswordUpdate, request: Request, response: Response):
    with transaction(request.app.state.engine) as session:
        user = session.get(Account, request.state.user['id'])
        if not verify_password(body.current_password, user.password_hash):
            raise HTTPException(400, '当前密码不正确。')
        user.password_hash = password_hash(body.new_password)
        revoke(session, user.id)
        audit(session, request, 'account.password_changed', user.id)
    response.delete_cookie(COOKIE, path='/')
    return {'ok': True}

@router.get('/accounts')
def accounts(request: Request):
    with Session(request.app.state.engine) as session:
        return [public_account(row) for row in session.scalars(select(Account).order_by(Account.created_at))]

@router.post('/accounts', status_code=201)
def create_account(body: CreateAccount, request: Request):
    try:
        with transaction(request.app.state.engine) as session:
            user = Account(phone=body.phone, name=body.name.strip(), role=body.role, password_hash=password_hash(body.password))
            session.add(user)
            session.flush()
            audit(session, request, 'account.created', user.id)
            return public_account(user)
    except IntegrityError:
        raise HTTPException(409, '此手机号已存在，请编辑原账号。') from None

@router.patch('/accounts/{account_id}')
def edit_account(account_id: str, body: EditAccount, request: Request):
    try:
        with transaction(request.app.state.engine) as session:
            user = session.get(Account, account_id)
            if not user:
                raise HTTPException(404, '账号不存在。')
            if user.role == 'admin' and user.enabled and (body.role != 'admin' or not body.enabled):
                others = session.scalar(select(Account.id).where(Account.id != user.id, Account.role == 'admin', Account.enabled.is_(True)).limit(1))
                if others is None:
                    raise HTTPException(409, '至少需要保留一位启用中的管理员。')
            for key, value in body.model_dump().items():
                setattr(user, key, value.strip() if isinstance(value, str) else value)
            revoke(session, user.id)
            session.flush()
            audit(session, request, 'account.updated', user.id)
            return public_account(user)
    except IntegrityError:
        raise HTTPException(409, '此手机号已存在，请使用其他手机号。') from None

@router.post('/accounts/{account_id}/password')
def reset_password(account_id: str, body: ResetPassword, request: Request):
    with transaction(request.app.state.engine) as session:
        user = session.get(Account, account_id)
        if not user:
            raise HTTPException(404, '账号不存在。')
        user.password_hash = password_hash(body.password)
        user.failures = 0
        user.locked_until = 0
        revoke(session, user.id)
        audit(session, request, 'account.password_reset', user.id)
    return {'ok': True}

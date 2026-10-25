"""Streaming, checksummed data archives. Restore is an offline CLI into an empty target."""
import base64
import hashlib
import json
import os
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy import Date, DateTime, LargeBinary, func, inspect, select, text
from sqlalchemy.orm import Session

from .database import Base, AuditEvent
from .people import PeopleRoute, add_audit, timestamp, utc_now

FORMAT = 'nia-data-v1'
router = APIRouter(prefix='/api/maintenance', tags=['本地维护'], route_class=PeopleRoute)


def encode(value):
    if isinstance(value, bytes): return {'__bytes__': base64.b64encode(value).decode('ascii')}
    if isinstance(value, (datetime, date)): return value.isoformat()
    raise TypeError('不支持的数据类型')


def snapshot(engine, destination):
    manifest = {'format': FORMAT, 'created_at': timestamp(utc_now()), 'tables': {}}
    # Snapshot isolation across the entire export, including originals and audio.
    with engine.connect() as raw:
        connection = raw.execution_options(isolation_level='REPEATABLE READ') if engine.dialect.name == 'postgresql' else raw
        if engine.dialect.name == 'sqlite': connection.exec_driver_sql('BEGIN')
        else: connection.begin()
        try:
            with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for table in Base.metadata.sorted_tables:
                    hasher = hashlib.sha256(); count = 0
                    with archive.open(table.name+'.jsonl', 'w', force_zip64=True) as target:
                        for row in ([] if table.name in {'login_sessions', 'login_attempts'} else connection.execution_options(stream_results=True).execute(select(table)).mappings()):
                            encoded = (json.dumps(dict(row), default=encode, ensure_ascii=False, separators=(',', ':'))+'\n').encode()
                            hasher.update(encoded); target.write(encoded); count += 1
                    manifest['tables'][table.name] = {'rows': count, 'sha256': hasher.hexdigest(), 'columns': list(table.columns.keys())}
                archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
        finally: connection.rollback()
    Path(destination).chmod(0o600)
    return manifest


def archive_lines(stream):
    while True:
        line = stream.readline(128*1024*1024+1)
        if not line: return
        if len(line) > 128*1024*1024: raise ValueError('单条备份记录过大')
        yield line


def verify_archive(source):
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        expected = {table.name+'.jsonl' for table in Base.metadata.sorted_tables} | {'manifest.json'}
        # 0.5.0 archives predate the optional presentation-data ledger.
        for optional in ('demo_datasets', 'accounts', 'login_sessions', 'login_attempts', 'analysis_templates', 'assistant_conversations', 'assistant_messages'):
            if optional+'.jsonl' not in names: expected.discard(optional+'.jsonl')
        if len(names) != len(set(names)) or set(names) != expected: raise ValueError('备份文件条目或表版本不匹配')
        if archive.getinfo('manifest.json').file_size > 128*1024: raise ValueError('备份清单过大')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('format') != FORMAT or {name+'.jsonl' for name in manifest.get('tables', {})} != expected-{'manifest.json'}: raise ValueError('备份格式或表结构不匹配')
        for table in Base.metadata.sorted_tables:
            if table.name not in manifest['tables']: continue
            metadata = manifest['tables'][table.name]
            columns = list(table.columns.keys())
            legacy_account = table.name == 'accounts' and metadata.get('columns') == [name for name in columns if name not in {'department', 'notes'}]
            if metadata.get('columns') != columns and not legacy_account: raise ValueError('备份列结构不匹配')
            digest = hashlib.sha256(); count = 0
            with archive.open(table.name+'.jsonl') as stream:
                for line in archive_lines(stream):
                    digest.update(line); count += 1
            if count != metadata.get('rows') or digest.hexdigest() != metadata.get('sha256'): raise ValueError('备份完整性校验失败')
        return manifest


def restore_empty(engine, source):
    manifest = verify_archive(source)
    existing = set(inspect(engine).get_table_names())
    if existing - set(Base.metadata.tables): raise ValueError('目标包含其他应用表，拒绝恢复')
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        if engine.dialect.name == 'postgresql':
            for table in Base.metadata.sorted_tables: connection.execute(text(f'LOCK TABLE "{table.name}" IN ACCESS EXCLUSIVE MODE'))
        elif engine.dialect.name == 'sqlite':
            # Take the writer lock before emptiness checks and all inserts.
            connection.exec_driver_sql('BEGIN IMMEDIATE')
        for table in Base.metadata.sorted_tables:
            if connection.scalar(select(func.count()).select_from(table)): raise ValueError('目标数据库非空，拒绝覆盖；请选择全新恢复目录/数据库')
        with zipfile.ZipFile(source) as archive:
            for table in Base.metadata.sorted_tables:
                if table.name not in manifest['tables'] or table.name in {'login_sessions', 'login_attempts'}: continue
                with archive.open(table.name+'.jsonl') as stream:
                    for line in archive_lines(stream):
                        row = json.loads(line)
                        if set(row) != set(manifest['tables'][table.name]['columns']): raise ValueError('备份记录字段不匹配')
                        if table.name == 'accounts':
                            row.setdefault('department', '')
                            row.setdefault('notes', '')
                        if set(row) != set(table.columns.keys()): raise ValueError('备份记录字段不匹配')
                        for column in table.columns:
                            value = row[column.name]
                            if value is None: continue
                            if isinstance(column.type, LargeBinary):
                                if not isinstance(value, dict) or set(value) != {'__bytes__'}: raise ValueError('二进制备份格式错误')
                                row[column.name] = base64.b64decode(value['__bytes__'], validate=True)
                            elif isinstance(column.type, DateTime): row[column.name] = datetime.fromisoformat(value)
                            elif isinstance(column.type, Date): row[column.name] = date.fromisoformat(value)
                        connection.execute(table.insert().values(**row))
        if engine.dialect.name == 'sqlite' and connection.exec_driver_sql('PRAGMA foreign_key_check').first(): raise ValueError('备份关联完整性校验失败')
        if engine.dialect.name == 'postgresql':
            connection.execute(text("SELECT setval(pg_get_serial_sequence('audit_events','id'), COALESCE((SELECT MAX(id) FROM audit_events), 1), EXISTS(SELECT 1 FROM audit_events))"))
    return manifest


@router.get('')
def status(request: Request):
    with Session(request.app.state.engine) as session:
        counts = {table.name: session.scalar(select(func.count()).select_from(table)) for table in Base.metadata.sorted_tables}
        events = session.scalars(select(AuditEvent).where(AuditEvent.action.in_(['maintenance.exported', 'migration.imported'])).order_by(AuditEvent.id.desc()).limit(10)).all()
        return {'counts': counts, 'format': FORMAT, 'environment': request.app.state.active_settings.environment.mode, 'events': [{'action': row.action, 'created_at': timestamp(row.created_at), 'details': row.details} for row in events], 'restore': '离线恢复到全新目录或空数据库；不覆盖在线数据。'}


@router.post('/backup')
def backup(request: Request):
    descriptor, path = tempfile.mkstemp(prefix='nia-backup-', suffix='.zip'); os.close(descriptor)
    try:
        manifest = snapshot(request.app.state.engine, path)
        with Session(request.app.state.engine) as session:
            add_audit(session, 'maintenance.exported', tables=len(manifest['tables']), rows=sum(table['rows'] for table in manifest['tables'].values()))
            session.commit()
        return FileResponse(path, filename=f'nia-data-{utc_now():%Y%m%d-%H%M%S}.zip', media_type='application/zip', background=BackgroundTask(os.unlink, path))
    except BaseException:
        Path(path).unlink(missing_ok=True); raise

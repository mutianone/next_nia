#!/usr/bin/env python3
"""macOS/Linux offline backup, verification, restore and legacy staging."""
import argparse
import csv
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, URL
from backend.app.main import create_app  # registers all model metadata; does not start services
from backend.app.maintenance import snapshot, verify_archive, restore_empty
from backend.app.database import application_engine
from backend.app.config import SettingsStore


def legacy_export(source, output):
    source = source.resolve()
    connection = sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    items = []
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ['qian_zheng_data', 'chu_ru_jing_data']:
            if table not in tables: continue
            for row in connection.execute(f'SELECT * FROM "{table}"'):
                raw = dict(row); identifier = str(raw.get('id', ''))
                # Legacy dates have ambiguous semantics; preserve them as source material.
                # Nationality has no reliable field in the legacy schema and must be filled by a reviewer.
                person = {'name': str(raw.get('name') or ''), 'nationality': '', 'document_type': 'other', 'document_number': str(raw.get('idCard') or ''), 'gender': {'男':'male', '女':'female'}.get(raw.get('gender'), 'unknown'), 'verification': 'pending', 'source': f'旧系统 {table} / {identifier}，待人工核对', 'notes': '原日期未自动映射：签证到期日、要求离境日不等于停留截止日、实际离境日。'}
                items.append({'legacy_id': table+':'+identifier, 'person': person, 'stays': [], 'original': raw})
        if not items: raise ValueError('未找到可迁移的旧系统签证/出入境记录')
        if len(items) > 500: raise ValueError('旧记录超过 500 条，请先按批次导出；本工具不截断资料')
        write_new(output, json.dumps({'source_key': 'legacy-ai-system', 'items': items}, ensure_ascii=False, indent=2).encode())
        return {'staged': len(items), 'review_required': True}
    finally: connection.close()


def write_new(path, content):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'wb') as stream: stream.write(content)


def main():
    parser = argparse.ArgumentParser(description='境智本地数据维护（不覆盖既有数据）')
    sub = parser.add_subparsers(dest='action', required=True)
    backup = sub.add_parser('backup'); backup.add_argument('--data-dir', type=Path, required=True); backup.add_argument('--output', type=Path, required=True)
    verify = sub.add_parser('verify'); verify.add_argument('archive', type=Path)
    restore = sub.add_parser('restore'); restore.add_argument('archive', type=Path); restore.add_argument('--new-data-dir', type=Path, required=True)
    restore_pg = sub.add_parser('restore-postgres'); restore_pg.add_argument('archive', type=Path); restore_pg.add_argument('--config-dir', type=Path, required=True)
    legacy = sub.add_parser('legacy-stage'); legacy.add_argument('--source', type=Path, required=True); legacy.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    engine = None
    try:
        if args.action == 'verify': result = verify_archive(args.archive)
        elif args.action == 'legacy-stage': result = legacy_export(args.source, args.output)
        elif args.action == 'restore':
            verify_archive(args.archive)
            args.new_data_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
            store = SettingsStore(args.new_data_dir); settings = store.load()
            engine = application_engine(settings, args.new_data_dir)
            result = restore_empty(engine, args.archive)
            (args.new_data_dir/'test.sqlite3').chmod(0o600)
        elif args.action == 'restore-postgres':
            if not any((args.config_dir/name).is_file() for name in ('settings.sqlite3', 'settings.json')): raise ValueError('配置目录必须已存在')
            settings = SettingsStore(args.config_dir).load()
            if settings.environment.mode != 'production': raise ValueError('必须指定已配置 PostgreSQL 的正式环境配置目录')
            engine = application_engine(settings, args.config_dir); result = restore_empty(engine, args.archive)
        else:
            if not any((args.data_dir/name).is_file() for name in ('settings.sqlite3', 'settings.json')): raise ValueError('数据目录必须已有配置')
            settings = SettingsStore(args.data_dir).load(); engine = application_engine(settings, args.data_dir)
            # Reserve a private file exclusively; never truncate an existing archive.
            write_new(args.output, b'')
            try: result = snapshot(engine, args.output)
            except BaseException: args.output.unlink(missing_ok=True); raise
        print(json.dumps({'ok': True, 'tables': len(result.get('tables', {})), 'staged': result.get('staged')}, ensure_ascii=False))
    except Exception:
        print('操作未完成：请检查参数、文件格式、校验和、目标是否全新及数据库状态；既有业务库不会被覆盖。', file=sys.stderr)
        return 1
    finally:
        if engine: engine.dispose()
    return 0

if __name__ == '__main__': raise SystemExit(main())

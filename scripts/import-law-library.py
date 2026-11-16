#!/usr/bin/env python3
"""Import an explicitly described local law library; dry-run unless --apply."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from backend.app.config import SettingsStore
from backend.app.database import application_engine
from backend.app.knowledge import KnowledgeDocument, Metadata, _extract, _build_chunks, _validate_publish, _audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    records = json.loads(args.manifest.read_text())
    prepared = []
    for record in records:
        filename = record['filename']
        if Path(filename).name != filename:
            raise ValueError('Filename must be a basename')
        raw = (args.source / filename).read_bytes()
        if not raw or len(raw) > 10 * 1024 * 1024:
            raise ValueError('Invalid document size')
        metadata = Metadata(**record['metadata'])
        identifier = str(uuid4())
        sections, chunks = _build_chunks(identifier, _extract(raw, 'docx'))
        prepared.append((filename, raw, metadata, identifier, sections, chunks))
    settings = SettingsStore(args.data_dir).load()
    engine = application_engine(settings, args.data_dir)
    report = []
    try:
        with Session(engine) as session:
            if engine.dialect.name == 'sqlite':
                session.execute(text('BEGIN IMMEDIATE'))
            else:
                session.execute(text('SELECT pg_advisory_xact_lock(731984205)'))
            for filename, raw, metadata, identifier, sections, chunks in prepared:
                digest = hashlib.sha256(raw).hexdigest()
                existing = session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.series_key == metadata.series_key, KnowledgeDocument.version == metadata.version))
                if existing:
                    if existing.sha256 != digest:
                        raise ValueError(f'Existing version has different content: {filename}')
                    report.append({'filename': filename, 'result': 'already_exists', 'id': existing.id})
                    continue
                _validate_publish(session, metadata)
                session.add(KnowledgeDocument(id=identifier, **metadata.model_dump(), filename=filename, file_type='docx', size_bytes=len(raw), raw_file=raw, sha256=digest, chunk_count=len(chunks)))
                session.flush()
                session.add_all(sections)
                session.flush()
                session.add_all(chunks)
                _audit(session, 'knowledge.local_library_imported', identifier, len(chunks))
                session.flush()
                report.append({'filename': filename, 'result': 'imported' if args.apply else 'preview', 'id': identifier, 'chunks': len(chunks), 'sha256': digest})
            if args.apply:
                session.commit()
            else:
                session.rollback()
    finally:
        engine.dispose()
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()

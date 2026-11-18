"""Persist human source choices as a new version, preserving both originals."""
from copy import deepcopy
from typing import Literal
from uuid import uuid4
from fastapi import HTTPException, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import records as r, record_asr_results as ar
from .record_qa_draft_api import Write, actor


class Choice(Write):
    confirmed: Literal[True]
    choices: list[Literal['siliconflow', 'volcengine']] = Field(min_length=1, max_length=500)


class Judge(Write):
    confirmed: Literal[True]


@r.router.post('/{record_id}/dual-review/{run_id}/recommend')
def recommend(record_id: str, run_id: str, body: Judge, request: Request):
    actor(request)
    from .record_asr_judge import recommend as choose
    settings = r.settings_copy(request)
    with Session(request.app.state.engine) as session:
        record = r.get_record(session, record_id)
        r.mutable(record, body.expected_revision)
        run = session.get(ar.RecognitionRun, run_id)
        if not run or run.record_id != record_id or run.provider != 'dual':
            raise HTTPException(404, '未找到双引擎识别版本。')
        output = {'segments': [deepcopy(s.data) for s in ar.sentences(session, run)]}
    chosen = choose(output, settings)
    with Session(request.app.state.engine) as session:
        r.mutable(r.get_record(session, record_id), body.expected_revision)
    # Suggestions are ephemeral for old versions; neither originals nor body change.
    return {'revision': body.expected_revision, 'groups': [s['dual_review'] for s in chosen['segments']]}


@r.router.get('/{record_id}/dual-review')
def material(record_id: str, request: Request):
    with Session(request.app.state.engine) as session:
        row = r.get_record(session, record_id)
        runs = session.scalars(select(ar.RecognitionRun).where(ar.RecognitionRun.record_id == record_id,
                               ar.RecognitionRun.provider == 'dual'))
        return {'revision': row.revision, 'runs': [
            {'id': run.id, 'audio_id': run.audio_id, 'applied': run.applied,
             'groups': [s.data['dual_review'] for s in ar.sentences(session, run)]}
            for run in runs]}


@r.router.post('/{record_id}/dual-review/{run_id}/prefill')
def prefill_saved(record_id: str, run_id: str, body: Write, request: Request):
    from .record_dual_prefill import prefill
    actor(request)
    with r.write_session(request.app.state.engine) as session:
        record = r.get_record(session, record_id, True)
        r.mutable(record, body.expected_revision)
        run = session.get(ar.RecognitionRun, run_id)
        if not run or run.record_id != record_id or run.provider != 'dual':
            raise HTTPException(404, '未找到双引擎识别版本。')
        if not run.applied or run.interval_map:
            raise HTTPException(409, '请选择已应用的上传录音版本。')
        originals = ar.sentences(session, run)
        current = [s for s in r.ordered_segments(session, record_id) if s.audio_id == run.audio_id]
        if len(originals) != len(current) or any(
            s.id != o.id or s.text != o.data['text'] or s.speaker != 'unknown'
            or s.start_ms != o.data.get('start_ms') or s.end_ms != o.data.get('end_ms')
            for s, o in zip(current, originals)
        ):
            raise HTTPException(409, '正文已有修订，无法按旧版本预填。')
        updated, fields = prefill(record.template_snapshot, [(o.id, o.data['dual_review']) for o in originals])
        if fields:
            record.template_snapshot = updated
            r.version(session, record, 'dual_template_prefilled')
            r.audit(session, 'dual_template_prefilled', record_id=record_id, field_ids=fields)
        return r.detail(session, record)


@r.router.post('/{record_id}/dual-review/{run_id}')
def apply(record_id: str, run_id: str, body: Choice, request: Request):
    who = actor(request)
    with r.write_session(request.app.state.engine) as session:
        record = r.get_record(session, record_id, True)
        r.mutable(record, body.expected_revision)
        run = session.get(ar.RecognitionRun, run_id)
        if not run or run.record_id != record_id or run.provider != 'dual':
            raise HTTPException(404, '未找到双引擎识别版本。')
        originals = ar.sentences(session, run)
        if len(originals) != len(body.choices):
            raise HTTPException(422, '请逐段核对并选择完整结果。')
        existing = r.ordered_segments(session, record_id)
        old_ids = {s.id for s in originals}
        if session.scalar(select(ar.RecognitionRun.id).where(ar.RecognitionRun.record_id == record_id,
                          ar.RecognitionRun.origin_key == run.origin_key, ar.RecognitionRun.applied.is_(True),
                          ar.RecognitionRun.id != run.id)):
            raise HTTPException(409, '此音频已有其他已应用版本，已保留；不自动覆盖。')
        affected = [s for s in existing if s.audio_id == run.audio_id]
        original_by_id = {s.id: s.data for s in originals}
        if any(s.id not in old_ids or s.text != original_by_id[s.id]['text'] or s.speaker != 'unknown'
               or s.start_ms != original_by_id[s.id].get('start_ms') or s.end_ms != original_by_id[s.id].get('end_ms')
               for s in affected):
            raise HTTPException(409, '此音频已有其他版本或人工修订，已保留；请核对原有正文，不自动覆盖。')
        clip = session.get(r.AudioClip, run.audio_id)
        if not clip or run.interval_map:
            raise HTTPException(409, '此版本不是可核对的上传录音。')
        parts = []
        for sentence, choice in zip(originals, body.choices):
            candidate = next((c for c in sentence.data['dual_review']['candidates'] if c['provider'] == choice), None)
            if candidate is None:
                raise HTTPException(422, '所选服务未返回此时间段，不能丢弃该段原文。')
            for original in candidate['segments']:
                part = deepcopy(original)
                part['speaker'] = 'unknown'
                # Namespace by engine AND its original run/channel. Never equate engine IDs.
                identity = [choice, original.get('recognition_run_id'), original.get('channel_id'), original.get('speaker_id')]
                part['speaker_id'] = (choice[:1] + ar.fingerprint(identity)[:20]) if original.get('speaker_id') is not None else None
                part['dual_source'] = {'run_id': run.id, 'group': sentence.data['dual_review']['group'],
                                       'provider': choice, 'original': deepcopy(original)}
                if clip.duration_ms is not None and part.get('end_ms') is not None and part['end_ms'] > clip.duration_ms:
                    part.update(start_ms=None, end_ms=None, words=None, timestamp_source='UNKNOWN', voice_review=True)
                parts.append(part)
        if any(len(p['text']) > r.LIMITS['max_segment_chars'] for p in parts) or len(parts) + len(existing) - len(affected) > r.LIMITS['max_segments'] or sum(len(p['text']) for p in parts) + sum(len(s.text) for s in existing if s not in affected) > r.LIMITS['max_total_text_chars']:
            raise HTTPException(422, '核对结果超过正文上限，未修改。')
        output = {'provider': 'dual_reviewed', 'recognition_run_id': str(uuid4()),
                  'text': '\n'.join(p['text'] for p in parts), 'segments': parts,
                  'raw_result': {'source_run_id': run.id, 'choices': body.choices, 'confirmed_by': who,
                                 'confirmed_at': r.utc_now().isoformat()}}
        reviewed = ar.save_result(session, record_id, run.origin_key, output, run.audio_id)
        index = min((s.position for s in affected), default=len(existing))
        for s in affected:
            session.delete(s)
        run.applied = False
        session.flush()
        ar.project(session, reviewed, record, run.audio_id, None, index)
        # Keep surrounding audio in its original order after one-to-many replacement.
        added = [session.get(r.RecordSegment, s.id) for s in ar.sentences(session, reviewed)]
        ordered = [s for s in existing if s not in affected]
        ordered[index:index] = [s for s in added if s is not None]
        for position, s in enumerate(ordered):
            s.position = position
        if record.template_snapshot.get('dual_prefill'):
            # Both originals contained each provisional answer. Rebind its evidence
            # to the human-selected projection, including one-to-many replacements.
            replacements = {}
            for old, choice in zip(originals, body.choices):
                group = old.data['dual_review']['group']
                replacements[old.id] = [s.id for s, part in zip(added, parts) if part['dual_source']['group'] == group]
            template = record.template_snapshot
            sources = dict(template.get('sources', {}))
            for field in template['dual_prefill']:
                sources[field] = [new for sid in sources.get(field, []) for new in replacements.get(sid, [sid])]
            record.template_snapshot = {**template, 'sources': sources}
        r.version(session, record, 'dual_asr_reviewed')
        r.audit(session, 'dual_asr_reviewed', record_id=record_id, source_run_id=run.id, actor_id=who)
        return r.detail(session, record)

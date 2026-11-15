"""Immutable ASR versions and source metadata; no speaker-role inference.

Tables are additive. Recognition sentences are originals, RecordSegment remains
an editable projection. Provider identifiers are scoped to a recognition run.
"""
from copy import deepcopy
import hashlib
import json
import re
from uuid import uuid4, uuid5, NAMESPACE_URL
from sqlalchemy import JSON, String, ForeignKey, select
from sqlalchemy.orm import Mapped, mapped_column, object_session
from .database import Base


class RecognitionRun(Base):
    __tablename__ = 'record_recognition_runs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(36), index=True)
    origin_key: Mapped[str] = mapped_column(String(100), index=True)
    audio_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(30))
    provider_task_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    digest: Mapped[str] = mapped_column(String(64))
    raw_result: Mapped[dict] = mapped_column(JSON, deferred=True)
    interval_map: Mapped[list] = mapped_column(JSON)
    applied: Mapped[bool] = mapped_column(default=False)


class RecognitionSentence(Base):
    __tablename__ = 'record_recognition_sentences'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('record_recognition_runs.id'), index=True)
    ordinal: Mapped[int] = mapped_column()
    data: Mapped[dict] = mapped_column(JSON)
    projection: Mapped[dict] = mapped_column(JSON, default=dict)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _times(item):
    start, end = item.get('begin_time'), item.get('end_time')
    if start is None and end is None:
        return None, None
    if type(start) is not int or type(end) is not int or start < 0 or end <= start or end > 86400000:
        raise ValueError('invalid timestamp')
    return start, end


def normalize_funasr(payload, task_id, scope=''):
    """Preserve provider fields, including empty speaker/word information."""
    from .records_ai import RecordAIError
    from .documents import plain_text
    try:
        tracks = payload['transcripts']
        if not isinstance(tracks, list) or not tracks:
            raise ValueError('missing transcripts')
        run_id = str(uuid5(NAMESPACE_URL, 'fun-asr:' + scope + ':' + task_id))
        parts = []
        for ti, track in enumerate(tracks):
            if not isinstance(track, dict):
                raise ValueError('invalid transcript')
            sentences = track['sentences']
            if not isinstance(sentences, list):
                raise ValueError('invalid sentences')
            previous = -1
            for si, sentence in enumerate(sentences):
                if not isinstance(sentence, dict):
                    raise ValueError('invalid sentence')
                text = sentence['text']
                if not isinstance(text, str) or not text.strip():
                    raise ValueError('invalid text')
                plain_text(text)
                start, end = _times(sentence)
                if start is not None:
                    if start < previous:
                        raise ValueError('out of order')
                    previous = start
                speaker = sentence.get('speaker_id')
                if speaker is not None and (isinstance(speaker, bool) or not isinstance(speaker, (int, str)) or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', str(speaker))):
                    raise ValueError('invalid speaker')
                words = sentence.get('words')
                if words is not None:
                    if not isinstance(words, list):
                        raise ValueError('invalid words')
                    word_previous = -1
                    for word in words:
                        if not isinstance(word, dict):
                            raise ValueError('invalid word')
                        ws, we = _times(word)
                        if not isinstance(word.get('text'), str):
                            raise ValueError('invalid word')
                        if ws is not None:
                            if ws < word_previous or (start is not None and (ws < start or we > end)):
                                raise ValueError('word outside sentence')
                            word_previous = ws
                parts.append({'speaker': 'unknown', 'text': text, 'start_ms': start, 'end_ms': end,
                              'sentence_id': sentence.get('sentence_id'), 'speaker_id': speaker,
                              'channel_id': track.get('channel_id'), 'words': deepcopy(words),
                              'provider_position': [ti, si], 'recognition_run_id': run_id,
                              'raw_result_ref': run_id, 'timestamp_source': 'PROVIDER' if start is not None else 'UNKNOWN',
                              'timestamp_unit': 'ms', 'is_final': True})
        if not parts:
            raise ValueError('result limits')
        return {'provider': 'aliyun', 'model': 'fun-asr', 'recognition_run_id': run_id,
                'provider_task_id': task_id, 'raw_result': deepcopy(payload),
                'text': '\n'.join(p['text'] for p in parts), 'segments': parts}
    except (KeyError, TypeError, ValueError):
        raise RecordAIError(502, 'Fun-ASR分句或时间信息无效，原始结果未应用。') from None


def map_interval(start, end, intervals):
    """A span crossing a gap stays multiple spans; never fabricate one offset."""
    if start is None or end is None:
        return []
    result = []
    for item in intervals:
        rate = item['sample_rate']
        left = max(start, item['batch_start_sample'] * 1000 / rate)
        right = min(end, item['batch_end_sample'] * 1000 / rate)
        if right > left:
            offset = (item['session_start_sample'] - item['batch_start_sample']) * 1000 / rate
            result.append({'start_ms': left + offset, 'end_ms': right + offset,
                           'batch_start_ms': left, 'batch_end_ms': right})
    return result


def save_result(session, record_id, origin_key, output, audio_id=None, intervals=None):
    """Idempotent per actual provider run and business origin. Never overwrite."""
    base_id = output.get('recognition_run_id') or str(uuid4())
    run_id = str(uuid5(NAMESPACE_URL, f'{record_id}:{origin_key}:{base_id}'))
    raw = output.get('raw_result') or deepcopy(output)
    mapping = deepcopy(intervals or [])
    last_end = 0
    for interval in mapping:
        if any(type(interval.get(k)) is not int for k in ('batch_start_sample','batch_end_sample','session_start_sample','sample_rate')):
            raise ValueError('invalid sample map')
        if interval['sample_rate'] <= 0 or interval['batch_start_sample'] != last_end or interval['batch_end_sample'] <= last_end or interval['session_start_sample'] < 0:
            raise ValueError('non-contiguous input map')
        last_end = interval['batch_end_sample']
    digest = fingerprint({'raw': raw, 'segments': output.get('segments'), 'intervals': mapping})
    old = session.get(RecognitionRun, run_id)
    if old:
        if old.digest != digest:
            from .records_ai import RecordAIError
            raise RecordAIError(409, '同一识别运行返回不同内容，未覆盖原始识别结果。')
        return old
    run = RecognitionRun(id=run_id, record_id=record_id, origin_key=origin_key, audio_id=audio_id,
                         provider=output.get('provider', 'legacy'), provider_task_id=output.get('provider_task_id'),
                         digest=digest, raw_result=raw, interval_map=mapping, applied=False)
    session.add(run)
    session.flush()
    parts = output.get('segments') or [{'text': output['text'], 'speaker': 'unknown'}]
    for i, part in enumerate(parts):
        data = deepcopy(part)
        data.update(recognition_run_id=run_id, raw_result_ref=run_id)
        speaker = data.get('speaker_id')
        data['speaker_scope'] = f'{run_id}:{speaker}' if speaker is not None else None
        data['session_intervals'] = map_interval(data.get('start_ms'), data.get('end_ms'), mapping)
        data['word_session_intervals'] = [map_interval(w.get('begin_time'), w.get('end_time'), mapping) for w in data.get('words') or []]
        data['timestamp_unit'] = 'ms'
        data.setdefault('timestamp_source', 'UNKNOWN')
        session.add(RecognitionSentence(id=str(uuid5(NAMESPACE_URL, f'{run_id}:{i}')), run_id=run_id, ordinal=i, data=data))
    session.flush()
    return run


def sentences(session, run):
    return list(session.scalars(select(RecognitionSentence).where(RecognitionSentence.run_id == run.id).order_by(RecognitionSentence.ordinal)))


def origin_run(session, record_id, origin):
    return session.scalar(select(RecognitionRun).where(RecognitionRun.record_id == record_id, RecognitionRun.origin_key == origin))


def analysis_inputs(session, run):
    return [{'segment_id': item.id, 'text': item.data['text'], 'speaker': 'unknown',
             'speaker_id': item.data.get('speaker_id'), 'recognition_run_id': run.id} for item in sentences(session, run)]


def coverage(data, run):
    spans=data.get('session_intervals', [])
    if spans:
        return spans
    # Missing sentence timing: conservatively review overlapping source windows.
    # These are audio-window bounds, not invented sentence timestamps.
    return [{'start_ms':m['session_start_sample']*1000/m['sample_rate'],
             'end_ms':(m['session_start_sample']+m['batch_end_sample']-m['batch_start_sample'])*1000/m['sample_rate']}
            for m in run.interval_map]


def project(session, run, record, audio_id, transcription_id, position):
    """Apply first version only. Later versions remain reviewable originals."""
    from . import records as r
    if run.applied:
        return 0
    if session.scalar(select(RecognitionRun.id).where(RecognitionRun.record_id == run.record_id,
                      RecognitionRun.origin_key == run.origin_key, RecognitionRun.applied.is_(True))):
        return 0
    if session.scalar(select(r.RecordSegment.id).where(r.RecordSegment.record_id == record.id, r.RecordSegment.audio_id == audio_id)):
        return 0  # Includes historical/manual projections with no sidecar.
    previous = list(session.scalars(select(RecognitionSentence).join(RecognitionRun).where(
        RecognitionRun.record_id == record.id, RecognitionRun.applied.is_(True))))
    count = 0
    for item in sentences(session, run):
        data = item.data
        overlaps = []
        for old in previous:
            # Different capture sessions have unrelated relative clocks.
            old_run = session.get(RecognitionRun, old.run_id)
            if not run.origin_key.startswith('capture:') or old_run.origin_key.split(':')[:2] != run.origin_key.split(':')[:2]:
                continue
            for a in coverage(data,run):
                for b in coverage(old.data,old_run):
                    if max(a['start_ms'], b['start_ms']) < min(a['end_ms'], b['end_ms']):
                        overlaps.append({'run_id': old.run_id, 'segment_id': old.id, 'start_ms':max(a['start_ms'],b['start_ms']), 'end_ms':min(a['end_ms'],b['end_ms'])})
        # Preserve overlapping original but never duplicate it into the body.
        if overlaps:
            item.projection={'status':'overlap_review_required','overlaps':overlaps}
            continue
        session.add(r.RecordSegment(id=item.id, record_id=record.id, position=position + count,
                    audio_id=audio_id, transcription_id=transcription_id, source='asr', speaker='unknown',
                    text=data['text'], start_ms=data.get('start_ms'), end_ms=data.get('end_ms')))
        item.projection={'status':'applied','segment_id':item.id}
        count += 1
    run.applied = True
    run.audio_id = audio_id
    session.flush()
    return count


def metadata(row):
    if not hasattr(row, '_sa_instance_state'):
        return {}
    session = object_session(row)
    item = session.get(RecognitionSentence, row.id) if session else None
    if item is None:
        return {}
    data = item.data
    return {k: data.get(k) for k in ('recognition_run_id', 'raw_result_ref', 'sentence_id', 'speaker_id',
             'speaker_scope', 'channel_id', 'words', 'timestamp_unit', 'session_intervals', 'word_session_intervals')} | {
             'asr_timestamp_source': data.get('timestamp_source', 'UNKNOWN'),
             'asr_original_text': data['text'], 'asr_start_ms': data.get('start_ms'), 'asr_end_ms': data.get('end_ms')}


def purge_audio(session, record_id, audio_id):
    from .record_qa_drafts import purge_audio as purge_drafts
    purge_drafts(session, record_id, audio_id)
    for run in session.scalars(select(RecognitionRun).where(RecognitionRun.record_id == record_id, RecognitionRun.audio_id == audio_id)):
        for item in sentences(session, run):
            session.delete(item)
        session.flush()
        session.delete(run)

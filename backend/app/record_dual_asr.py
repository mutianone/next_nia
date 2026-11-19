"""Two independent recognizers, conservative time windows, explicit human choice.

Agreement is not ground truth. No cross-provider speaker identity is inferred.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import re
from uuid import uuid4

MODEL = 'volc.seedasr.auc + Qwen/Qwen3-ASR-1.7B'
QWEN = 'Qwen/Qwen3-ASR-1.7B'
ENGINES = ('siliconflow', 'volcengine')


def configurations(settings):
    result = {}
    for name in ENGINES:
        config = settings.model_copy(deep=True)
        config.ai.speech.provider = name
        if name == 'siliconflow':
            config.ai.providers.siliconflow.asr_model = QWEN
        result[name] = config
    return result


def ready(settings):
    from .records_ai import provider_config, RecordAIError
    try:
        for config in configurations(settings).values():
            provider_config(config, 'asr')
        return True
    except RecordAIError:
        return False


def normalized(text):
    text = re.sub(r'\[说话人 [A-Za-z0-9_-]+\]', '', text)
    return ''.join(c.lower() for c in text if c.isalnum())


def local_verification(results):
    """Corroborate a complete cloud transcript using independent local words."""
    import re
    from .record_voice_alignment import aligned_segments
    first, second = (results.get(name) for name in ENGINES)
    if not first or not second or normalized(first['text']) == normalized(second['text']):
        return None
    evidence = first.get('local_verification')
    if not evidence:
        return None
    local_text = evidence['text']
    for output in (first, second):
        # Preserve negation and numeric token boundaries, including decimals.
        if normalized(local_text) != normalized(output['text']):
            continue
        if re.findall(r'[-+−]?\d+(?:[.．]\d+)?', local_text) != re.findall(r'[-+−]?\d+(?:[.．]\d+)?', output['text']):
            continue
        parts = aligned_segments(output['text'], evidence['words'], evidence['decisions'], paired_dialogue=True)
        return {'provider': 'local', 'text': output['text'], 'segments': parts,
                'verified_provider': output['provider']}
    return None


def combine(results, errors=None):
    errors = errors or {}
    nodes = []
    verified = local_verification(results)
    candidates_by_engine = {**results, **({'local': verified} if verified else {})}
    for engine in (*ENGINES, 'local'):
        output = candidates_by_engine.get(engine)
        if not output:
            continue
        for i, part in enumerate(output.get('segments') or [{'text': output['text']} ]):
            nodes.append((engine, i, deepcopy(part)))
    if not nodes:
        from .records_ai import RecordAIError
        raise RecordAIError(502, '两个语音服务均未返回可用结果，请检查配置或稍后重试。')
    timed = all(type(p.get('start_ms')) is int and type(p.get('end_ms')) is int
                and 0 <= p['start_ms'] < p['end_ms'] for _, _, p in nodes)
    # Connected overlapping intervals allow one-to-many sentence alignment.
    # Missing times mean a whole-recording comparison, never guessed offsets.
    windows = []
    if timed:
        for node in sorted(nodes, key=lambda n: (n[2]['start_ms'], n[2]['end_ms'])):
            start, end = node[2]['start_ms'], node[2]['end_ms']
            if windows and start <= windows[-1]['end'] + 250:
                windows[-1]['nodes'].append(node)
                windows[-1]['end'] = max(windows[-1]['end'], end)
            else:
                windows.append({'start': start, 'end': end, 'nodes': [node]})
    else:
        windows = [{'start': None, 'end': None, 'nodes': nodes}]
    parts = []
    for i, window in enumerate(windows):
        candidates = []
        for engine in (*ENGINES, 'local'):
            source = [p for e, _, p in sorted(window['nodes'], key=lambda n: n[1]) if e == engine]
            if source:
                candidates.append({'provider': engine, 'text': '\n'.join(p['text'] for p in source), 'segments': source,
                                   **({'verified_provider': verified['verified_provider']} if engine == 'local' else {})})
        local = next((c for c in candidates if c['provider'] == 'local'), None)
        candidates = [c for c in candidates if c['provider'] != 'local']
        if not candidates:
            continue
        agrees = len(candidates) == 2 and normalized(candidates[0]['text']) == normalized(candidates[1]['text'])
        status = 'untimed' if not timed else 'single' if len(candidates) == 1 else 'text_agrees' if agrees else 'conflict'
        parts.append({'speaker': 'unknown', 'speaker_id': None, 'text': candidates[0]['text'],
                      'start_ms': window['start'], 'end_ms': window['end'],
                      'timestamp_source': 'UNKNOWN', 'voice_review': True,
                      'dual_review': {'group': i, 'status': status, 'candidates': candidates, 'errors': errors,
                                      **({'local_verification': local} if local else {})}})
    return {'provider': 'dual', 'model': MODEL, 'recognition_run_id': str(uuid4()),
            'text': '\n'.join(p['text'] for p in parts), 'segments': parts,
            'raw_result': {'engines': deepcopy(results), 'errors': errors, 'algorithm': 'time-overlap-v1'}}


def transcribe(raw, file_format, mime_type, settings, *, request_key='', speaker_count=None):
    from .records_ai import transcribe_audio, provider_config, RecordAIError
    from .record_voice_audio import available
    from .record_media import normalize_for_asr, RecordMediaError
    configs = configurations(settings)
    # Validate both before sending either; a missing key is not silent fallback.
    for config in configs.values():
        provider_config(config, 'asr')
    try:
        prepared = normalize_for_asr(raw, file_format, level_pair=speaker_count == 2)
    except RecordMediaError as exc:
        raise RecordAIError(422, str(exc)) from None
    local = available()
    results, errors = {}, {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {name: pool.submit(transcribe_audio, raw, file_format, mime_type, config,
                    request_key=request_key, speaker_count=(speaker_count if name == 'volcengine' or local else None),
                    _prepared=prepared)
                   for name, config in configs.items()}
        for name, future in futures.items():
            try:
                results[name] = future.result()
            except RecordAIError as exc:
                errors[name] = {'status': exc.status, 'message': exc.message}
            except Exception:
                # Never persist provider exception strings, keys or signed URLs.
                errors[name] = {'status': 502, 'message': '识别异常，未取得此服务的结果。'}
    from .record_asr_judge import recommend
    return recommend(combine(results, errors), settings)


def require_reviewed(session, record_id):
    from sqlalchemy import select
    from fastapi import HTTPException
    from .record_asr_results import RecognitionRun
    if session.scalar(select(RecognitionRun.id).where(RecognitionRun.record_id == record_id,
                      RecognitionRun.provider == 'dual', RecognitionRun.applied.is_(True))):
        raise HTTPException(409, '请先完成“双引擎核对”，再确认角色和回填模板。')

"""Doubao AUC 2.0: private TOS upload, bounded polling and immutable native turns."""
from copy import deepcopy
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

import httpx
import tos

from .schemas import reveal

BASE = 'https://openspeech.bytedance.com/api/v3/auc/bigmodel'
MODEL = 'volc.seedasr.auc'
MAX_WAIT = 180
MAX_RESPONSE = 2 * 1024 * 1024


def ready(config):
    return bool(config.tos_bucket and all(reveal(getattr(config, name))
        for name in ('api_key', 'tos_access_key', 'tos_secret_key')))


def _cache(identity, value=None, delete=False):
    path = Path(os.environ.get('CRJ_VOLC_ASR_CACHE', str(Path(__file__).resolve().parents[1] / 'data' / 'volc-asr-tasks.sqlite3')))
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=10) as db:
        path.chmod(0o600)
        db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL, created REAL NOT NULL)')
        db.execute('DELETE FROM tasks WHERE created < ?', (time.time() - 23 * 3600,))
        if delete:
            db.execute('DELETE FROM tasks WHERE id=?', (identity,))
        elif value is not None:
            db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?,?)', (identity, json.dumps(value), time.time()))
        else:
            row = db.execute('SELECT data FROM tasks WHERE id=?', (identity,)).fetchone()
            return json.loads(row[0]) if row else None


def normalize(payload, task_id, scope):
    from .record_asr_results import normalize_funasr
    from .records_ai import RecordAIError
    try:
        utterances = payload['result']['utterances']
        if not isinstance(utterances, list) or not 0 < len(utterances) <= 10000:
            raise ValueError()
        tracks = []
        previous = -1
        for i, utterance in enumerate(utterances):
            start = utterance.get('start_time')
            if type(start) is int:
                if start < previous:
                    raise ValueError()
                previous = start
            additions = utterance.get('additions') or {}
            words = utterance.get('words')
            if words is not None:
                words = [dict(w, begin_time=w.get('start_time'), end_time=w.get('end_time')) for w in words]
                # AUC can return zero-duration words. Keep the raw timestamps, but
                # expose no invented interval in the normalized evidence.
                for w in words:
                    if type(w['begin_time']) is int and w['begin_time'] >= 0 and w['begin_time'] == w['end_time']:
                        w['begin_time'] = w['end_time'] = None
            tracks.append({'channel_id': additions.get('channel_id'), 'sentences': [{
                'sentence_id': i, 'speaker_id': additions.get('speaker'), 'text': utterance['text'],
                'begin_time': utterance.get('start_time'), 'end_time': utterance.get('end_time'), 'words': words}]})
        result = normalize_funasr({'transcripts': tracks}, task_id, scope, provider='volcengine', model=MODEL)
        full_text = payload['result']['text']
        if not isinstance(full_text, str) or re.sub(r'[\W_]+', '', full_text) != re.sub(r'[\W_]+', '', result['text']):
            raise ValueError('incomplete utterances')
        duration = payload.get('audio_info', {}).get('duration')
        if duration is not None and (type(duration) is not int or duration <= 0 or
            any(p['end_ms'] is not None and p['end_ms'] > duration for p in result['segments'])):
            raise ValueError('timestamp outside audio')
        result['raw_result'] = deepcopy(payload)
        for i, part in enumerate(result['segments']):
            part['provider_position'] = ['result', 'utterances', i]
            part['word_boundaries'] = True
            part['voice_review'] = (part['speaker_id'] is None or part['start_ms'] is None or not part['words']
                or any(w['begin_time'] is None or w['end_time'] is None for w in part['words']))
        if len(result['text']) > 20000:
            raise ValueError()
        return result
    except (KeyError, ValueError, TypeError, AttributeError):
        raise RecordAIError(502, '火山转写分句或时间信息无效，未采用不完整结果。') from None


def transcribe(raw, fmt, mime, config, *, request_key='', speaker_count=None):
    from .records_ai import RecordAIError
    if not ready(config):
        raise RecordAIError(409, '请在系统设置中填写火山语音 API Key、TOS 私有桶、地域和上传凭据，未发送原音。')
    if fmt not in ('mp3', 'wav', 'ogg') or not raw or len(raw) >= 512 * 1024 * 1024:
        raise RecordAIError(422, '火山转写音频格式或大小不符合要求。')
    secrets = [reveal(getattr(config, k)) for k in ('api_key', 'tos_access_key', 'tos_secret_key')]
    api_key, ak, sk = secrets
    ssd = '300' if speaker_count and speaker_count > 5 else '200'
    scope = hashlib.sha256(('\0'.join(secrets + [config.tos_region, config.tos_bucket])).encode()).hexdigest()
    identity = hashlib.sha256((scope + '\0' + request_key + '\0' + str(speaker_count) + '\0v1').encode() + raw).hexdigest()
    deadline = time.monotonic() + MAX_WAIT
    signer = tos.TosClientV2(ak, sk, endpoint=f'https://tos-{config.tos_region}.volces.com', region=config.tos_region)

    def signed(method, object_key, headers=None):
        return signer.pre_signed_url(method, config.tos_bucket, object_key, expires=24 * 3600, header=headers).signed_url

    def request(client, url, *, headers=None, body=None, content=None, method='POST', storage=False):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RecordAIError(504, '火山任务仍在处理，已提交任务可稍后重试查询。')
        with client.stream(method, url, headers=headers, json=body, content=content, timeout=min(60, remaining)) as response:
            if response.status_code in (401, 403):
                raise RecordAIError(400, 'TOS 桶或上传凭据权限无效。' if storage else '火山语音凭据或模型权限无效，请检查同一项目下的 API Key 和模型开通状态。')
            if response.status_code == 429:
                raise RecordAIError(429, '火山请求频率受限，请稍后重试。')
            if response.status_code not in (200, 201, 204):
                raise RecordAIError(502, 'TOS 文件请求失败，请检查桶名和地域。' if storage else '火山接口请求失败，原音已保留。')
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_RESPONSE:
                    raise RecordAIError(502, '火山响应超过大小限制。')
            if storage:
                return None
            code = response.headers.get('X-Api-Status-Code')
            decoded = json.loads(data) if data else {}
            if any(secret in json.dumps(decoded, ensure_ascii=False) for secret in secrets):
                raise ValueError()
            return code, decoded

    def cleanup(client, task):
        try:
            request(client, signed(tos.HttpMethodType.Http_Method_Delete, task['object']), method='DELETE', storage=True)
        except (RecordAIError, httpx.HTTPError, tos.exceptions.TosError):
            logging.getLogger(__name__).warning('volc_asr_temporary_object_cleanup_failed')

    try:
        with httpx.Client(follow_redirects=False, trust_env=False) as client:
            task = _cache(identity)
            if not task:
                task = {'id': str(uuid4()), 'object': f'next-nia-asr/{uuid4().hex}.{fmt}'}
                upload_headers = {'x-tos-acl': 'private', 'Content-Type': mime}
                request(client, signed(tos.HttpMethodType.Http_Method_Put, task['object'], upload_headers),
                        headers=upload_headers, content=raw, method='PUT', storage=True)
                headers = {'X-Api-Key': api_key, 'X-Api-Resource-Id': MODEL, 'X-Api-Request-Id': task['id'], 'X-Api-Sequence': '-1'}
                code, _ = request(client, BASE + '/submit', headers=headers, body={
                    'user': {'uid': 'next-nia'},
                    'audio': {'url': signed(tos.HttpMethodType.Http_Method_Get, task['object']), 'format': fmt},
                    'request': {'model_name': 'bigmodel', 'enable_speaker_info': True, 'show_utterances': True,
                                'ssd_version': ssd, 'enable_ddc': False, 'enable_punc': True}})
                if code != '20000000':
                    cleanup(client, task)
                    raise RecordAIError(502, '火山未接受转写任务，请检查模型权限、音频和账户额度。')
                _cache(identity, task)
            headers = {'X-Api-Key': api_key, 'X-Api-Resource-Id': MODEL, 'X-Api-Request-Id': task['id']}
            while True:
                code, payload = request(client, BASE + '/query', headers=headers, body={})
                if code == '20000000':
                    cleanup(client, task)
                    return normalize(payload, task['id'], scope)
                if code not in ('20000001', '20000002'):
                    cleanup(client, task)
                    _cache(identity, delete=True)
                    message = '火山未检测到人声，未生成文字。' if code == '20000003' else '火山转写任务失败，请检查音频和模型权限；原音已保留。'
                    raise RecordAIError(502, message)
                if time.monotonic() + 2 >= deadline:
                    raise RecordAIError(504, '火山任务仍在处理，任务已保留，可稍后重试查询。')
                time.sleep(2)
    except RecordAIError:
        raise
    except httpx.TimeoutException:
        raise RecordAIError(504, '火山请求超时；已提交任务可在重试时继续查询。') from None
    except (httpx.HTTPError, tos.exceptions.TosError, ValueError, KeyError, TypeError, sqlite3.Error, OSError):
        raise RecordAIError(502, '火山接口响应、存储配置或任务保存异常，原音已保留。') from None
    finally:
        signer.close()

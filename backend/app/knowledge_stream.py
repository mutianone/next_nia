"""Bounded upstream SSE; only complete, citation-checked claims leave this module."""
import json
import re
import time
import httpx
from .knowledge_ai import CHAT_URLS, KnowledgeAIError, prepare_answer, validate_answer, attach_analysis_basis, MAX_CLAIMS, MAX_ANSWER_CHARS


def complete_claims(raw):
    prefix = re.match(r'^\s*\{\s*"claims"\s*:\s*\[', raw)
    if not prefix:
        return []
    position, claims = prefix.end(), []
    while position < len(raw):
        while position < len(raw) and raw[position] in ' \t\r\n,':
            position += 1
        if position >= len(raw) or raw[position] == ']':
            break
        try:
            claim, position = json.JSONDecoder().raw_decode(raw, position)
        except ValueError:
            break
        claims.append(claim)
        if len(claims) > MAX_CLAIMS:
            raise KnowledgeAIError(502, 'AI 结论条目过多，请缩小问题范围后重试。')
    return claims


def stream_answer(question, sources, settings, *, analysis=False):
    provider, model, key, payload = prepare_answer(question, sources, settings, analysis=analysis)
    payload['stream'] = True
    raw, pending, size, emitted, finish, done = '', '', 0, 0, None, False
    started = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(90.0, connect=5.0), follow_redirects=False, trust_env=False) as client:
            with client.stream('POST', CHAT_URLS[provider], headers={'Authorization': f'Bearer {key}', 'Accept': 'text/event-stream'}, json=payload) as response:
                if response.status_code != 200:
                    raise KnowledgeAIError(502, 'AI 请求未成功，请检查接口配置、额度或稍后重试。')
                for block in response.iter_text():
                    size += len(block.encode('utf-8'))
                    if size > 2 * 1024 * 1024 or time.monotonic() - started > 120:
                        raise KnowledgeAIError(502, 'AI 响应超过时间或长度限制。')
                    pending += block
                    while '\n' in pending:
                        line, pending = pending.split('\n', 1)
                        if not line.startswith('data:'):
                            continue
                        value = line[5:].strip()
                        if value == '[DONE]':
                            done = True
                            break
                        packet = json.loads(value)
                        if 'error' in packet:
                            raise ValueError('upstream error')
                        choices = packet.get('choices', [])
                        if not choices:
                            continue
                        choice = choices[0]
                        delta = choice.get('delta', {}).get('content') or ''
                        if not isinstance(delta, str):
                            raise ValueError('bad delta')
                        raw += delta
                        if len(raw) > MAX_ANSWER_CHARS or key in raw:
                            raise ValueError('invalid output')
                        finish = choice.get('finish_reason') or finish
                        claims = complete_claims(raw)
                        if len(claims) > emitted:
                            verified = validate_answer(json.dumps({'claims': claims}, ensure_ascii=False), sources, provider, model, key)
                            emitted = len(claims)
                            if analysis: verified = attach_analysis_basis(verified)
                            yield {'type': 'partial', 'answer': verified}
                    if done:
                        break
                if not done or finish != 'stop':
                    raise KnowledgeAIError(502, 'AI 回答中断或未完整生成，请重试。')
                verified = validate_answer(raw, sources, provider, model, key)
                yield {'type': 'complete', 'answer': attach_analysis_basis(verified) if analysis else verified}
    except KnowledgeAIError:
        raise
    except httpx.TimeoutException:
        raise KnowledgeAIError(504, 'AI 响应超时，请重试。') from None
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError, RecursionError):
        raise KnowledgeAIError(502, 'AI 流式回答未通过完整性或引用检查，请重试。') from None

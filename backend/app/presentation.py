"""Offline presentation processing. Never represents a request to a cloud provider."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from .records_ai import RecordAIError

ASSETS = Path(__file__).resolve().parents[1] / 'assets'


def enabled(request):
    return bool(getattr(request.app.state, 'presentation', False))


@lru_cache(maxsize=1)
def recordings():
    scripts = json.loads((ASSETS / 'interviews.json').read_text(encoding='utf-8'))
    return {hashlib.sha256((ASSETS / f'{name}.wav').read_bytes()).hexdigest(): rows for name, rows in scripts.items()}


def transcribe(raw):
    rows = recordings().get(hashlib.sha256(raw).hexdigest())
    if rows is None:
        raise RecordAIError(422, '此音频尚无本地识别资料，请选择资料库中的配套录音，或在人工修订中录入文字。原始音频已保留。')
    labels = {'interviewer': '询问人', 'interviewee': '被询问人'}
    return {'text': '\n'.join(f'{labels.get(role, role)}：{text}' for role, text in rows),
            'segments': [{'speaker': role, 'text': text} for role, text in rows],
            'provider': 'local', 'model': '语音资料整理'}


def analyze(inputs):
    # Extractive only: every visible fact is copied from a current input segment.
    answers = [row for row in inputs if row.get('speaker') == 'interviewee'] or inputs
    facts = []
    for row in answers[:12]:
        content = row['text']
        label = ('证件与材料' if any(word in content for word in ('护照', '证明', '回执', '证件')) else
                 '待补充信息' if any(word in content for word in ('补充', '核实', '确认后')) else
                 '事由与安排' if any(word in content for word in ('住', '工作', '学习', '行程', '返程')) else '询问内容')
        facts.append({'label': label, 'value': content[:2000], 'segment_ids': [row['segment_id']]})
    issues = [f'需结合材料核对：{row["text"][:300]}' for row in inputs if any(word in row['text'] for word in ('补充', '核实', '确认返程', '需要再'))][:6]
    summary = '\n\n'.join(f'{fact["label"]}：{fact["value"]}' for fact in facts)[:6000]
    return {'summary': summary, 'key_facts': facts, 'issues': issues, 'provider': 'local', 'model': '文字要素整理'}


def answer(sources):
    if not sources:
        return {'status': 'insufficient', 'answer': '当前资料未检索到足够依据，请调整关键词或补充已发布资料。', 'sources': [], 'claims': [], 'provider': 'local', 'model': '法规资料整理'}
    sources = sources[:4]
    claims = [{'text': f'【{source["article"] or source["title"]}】{source["text"]}', 'source_ids': [source['id']]} for source in sources]
    return {'status': 'answered', 'answer': '\n\n'.join(item['text'] for item in claims),
            'claims': claims, 'sources': sources, 'provider': 'local', 'model': '法规资料整理'}

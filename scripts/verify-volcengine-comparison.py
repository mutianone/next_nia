"""Recompute real Volcengine results against frozen Qwen evidence, offline."""
import json
from pathlib import Path
import runpy

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / 'docs/volcengine-asr-evidence-20261118'
baseline = runpy.run_path(str(REPO / 'scripts/verify-asr-comparison.py'))
norm, distance = baseline['normalize'], baseline['distance']


def read(path):
    return json.loads(path.read_text())


def measure(cases, calls, summary, manifest):
    result = dict(edits=0, characters=0, stable=0, short_text_present=0, short_separate_exact=0,
                  saved=0, faithful=0, blank=0, failed=0, supplement_complete=0, cloud_calls=0)
    assert {c['id'] for c in cases} == set(manifest) and len(cases) == 12
    for c in cases:
        ident, asr, score = c['id'], c['asr'], c['score']
        source = manifest[ident]
        assert asr['sha256'] == source['sha256']
        full = ''.join(t['text'] for t in source['refs'])
        answer = ''.join(t['text'] for t in source['refs'] if t['role'] == '答')
        edits = distance(norm(full), norm(asr['text']))
        chars = len(norm(full))
        cut = score['cut']  # Frozen manual boundary; mixed turns deliberately stay unknown.
        assert cut is None or 0 < cut < len(asr['segments'])
        segments = asr['segments']
        text = '\n'.join(s['text'] for s in segments[cut:]) if cut is not None else None
        qids = {s.get('speaker_id') for s in segments[:cut]} if cut is not None else set()
        aids = {s.get('speaker_id') for s in segments[cut:]} if cut is not None else set()
        stable = cut is not None and len(qids) == len(aids) == 1 and None not in qids | aids and qids != aids
        exact = text is not None and norm(text) == norm(answer)
        assert (edits, chars, stable, exact, text) == (score['character_edits'], score['characters'],
            score['stable'], score['exact_answer'], score['transcribed_answer'])
        result['edits'] += edits; result['characters'] += chars; result['stable'] += stable
        if ident in summary['short_cases']:
            result['short_text_present'] += norm(answer) in norm(asr['text'])
            result['short_separate_exact'] += exact
        call = calls[ident]; value = call.get('value')
        result['cloud_calls'] += call['cloud_calls']
        if call['status'] != 200:
            assert value is None and call['error']
            result['failed'] += 1
        elif value is None:
            result['blank'] += 1
        else:
            assert value == text and value in call['candidate_values'] and value in call['rendered_text']
            assert any(f['value'] == value and f.get('evidence') for f in call['facts'])
            result['saved'] += 1
            result['faithful'] += norm(value) == norm(answer)
            result['supplement_complete'] += ident in summary['supplement_cases']
    return result


def main():
    summary = read(ROOT / 'summary.json'); cases = read(ROOT / 'cases.json')
    old = REPO / 'docs/asr-model-evidence-20261118'
    manifest = {c['id']: c for c in read(old / 'manifest.json')}
    calls = {c['id']: c['actual_template'] for c in cases}
    assert measure(cases, calls, summary, manifest) == summary['volcengine']
    for c in cases:
        asr = c['asr']
        assert asr['provider'] == 'volcengine' and asr['model'] == 'volc.seedasr.auc'
        raw = asr['raw_result']['result']
        assert raw['text'] == asr['text'] or norm(raw['text']) == norm(asr['text'])
        assert [s['text'] for s in raw['utterances']] == [s['text'] for s in asr['segments']]
        assert [s['additions'].get('speaker') for s in raw['utterances']] == [s['speaker_id'] for s in asr['segments']]
    qwen = read(old / 'qwen3-asr.json')['cases']
    qwen_calls = {c['id']: c['after'] for c in read(REPO / 'docs/record-fill-optimization-evidence-20261118/results.json')['qwen3-asr']}
    assert measure(qwen, qwen_calls, summary, manifest) == summary['qwen3_asr']
    assert [c['id'] for c in cases if c['score']['cut'] is None] == summary['merged_cases']
    overruns = {}
    for c in cases:
        duration = round((manifest[c['id']]['end'] - manifest[c['id']]['start']) * 1000)
        overrun = max(s['end_ms'] for s in c['asr']['segments']) - duration
        if overrun > 0:
            overruns[c['id']] = overrun
            assert calls[c['id']]['status'] == 422
    assert overruns == summary['timestamp_overruns_ms']
    print('Verified 12 real Volcengine ASR and template results, plus 12 frozen Qwen results.')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()

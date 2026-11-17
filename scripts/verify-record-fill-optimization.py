"""Check frozen real-route results against the original ASR comparison evidence."""
import json
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / 'docs'
ROOT = DOCS / 'record-fill-optimization-evidence-20261118'


def read(path):
    return json.loads(path.read_text())


def norm(text):
    return ''.join(c.lower() for c in text if c.isalnum())


def main():
    results = read(ROOT / 'results.json')
    expected = read(ROOT / 'summary.json')
    count = 0
    for model, cases in results.items():
        original = {c['id']: c for c in read(DOCS / 'asr-model-evidence-20261118' / f'{model}.json')['cases']}
        assert len({c['id'] for c in cases}) == len(cases)
        for case in cases:
            source, after = original[case['id']], case['after']
            assert case['before'] == source['actual_template']
            assert after['status'] == 200 and after['stage'] == 'analysis'
            assert case['reference_answer'] == source['score']['reference_answer']
            value = after.get('value')
            if value is not None:
                assert value == source['score']['transcribed_answer']
                assert value in after['candidate_values'] and value in after['rendered_text']
                assert any(f['value'] == value and f.get('evidence') for f in after['facts'])
                assert after['cloud_calls'] > 0
            else:
                assert after['cloud_calls'] == 0 and not after['candidate_values']
                assert after['quality_checks'][0]['code'] == 'no_qa_options'
            faithful = value is not None and norm(value) == norm(case['reference_answer'])
            assert faithful == case['faithful_after']
            count += 1
    qwen = results['qwen3-asr']
    assert {c['id'] for c in qwen} == {f'{i:02}' for i in range(1, 13)}
    metrics = {}
    for stage in ('before', 'after'):
        metrics[stage] = dict(
            saved=sum(c[stage].get('value') is not None for c in qwen),
            faithful=sum(c[stage].get('value') is not None and norm(c[stage]['value']) == norm(c['reference_answer']) for c in qwen),
            failed=sum(c[stage]['status'] != 200 for c in qwen),
            blank=sum(c[stage]['status'] == 200 and c[stage].get('value') is None for c in qwen))
    metrics['after'].update(cloud_calls=sum(c['after']['cloud_calls'] for c in qwen),
                            local_only_cases=[c['id'] for c in qwen if c['after']['cloud_calls'] == 0])
    assert metrics == expected
    assert any(c['code'] == 'paused_reply' for c in results['xingchen'][0]['after']['quality_checks'])
    assert any(c['code'] == 'tentative_answer' for c in results['sensevoice'][0]['after']['quality_checks'])
    assert count == 14
    print('Verified 14 real template workflow results; all saved values preserve complete ASR answers.')
    print(json.dumps(metrics, ensure_ascii=False))


if __name__ == '__main__':
    main()

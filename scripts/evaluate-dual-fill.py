"""Reference-assisted review replay, NOT an automatic best-model selector.

Uses the real review/save/analyze APIs in temporary SQLite databases and the
configured text model. Public corpus only; no production records are changed.
"""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import time
import wave
from pytest import MonkeyPatch
from backend.tests.test_record_capture import client
from backend.tests.test_records import create, upload, editable, segments, analyze
from backend.app import records as r, record_asr_results as ar, records_ai, record_jobs
from backend.app.config import SettingsStore

# After inspecting the public reference turns, prefer separate short answers and
# complete supplementary turns. This is an explicit offline review decision,
# never a deployed rule or a claim that the system can know ground truth.
DECISIONS = {
    '01': ('siliconflow', 1), '02': ('siliconflow', 2), '03': ('volcengine', 1),
    '04': ('siliconflow', 4), '05': ('siliconflow', 6), '06': ('siliconflow', 3),
    '07': ('volcengine', 1), '08': ('volcengine', 4), '09': ('siliconflow', 6),
    '10': ('volcengine', 3), '11': ('volcengine', 2), '12': ('volcengine', 2),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--settings-dir', type=Path, required=True)
    parser.add_argument('--corpus', type=Path, default=Path('output/新增真人对话实测-20261118'))
    parser.add_argument('--output', type=Path, default=Path('output/双引擎核对实测-20261118'))
    args = parser.parse_args()
    settings = SettingsStore(args.settings_dir).load().model_copy(deep=True)
    assert settings.ai.external_permissions.analysis
    calls = []
    original_post = records_ai.bounded_post
    def tracked(*a, **kw):
        calls.append(1)
        return original_post(*a, **kw)
    records_ai.bounded_post = tracked
    for identifier, (engine, cut) in DECISIONS.items():
        destination = args.output/'data'/f'{identifier}-api.json'
        if destination.exists():
            print(identifier, 'retained template replay', flush=True); continue
        before = len(calls)
        recognition = json.loads((args.output/'data'/f'{identifier}.json').read_text())
        print(identifier, 'TEMPLATE START', engine, flush=True)
        with tempfile.TemporaryDirectory() as directory, MonkeyPatch.context() as mp, contextmanager(client.__wrapped__)(Path(directory), mp) as api:
            api.app.state.store.current = settings
            audio = args.corpus/'audio'/f'{identifier}.wav'
            with wave.open(str(audio)) as wav:
                duration = round(wav.getnframes()*1000/wav.getframerate())
            response = upload(api, create(api), audio.read_bytes(), audio.name, duration)
            assert response.status_code == 201, response.text
            row = response.json(); root = f"/api/records/{row['id']}"; audio_id = row['audio_clips'][0]['id']
            with r.write_session(api.app.state.engine) as session:
                record = session.get(r.RecordSession, row['id'])
                record.template_snapshot = {'id':'dual-eval', 'name':'真人对话核对', 'version':'1', 'values':{},
                    'fields':[{'id':'q001','label':[ref['text'] for ref in recognition['refs'] if ref['role']=='问'][-1]}]}
                run = ar.save_result(session, record.id, 'audio:'+audio_id, recognition, audio_id)
                ar.project(session, run, record, audio_id, None, 0); run_id = run.id
            row = api.get(root).json()
            pending = analyze(api, row)
            assert pending.status_code == 409, pending.text
            assert '双引擎' in pending.json()['detail']
            choices = [engine] * len(recognition['segments'])
            response = api.post(root+'/dual-review/'+run_id, json={'expected_revision':row['revision'],'confirmed':True,'choices':choices})
            assert response.status_code == 200, response.text
            row = response.json()
            selected_segments = row['segments']
            response = segments(api, row, [editable(s, speaker='interviewer' if i<cut else 'interviewee') for i,s in enumerate(selected_segments)])
            assert response.status_code == 200, response.text
            row = response.json()
            response = analyze(api, row)
            for _ in range(2):
                if response.status_code not in (429,503,504):
                    break
                time.sleep(60); response = analyze(api, row)
            saved = api.get(root).json()
            result = {'id':identifier,'selection_method':'reference-assisted manual replay; no automatic selection',
                'choices':choices, 'cut':cut, 'pending_fill_status':pending.status_code,
                'status':response.status_code,'value':saved['template_snapshot'].get('values',{}).get('q001'),
                'sources':saved['template_snapshot'].get('sources',{}), 'candidates':saved.get('candidates',[]),
                'quality_checks':saved['template_snapshot'].get('quality_checks',[]),
                'selected_segments':selected_segments, 'reference_answer':''.join(x['text'] for x in recognition['refs'] if x['role']=='答'),
                'analysis_provider':settings.ai.default_provider,'analysis_model':getattr(settings.ai.providers,settings.ai.default_provider).chat_model,
                'external_analysis_calls':len(calls)-before}
            if response.status_code != 200:
                result['error'] = response.json().get('detail')
            destination.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(identifier, result['status'], 'saved' if result['value'] else 'blank', 'calls',result['external_analysis_calls'], flush=True)
        if len(calls)>before:
            time.sleep(20)


if __name__ == '__main__':
    main()

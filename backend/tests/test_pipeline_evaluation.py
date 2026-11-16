import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('evaluator',Path(__file__).parents[2]/'scripts/evaluate_record_pipeline.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def fixture():
    gold={'utterances':[{'id':'q','role':'OFFICER','speaker':'a','text':'你叫什么？'},{'id':'a','role':'INTERVIEWEE','speaker':'b','text':'金子。'}],
          'qa':[{'question_sources':['q'],'answer_sources':['a']}], 'fields':[{'field_id':'name','answer_sources':['a'],'value':'金子。'}],
          'adhesion_issues':[],'word_text':'问：你叫什么？\n答：金子。'}
    return {'case_id':'synthetic-gold','data_class':'synthetic','expected':gold,'reviewed':gold}


def test_manual_success_never_becomes_automatic_accuracy():
    d=fixture();d['automatic']={'utterances':[{'id':'q','role':'UNKNOWN','text':'你叫什么？'}]}
    result=m.evaluate(d)['stages']
    assert result['automatic']['role_accuracy']==0 and result['automatic']['unknown_or_missing']==2
    assert result['reviewed']['role_accuracy']==1 and result['reviewed']['field_exact']['f1']==1
    assert result['reviewed']['review_seconds'] is None and result['automatic']['export_exact'] is None


def test_glued_qa_and_duplicate_fill_are_penalized():
    d=fixture();d['automatic']={**d['expected'],'qa':[{'question_sources':['q','a'],'answer_sources':[]}],'fields':d['expected']['fields']*2}
    r=m.evaluate(d)['stages']['automatic'];assert r['qa_boundaries']['f1']==0 and r['field_exact']['precision']==0.5


def test_audio_checksum_required(tmp_path):
    d=fixture();(tmp_path/'audio.wav').write_bytes(b'synthetic');d['audio']={'path':'audio.wav','sha256':'wrong'}
    with pytest.raises(ValueError):m.evaluate(d,tmp_path)


def test_unknown_ids_cannot_score():
    d=fixture();d['automatic']={'utterances':[{'id':'invented'}]}
    with pytest.raises(ValueError):m.evaluate(d)


from backend.tests.test_record_capture import client

def test_observed_http_pipeline_metrics_keep_unknown_separate(client,tmp_path):
    from copy import deepcopy
    from io import BytesIO
    from zipfile import ZipFile
    from xml.etree import ElementTree as ET
    import json,os
    from backend.tests.test_qa_drafts import seed,get,post,review_all,url
    from backend.tests.test_shadow_fill import DEFAULT,preview,review
    from backend.tests.test_fill_apply import request_body,apply
    from backend.app import records as r,record_fill_apply as service
    row,_=seed(client,texts=('你叫什么名字？','金子。','你住在哪里？','我住在测试路8号。'))
    with r.write_session(client.app.state.engine) as s:
        rec=s.get(r.RecordSession,row['id']);rec.template_id='synthetic';rec.template_snapshot={'name':'合成测试',**deepcopy(DEFAULT)};service.setting(s,True).enabled=True
    row=get(client,row,'');sentences=get(client,row,'speaker-mappings')['sentences'];ids=[x['id'] for x in sentences]
    gold={'utterances':[{'id':x['id'],'text':x['text'],'speaker':str(i%2),'role':'OFFICER' if i%2==0 else 'INTERVIEWEE'} for i,x in enumerate(sentences)],
        'qa':[{'question_sources':[ids[0]],'answer_sources':[ids[1]]},{'question_sources':[ids[2]],'answer_sources':[ids[3]]}],
        'fields':[{'field_id':'q_name','answer_sources':[ids[1]],'value':'金子。'},{'field_id':'q_address','answer_sources':[ids[3]],'value':'我住在测试路8号。'}],
        'adhesion_issues':[],'export_fragments':['金子。','我住在测试路8号。']}
    def observed(draft):
        turns={t['id']:t for t in draft['turns']}
        return {'utterances':[{'id':src['sentence_id'],'role':t['role'],'speaker':str(src['speaker_id']),'text':t['text']} for t in draft['turns'] for src in t['sources']],
            'qa':[{'question_sources':[s['sentence_id'] for tid in p['question_turn_ids'] for s in turns[tid]['sources']],
                   'answer_sources':[s['sentence_id'] for tid in p['answer_turn_ids'] for s in turns[tid]['sources']]} for p in draft['pairs']], 'fields':[]}
    automatic=observed(post(client,row,'qa-drafts/rebuild').json())
    review_all(client,row);draft=post(client,row,'qa-drafts/rebuild').json()
    response=post(client,row,f'qa-drafts/{draft["id"]}/confirm',draft_revision=draft['revision']);assert response.status_code==200;draft=response.json()
    b=preview(client,row,draft).json()
    for c in b['items']:assert review(client,row,c).status_code==200
    reviewed=observed(draft)
    reviewed['fields']=[{'field_id':c['template_field_id'],'answer_sources':[s['sentence_id'] for s in c['source_refs'] if s['role']=='INTERVIEWEE'],'value':c['answer_text']} for c in b['items']]
    assert apply(client,row,request_body(client,row,b)).status_code==200
    response=client.get(url(row,'export.docx'));assert response.status_code==200
    with ZipFile(BytesIO(response.content)) as z:
        root=ET.fromstring(z.read('word/document.xml'));reviewed['word_text']='\n'.join(x.text or '' for x in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
    report=m.evaluate({'case_id':'http-synthetic-jinzi','data_class':'synthetic','expected':gold,'automatic':automatic,'reviewed':reviewed})
    assert report['stages']['automatic']['role_accuracy']==0
    assert report['stages']['automatic']['unknown_or_missing']==4
    actual=report['stages']['reviewed'];assert actual['qa_boundaries']['f1']==1 and actual['field_exact']['f1']==1
    assert all(x==1 for x in actual['export_fragments'].values()) and actual['review_seconds'] is None
    target=Path(os.environ.get('STAGE5_EVAL_REPORT',str(tmp_path/'metrics.json')))
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2))

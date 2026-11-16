"""Three original Word templates: real HTTP apply/export/restore, synthetic sources."""
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from uuid import uuid4
import os
from pathlib import Path
import pytest
from backend.tests.test_record_capture import client
from backend.tests.test_qa_drafts import seed,get,post,url
from backend.tests.test_shadow_fill import preview,review
from backend.tests.test_fill_apply import request_body,apply
from backend.app import records as r,record_fill_apply as service
from backend.app.record_templates import CASE_TEMPLATES
from backend.app.record_word import ROOT,W


def word_fixture(client,template):
    t=deepcopy(template);family=next(f for f in t['fields'] if '家庭情况' in f['label']);first=next(f for f in t['fields'] if f['id']=='q001')
    family['allow_multiple']=True;family['question_aliases']=['刚才的家庭情况还有补充吗？']
    answer='我叫金子，父亲叫甲某，母亲叫乙某。'+''.join(f'第{i}次联系时，我询问了家人的近况，具体日期记不清。' for i in range(1,13))
    texts=(first['label'],'听明白了。',family['label'],answer,'刚才的家庭情况还有补充吗？','没有其他家庭成员需要补充。')
    row,_=seed(client,texts,(0,1,0,1,0,1))
    with r.write_session(client.app.state.engine) as s:
        rec=s.get(r.RecordSession,row['id']);rec.template_id=t['id'];rec.template_snapshot=t;service.setting(s,True).enabled=True
    row=get(client,row,'')
    for i,sentence in enumerate(get(client,row,'speaker-mappings')['sentences']):
        response=post(client,row,f'speaker-sentences/{sentence["id"]}/review',sentence_revision=0,parts=[{'start':0,'end':len(sentence['text']),'role':'OFFICER' if i%2==0 else 'INTERVIEWEE','intent':'FOLLOW_UP' if i==4 else 'QUESTION' if i%2==0 else 'ANSWER'}]);assert response.status_code==200,response.text
    d=post(client,row,'qa-drafts/rebuild').json();response=post(client,row,f'qa-drafts/{d["id"]}/confirm',draft_revision=d['revision']);assert response.status_code==200,response.text
    b=preview(client,row,response.json()).json()
    for c in b['items']:
        response=review(client,row,c,**({'action':'MODIFIED','value':'已经听明白了。'} if c['template_field_id']=='q001' else {}));assert response.status_code==200,response.text
    return row,b,t,family['id'],answer


def export(client,row):
    result=client.get(url(row,'export.docx'));assert result.status_code==200,result.text[:200] if result.status_code!=200 else ''
    return result.content


def inspect_word(content,template):
    with ZipFile(BytesIO(content)) as z,ZipFile(ROOT/template['word_asset']) as source:
        assert z.testzip() is None
        xml=ET.fromstring(z.read('word/document.xml'));old=ET.fromstring(source.read('word/document.xml'))
        assert ET.tostring(xml.find('.//'+W+'sectPr'))==ET.tostring(old.find('.//'+W+'sectPr'))
        assert [ET.tostring(x) for x in xml.iter(W+'pPr')]==[ET.tostring(x) for x in old.iter(W+'pPr')]
        # Formal export prepends one existing draft-label run; original run formatting remains intact.
        assert [ET.tostring(x) for x in xml.iter(W+'rPr')][-len(list(old.iter(W+'rPr'))):]==[ET.tostring(x) for x in old.iter(W+'rPr')]
        for name in source.namelist():
            if name!='word/document.xml':assert z.read(name)==source.read(name)
        return ''.join(xml.itertext())

@pytest.mark.parametrize('template',CASE_TEMPLATES,ids=lambda t:t['id'])
def test_original_word_apply_restore_e2e(client,template):
    row,b,t,fid,answer=word_fixture(client,template)
    assert client.get(url(row,'export.docx')).status_code==409
    from backend.app.record_word import export_original
    before=export_original(t,r.DRAFT_LABEL)
    body=request_body(client,row,b)
    # Reversing the selected IDs never reverses the confirmed QA order.
    body['candidate_ids'].reverse();result=apply(client,row,body);assert result.status_code==200,result.text
    assert apply(client,row,body).json()==result.json()
    current=get(client,row,'');values=current['template_snapshot']['values']
    assert values[fid]==answer+'\n没有其他家庭成员需要补充。'
    assert values['q001']=='已经听明白了。' and set(values)=={fid,'q001'}
    applied=export(client,row);text=inspect_word(applied,t)
    assert answer in text and text.count('没有其他家庭成员需要补充。')==1 and '已经听明白了。' in text
    history=get(client,row,'fill-apply/history')['items'][0];p=history['restore_preview'];p.pop('differences')
    response=client.post(url(row,f'fill-apply/{result.json()["application_id"]}/restore'),json={**p,'confirmed':True,'idempotency_key':str(uuid4()),'reason':'合成 Word 验收恢复'})
    assert response.status_code==200,response.text
    restored=export(client,row);assert answer not in inspect_word(restored,t)
    assert inspect_word(restored,t)==inspect_word(before,t)
    if directory:=os.environ.get('FILL_APPLY_ARTIFACT_DIR'):
        output=Path(directory);output.mkdir(parents=True,exist_ok=True)
        for suffix,content in [('before',before),('applied',applied),('restored',restored)]:
            (output/f'{t["id"]}-{suffix}.docx').write_bytes(content)

@pytest.mark.parametrize('template',CASE_TEMPLATES,ids=lambda t:t['id'])
def test_word_restore_cannot_overwrite_human_lock(client,template):
    row,b,t,fid,_=word_fixture(client,template)
    assert apply(client,row,request_body(client,row,b)).status_code==200
    current=get(client,row,'')
    response=client.patch(url(row,'template-values'),json={'expected_revision':current['revision'],'values':{fid:'人工核对后的合成家庭信息'}})
    assert response.status_code==200,response.text
    item=get(client,row,'fill-apply/history')['items'][0]
    assert item['restore_preview'] is None and '锁定' in item['restore_error']
    assert '人工核对后的合成家庭信息' in inspect_word(export(client,row),t)

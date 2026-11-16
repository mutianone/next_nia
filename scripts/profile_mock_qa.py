"""Generate fictional portrait API fixtures in an isolated temporary database.
Run: PYTHONPATH=. backend/.venv/bin/python scripts/profile_mock_qa.py /private/tmp/portrait-qa
"""
import json, sys, tempfile
from pathlib import Path
from datetime import date
from sqlalchemy.orm import Session
from backend.app.main import create_app
from backend.tests.auth_client import TestClient
from backend.app.people import Person, PersonStay
from backend.app.records import RecordSession, RecordSegment
from backend.app.profiles import ProfileRelation

out=Path(sys.argv[1] if len(sys.argv)>1 else '/private/tmp/portrait-qa');out.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='portrait-mock-') as directory:
 app=create_app(Path(directory))
 with TestClient(app) as client:
  with Session(app.state.engine) as db:
   names=[('subject','测试阿雅'),('father','测试父亲'),('mother','测试母亲'),('husband','测试丈夫'),('same1','测试小林'),('same2','测试小林'),('empty','测试空档案')]
   for i,(pid,name) in enumerate(names):
    db.add(Person(id=pid,code=f'MOCK-{i:03}',name=name,nationality='虚构测试国',gender='female' if pid in ('subject','mother') else 'unknown',document_number=f'TEST-NOT-VALID-{i:03}',source='全虚构测试数据',english_name='AYA TEST' if pid=='subject' else '',address='虚构省虚构市测试街道一百二十三号测试单元（非真实地址）' if pid=='subject' else '',phone='00000000000' if pid=='subject' else '',purpose='测试婚姻事由' if pid=='subject' else '',birth_date=date(1995,2,3) if pid=='subject' else None,document_expiry=date(2027,12,31) if pid=='subject' else None))
   db.flush()
   db.add(PersonStay(person_id='subject',entry_date=date(2025,1,2),exit_date=date(2025,3,4),source='虚构行程'))
   db.add(RecordSession(id='record-demo',title='虚构询问笔录',scenario='marriage',template_id='bride',template_snapshot={},person_id='subject'))
   db.flush()
   text='答：我的父亲叫测试父亲。\n答：我的妈妈是测试母亲。\n问：你的丈夫叫什么名字？\n答：测试丈夫。\n答：我的介绍人是测试小林。\n答：我的姐姐叫测试未建档。\n答：我的爸爸是测试父亲。\n答：我的父亲不是错误人物。'
   db.add(RecordSegment(record_id='record-demo',position=0,text=text))
   db.add(ProfileRelation(person_id='subject',target_id='father',label='父亲',source='虚构核对材料',verified=True,review_note='测试确认'))
   db.commit()
  payload={}
  for pid,_ in names:
   response=client.get('/api/profiles/'+pid);assert response.status_code==200,response.text;payload[pid]=response.json()
  rows=payload['subject']['radial']['relations']
  assert len([n for n in rows if n['role']=='父亲'])==1
  assert len(rows)==5
  assert not any(n['name']=='错误人物' for n in rows)
  assert len(next(n for n in rows if n['role']=='介绍人')['candidates'])==2
  assert next(n for n in rows if n['role']=='姐姐')['candidates']==[]
  assert any(n['target_id']=='father' for n in rows)
  assert not payload['empty']['radial']['relations']
  assert payload['subject']['person']['stays'][0]['exit_date']=='2025-03-04'
  (out/'profiles.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
  checks={'people':len(names),'relations':len(rows),'checks':['真实接口返回成功','出入境时间','已关联父亲','同名介绍人双候选','未建档姐姐','否定语句排除','空档案空关系'],'isolated_database':True}
  (out/'api-results.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2))
  print(json.dumps(checks,ensure_ascii=False))

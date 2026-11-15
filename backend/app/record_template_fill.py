"""Shadow-only service. Never writes formal segments, template values or exports."""
from uuid import uuid4,uuid5,NAMESPACE_URL
from sqlalchemy import select
from .record_fill_candidates import FillBatch,FillCandidate,FillReview
from .record_fill_validation import validate_material
from .record_template_matcher import match_pairs
from .record_asr_results import fingerprint
from .record_speaker_roles import now

def generate(session,record,draft_id,revision,actor,bindings=None,semantic=None):
    draft,pairs,template=validate_material(session,record,draft_id,revision)
    matches=match_pairs(pairs,template,bindings,semantic)
    signature=fingerprint([draft.id,draft.revision,draft.source_hash,template['digest'],bindings or {},semantic or {}])
    old=session.scalar(select(FillBatch).where(FillBatch.record_id==record.id,FillBatch.signature==signature))
    if old:return old
    batch=FillBatch(id=str(uuid4()),record_id=record.id,draft_id=draft.id,draft_revision=draft.revision,signature=signature,
                    source_digest=draft.source_hash,template=template,created_by=actor,created_at=now())
    session.add(batch);session.flush()
    for i,m in enumerate(matches):
        qa=m.pop('qa');cid=str(uuid5(NAMESPACE_URL,f'{batch.id}:{m["qa_pair_id"]}:{m["template_field_id"]}'))
        data={**m,'candidate_id':cid,'record_id':record.id,'draft_id':draft.id,'draft_revision':draft.revision,
              'template_id':template['id'],'template_version':template['version'],'source_digest':draft.source_hash,
              'question_text':qa['question'],'answer_text':qa['answer'],'source_refs':qa['sources'],'follow_up_of':qa['follow_up_of']}
        session.add(FillCandidate(id=cid,record_id=record.id,batch_id=batch.id,position=i,data=data))
    session.flush();return batch

def current(session,record,batch):
    _,_,template=validate_material(session,record,batch.draft_id,batch.draft_revision)
    if template['digest']!=batch.template['digest']:raise ValueError('模板版本或人工字段已变化，请重新生成预览。')

def latest_review(session,candidate):
    return session.scalar(select(FillReview).where(FillReview.candidate_id==candidate.id).order_by(FillReview.revision.desc()))

def candidate_json(session,c):
    reviews=list(session.scalars(select(FillReview).where(FillReview.candidate_id==c.id).order_by(FillReview.revision)))
    return {**c.data,'revision':c.revision,'review':reviews[-1].data if reviews else None,
            'reviews':[{'revision':x.revision,'actor':x.actor_id,'at':x.created_at,**x.data} for x in reviews]}

def read_batch(session,record,batch):
    try:current(session,record,batch);stale=False;reason=None
    except ValueError as e:stale=True;reason=str(e)
    items=[candidate_json(session,c) for c in session.scalars(select(FillCandidate).where(FillCandidate.batch_id==batch.id).order_by(FillCandidate.position))]
    values=record.template_snapshot.get('values',{})
    for c in items:
        field=(c['review'] or {}).get('template_field_id',c['template_field_id']);c['formal_value']=values.get(field,'')
    return {'id':batch.id,'draft_id':batch.draft_id,'draft_revision':batch.draft_revision,'created_at':batch.created_at,
            'stale':stale,'reason':reason,'fields':batch.template['fields'],'formal_values':values,'items':items}

def review(session,record,candidate_id,expected,action,field,value,note,actor):
    c=session.get(FillCandidate,candidate_id)
    if not c or c.record_id!=record.id:raise ValueError('候选不存在。')
    if c.revision!=expected:raise ValueError('审核版本冲突，请刷新。')
    batch=session.get(FillBatch,c.batch_id);current(session,record,batch)
    fields={f['id']:f for f in batch.template['fields']};field=field or c.data['template_field_id']
    if action!='REJECTED':
        if field not in fields or not fields[field]['fillable']:raise ValueError('请选择允许回填的模板字段。')
        f=fields[field]
        if f['protected']:raise ValueError('该字段已由人工确认，不允许覆盖。')
        if action=='ACCEPTED':value=c.data['answer_text']
        if not value or not value.strip():raise ValueError('接受的答案不能为空。')
        # Review explicitly selects the target. Conflicting alternatives must be rejected first.
        for other in session.scalars(select(FillCandidate).where(FillCandidate.batch_id==batch.id,FillCandidate.id!=c.id)):
            previous=latest_review(session,other)
            if previous and previous.data['status']=='REJECTED':continue
            other_field=previous.data['template_field_id'] if previous else other.data['template_field_id']
            if other_field not in fields:continue
            if other_field==field and not f['allow_multiple']:raise ValueError('该字段有其他候选，请先拒绝冲突项。')
            if other.data['qa_pair_id']==c.data['qa_pair_id']:
                g=fields[other_field]
                if not(f['allow_shared_source'] and g['allow_shared_source']) or (f['exclusive_group'] and f['exclusive_group']==g['exclusive_group']):raise ValueError('同一回答的目标字段冲突，请先拒绝冲突项。')
    c.revision+=1
    session.add(FillReview(id=str(uuid4()),candidate_id=c.id,revision=c.revision,
        data={'status':action,'template_field_id':field,'value':value if action!='REJECTED' else None,'note':note,
              'value_source':'HUMAN' if action=='MODIFIED' else 'QA_ORIGINAL'},actor_id=actor,created_at=now()))
    session.flush();return candidate_json(session,c)

def purge_draft(session,draft_id):
    for batch in session.scalars(select(FillBatch).where(FillBatch.draft_id==draft_id)):
        for c in session.scalars(select(FillCandidate).where(FillCandidate.batch_id==batch.id)):
            for review in session.scalars(select(FillReview).where(FillReview.candidate_id==c.id)):session.delete(review)
            session.delete(c)
        session.delete(batch)

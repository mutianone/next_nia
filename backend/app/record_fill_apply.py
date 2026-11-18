"""Explicit, atomic application of reviewed QA candidates. No ASR extraction."""
from copy import deepcopy
from uuid import uuid4
from sqlalchemy import JSON,String,UniqueConstraint,select
from sqlalchemy.orm import Mapped,mapped_column
from .database import Base
from . import records as r
from .record_fill_candidates import FillBatch,FillCandidate
from .record_template_fill import current,latest_review
from .record_asr_results import fingerprint
from .record_speaker_roles import now

class FillApplySetting(Base):
    __tablename__='record_fill_apply_settings'
    id:Mapped[str]=mapped_column(String(30),primary_key=True)
    enabled:Mapped[bool]=mapped_column(default=False)
    revision:Mapped[int]=mapped_column(default=0)

class FillApplication(Base):
    __tablename__='record_fill_apply_batches'
    __table_args__=(UniqueConstraint('record_id','idempotency_key'),)
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    record_id:Mapped[str]=mapped_column(String(36),index=True)
    fill_batch_id:Mapped[str]=mapped_column(String(36))
    idempotency_key:Mapped[str]=mapped_column(String(100))
    request_digest:Mapped[str]=mapped_column(String(64))
    before_revision:Mapped[int]=mapped_column()
    after_revision:Mapped[int]=mapped_column()
    operation:Mapped[str]=mapped_column(String(16))
    restore_of:Mapped[str|None]=mapped_column(String(36),nullable=True)
    applied_by:Mapped[str]=mapped_column(String(100))
    applied_at:Mapped[str]=mapped_column(String(40))
    reason:Mapped[str]=mapped_column(String(1000),default='')

class FillApplicationItem(Base):
    __tablename__='record_fill_apply_items'
    __table_args__=(UniqueConstraint('candidate_id','candidate_revision'),)
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    application_id:Mapped[str]=mapped_column(String(36),index=True)
    field_id:Mapped[str]=mapped_column(String(50))
    candidate_id:Mapped[str|None]=mapped_column(String(36),nullable=True)
    candidate_revision:Mapped[int|None]=mapped_column(nullable=True)

SETTING='structured_fill'
def setting(session,lock=False):
    if lock:
        # Ensure a singleton on both supported engines, then serialize enable/disable and apply.
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        insert=pg_insert if session.bind.dialect.name=='postgresql' else sqlite_insert
        session.execute(insert(FillApplySetting).values(id=SETTING,enabled=False,revision=0).on_conflict_do_nothing(index_elements=['id']))
    return session.scalar(select(FillApplySetting).where(FillApplySetting.id==SETTING).with_for_update()) if lock else session.get(FillApplySetting,SETTING)

def enabled(session):
    value=setting(session);return bool(value and value.enabled)

def gate(session):
    if not setting(session,True).enabled:raise ValueError('结构化正式回填未启用。')

def schema_digest(t):
    return fingerprint({k:t.get(k) for k in ('id','version','fields','word_asset','word_paragraphs')})

def field_state(t,f):
    return {'present':f in t.get('values',{}),'value':t.get('values',{}).get(f),
            'locked':f in t.get('locked_fields',[]),'sources':t.get('sources',{}).get(f),
            'structured':t.get('structured_fill_sources',{}).get(f)}

def field_versions(session,record):
    """Derive monotonic field revisions from all existing formal writer snapshots (ABA safe)."""
    versions={};previous={};ids={f['id'] for f in record.template_snapshot.get('fields',[])}
    history=session.execute(select(r.RecordVersion.revision,r.RecordVersion.snapshot['template_snapshot']).where(r.RecordVersion.record_id==record.id).order_by(r.RecordVersion.revision)).all()
    for revision,t in [*history,(record.revision,record.template_snapshot)]:
        if not isinstance(t,dict):continue
        for f in ids:
            state=field_state(t,f)
            if previous.get(f)!=state:versions[f]=revision;previous[f]=state
    return versions

def plan(session,record,batch_id,candidate_ids):
    from .record_dual_asr import require_reviewed
    require_reviewed(session, record.id)
    batch=session.get(FillBatch,batch_id)
    if not batch or batch.record_id!=record.id:raise ValueError('回填批次不存在。')
    current(session,record,batch)
    if record.template_id!=batch.template['id']:raise ValueError('笔录模板 ID 与候选不一致。')
    if not candidate_ids or len(candidate_ids)!=len(set(candidate_ids)):raise ValueError('候选列表为空或重复。')
    fields={f['id']:f for f in batch.template['fields']};chosen=[];groups={};versions=field_versions(session,record)
    for cid in candidate_ids:
        c=session.get(FillCandidate,cid)
        if not c or c.batch_id!=batch.id or c.record_id!=record.id:raise ValueError('候选不属于本次笔录或批次。')
        from .record_qa_state_machine import QAPair
        qa=session.get(QAPair,c.data['qa_pair_id'])
        if (not qa or qa.draft_id!=batch.draft_id or c.data['draft_id']!=batch.draft_id
            or c.data['draft_revision']!=batch.draft_revision or c.data['source_digest']!=batch.source_digest
            or c.data['template_id']!=batch.template['id'] or c.data['template_version']!=batch.template['version']
            or c.data['question_text']!=qa.data['question'] or c.data['answer_text']!=qa.data['answer'] or c.data['source_refs']!=qa.data['sources']):
            raise ValueError('候选与已确认问答或模板版本不一致。')
        review=latest_review(session,c)
        if not review or review.revision!=c.revision or review.data['status'] not in {'ACCEPTED','MODIFIED'}:raise ValueError('候选尚未审核通过。')
        if session.scalar(select(FillApplicationItem.id).where(FillApplicationItem.candidate_id==cid,FillApplicationItem.candidate_revision==c.revision)):raise ValueError('该候选版本已正式应用。')
        f=review.data['template_field_id'];definition=fields.get(f)
        if not definition or not definition['fillable'] or definition['protected']:raise ValueError('字段不存在、由系统填写或已人工锁定。')
        if definition['kind']=='free_qa' and record.template_snapshot.get('word_asset'):raise ValueError('原 Word 没有自由问答区域，请保留待人工处理。')
        value=review.data['value']
        if not isinstance(value,str) or not value.strip():raise ValueError('审核答案为空。')
        if review.data['status']=='ACCEPTED' and value!=c.data['answer_text']:raise ValueError('审核答案与原答不一致。')
        entry={'candidate_id':cid,'revision':c.revision,'field_id':f,'value':value,'position':c.position,
               'question':c.data['question_text'],'qa_pair_id':c.data['qa_pair_id'],'sources':c.data['source_refs'],'value_source':review.data['value_source']}
        chosen.append(entry);groups.setdefault(f,[]).append(entry)
    # Recheck cross-field constraints using effective reviewed targets, not original suggestions.
    for c in chosen:
        f=fields[c['field_id']]
        for other in chosen:
            if c is other or c['qa_pair_id']!=other['qa_pair_id']:continue
            g=fields[other['field_id']]
            if not(f['allow_shared_source'] and g['allow_shared_source']) or (f['exclusive_group'] and f['exclusive_group']==g['exclusive_group']):raise ValueError('同一问答的字段共享或互斥约束冲突。')
    differences=[]
    for f,entries in groups.items():
        if len(entries)>1 and not fields[f]['allow_multiple']:raise ValueError('该字段不允许多个答案。')
        entries.sort(key=lambda e:e['position'])
        value='\n'.join(('问：'+e['question']+'\n答：'+e['value']) if fields[f]['kind']=='free_qa' else e['value'] for e in entries)
        if len(value)>200000:raise ValueError('字段内容过长。')
        differences.append({'field_id':f,'label':fields[f]['label'],'before':record.template_snapshot.get('values',{}).get(f,''),
                            'after':value,'locked':False,'field_revision':versions[f]})
    if sum(len(d['after']) for d in differences)>500000:raise ValueError('本次回填内容过长。')
    return {'fill_batch_id':batch_id,'candidate_ids':sorted(candidate_ids),'expected_record_revision':record.revision,
            'expected_template_revision':fingerprint(record.template_snapshot),
            'expected_field_revisions':{d['field_id']:d['field_revision'] for d in differences},
            'expected_candidate_revisions':{c['candidate_id']:c['revision'] for c in chosen},'differences':differences,'entries':chosen}

def checked_preview(session,record,body):
    p=plan(session,record,body.fill_batch_id,body.candidate_ids)
    for key in ('expected_template_revision','expected_field_revisions','expected_candidate_revisions'):
        if getattr(body,key)!=p[key]:raise ValueError('模板、字段或候选版本已变化，请重新预览。')
    return p

def snapshot(session,record_id,revision):
    value=session.scalar(select(r.RecordVersion.snapshot).where(r.RecordVersion.record_id==record_id,r.RecordVersion.revision==revision))
    if not value:raise ValueError('历史修订缺失，无法安全恢复。')
    return value['template_snapshot']

def ensure_before(session,record):
    if not session.scalar(select(r.RecordVersion.id).where(r.RecordVersion.record_id==record.id,r.RecordVersion.revision==record.revision)):
        r.version(session,record,'fill_apply_baseline',initial=True)
    elif snapshot(session,record.id,record.revision)!=record.template_snapshot:raise ValueError('当前字段与历史修订不一致，请核对后再应用。')

def application_json(session,application):
    items=list(session.scalars(select(FillApplicationItem).where(FillApplicationItem.application_id==application.id)))
    return {'application_id':application.id,'fill_batch_id':application.fill_batch_id,'candidate_ids':[x.candidate_id for x in items if x.candidate_id],
            'field_ids':sorted({x.field_id for x in items}),'template_revision_before':application.before_revision,
            'template_revision_after':application.after_revision,'applied_by':application.applied_by,'applied_at':application.applied_at,
            'operation_status':'COMPLETED','operation':application.operation,'restore_of':application.restore_of,'reason':application.reason}

def existing(session,record,body,operation,restore_of=None):
    digest=fingerprint([operation,restore_of,body.model_dump()])
    old=session.scalar(select(FillApplication).where(FillApplication.record_id==record.id,FillApplication.idempotency_key==body.idempotency_key))
    if old and old.request_digest!=digest:raise ValueError('幂等键已用于其他请求。')
    return old,digest

def apply(session,record,body,actor):
    gate(session)
    old,digest=existing(session,record,body,'APPLY')
    if old:return application_json(session,old)
    r.mutable(record,body.expected_record_revision)
    p=checked_preview(session,record,body);ensure_before(session,record)
    before=record.revision;t=deepcopy(record.template_snapshot)
    t.setdefault('values',{});t.setdefault('structured_fill_sources',{});t.setdefault('sources',{})
    for d in p['differences']:
        f=d['field_id'];t['values'][f]=d['after'];t['sources'].pop(f,None)
        t['structured_fill_sources'][f]=[{k:e[k] for k in ('candidate_id','revision','qa_pair_id','sources','value_source')} for e in p['entries'] if e['field_id']==f]
    # Do not bless unrelated old values as grounded in a new transcription.
    if t.get('generated') and t.get('source_hash')!=r.segment_hash(r.ordered_segments(session,record.id)):
        raise ValueError('原正式模板来源已过期，请先核对。')
    t['generated']=True;t['source_hash']=r.segment_hash(r.ordered_segments(session,record.id));record.template_snapshot=t
    r.version(session,record,'structured_fill_applied')
    app=FillApplication(id=str(uuid4()),record_id=record.id,fill_batch_id=body.fill_batch_id,idempotency_key=body.idempotency_key,
        request_digest=digest,before_revision=before,after_revision=record.revision,operation='APPLY',applied_by=actor,applied_at=now())
    session.add(app)
    for e in p['entries']:session.add(FillApplicationItem(id=str(uuid4()),application_id=app.id,field_id=e['field_id'],candidate_id=e['candidate_id'],candidate_revision=e['revision']))
    r.audit(session,'structured_fill_application',record_id=record.id,application_id=app.id,actor_id=actor,field_count=len(p['differences']))
    session.flush();return application_json(session,app)

def restore_plan(session,record,application):
    if application.record_id!=record.id or application.operation!='APPLY':raise ValueError('无法恢复该应用记录。')
    if session.scalar(select(FillApplication.id).where(FillApplication.restore_of==application.id)):raise ValueError('该应用已经恢复过。')
    before=snapshot(session,record.id,application.before_revision);after=snapshot(session,record.id,application.after_revision)
    if schema_digest(record.template_snapshot)!=schema_digest(after):raise ValueError('模板定义已变化，不能直接恢复。')
    fields=application_json(session,application)['field_ids'];versions=field_versions(session,record);diff=[]
    for f in fields:
        if f in record.template_snapshot.get('locked_fields',[]):raise ValueError('字段已人工锁定，不能恢复覆盖。')
        if versions.get(f)!=application.after_revision or field_state(record.template_snapshot,f)!=field_state(after,f):raise ValueError('字段发生后续修改，不能恢复覆盖。')
        diff.append({'field_id':f,'before':record.template_snapshot.get('values',{}).get(f,''),'after':before.get('values',{}).get(f,'')})
    return {'expected_record_revision':record.revision,'expected_template_revision':fingerprint(record.template_snapshot),
            'expected_field_revisions':{f:versions[f] for f in fields},'differences':diff}

def restore(session,record,application_id,body,actor):
    gate(session);old,digest=existing(session,record,body,'RESTORE',application_id)
    if old:return application_json(session,old)
    r.mutable(record,body.expected_record_revision)
    app=session.get(FillApplication,application_id)
    if not app:raise ValueError('应用记录不存在。')
    p=restore_plan(session,record,app)
    if body.expected_template_revision!=p['expected_template_revision'] or body.expected_field_revisions!=p['expected_field_revisions']:raise ValueError('恢复预览已过期。')
    ensure_before(session,record);before_revision=record.revision;t=deepcopy(record.template_snapshot);old_template=snapshot(session,record.id,app.before_revision)
    for f in p['expected_field_revisions']:
        for key in ('values','sources','structured_fill_sources'):
            t.setdefault(key,{})
            if f in old_template.get(key,{}):t[key][f]=old_template[key][f]
            else:t[key].pop(f,None)
    # Retain old provenance; restoring old text never claims fresh ASR evidence.
    if old_template.get('source_hash'):t['source_hash']=old_template['source_hash']
    record.template_snapshot=t;r.version(session,record,'structured_fill_restored')
    restored=FillApplication(id=str(uuid4()),record_id=record.id,fill_batch_id=app.fill_batch_id,idempotency_key=body.idempotency_key,
        request_digest=digest,before_revision=before_revision,after_revision=record.revision,operation='RESTORE',restore_of=app.id,applied_by=actor,applied_at=now(),reason=body.reason)
    session.add(restored)
    for f in p['expected_field_revisions']:session.add(FillApplicationItem(id=str(uuid4()),application_id=restored.id,field_id=f))
    r.audit(session,'structured_fill_restoration',record_id=record.id,application_id=restored.id,actor_id=actor)
    session.flush();return application_json(session,restored)

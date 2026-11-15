"""Durable PCM capture, idempotent ingestion, leased ASR and extraction checkpoints.
No credentials or transcript text are written to logs. Original chunks stay local.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import array
import math
import io
import logging
import threading
import time
import wave
import shutil
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import Field, field_validator
from sqlalchemy import select, func, Integer, String, JSON, LargeBinary, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, Session
from . import records as r
from . import record_asr_results as ar
from .records_ai import provider_config, transcribe_audio, analyze_segments, RecordAIError
from .record_candidates import add_candidates, pair_qa, rule_facts
from types import SimpleNamespace
from .record_templates import extraction_groups

RATE = 16000
CHUNK_BYTES = RATE * 2 * 10
MAX_BYTES = 500 * 1024 * 1024
BATCH_SAMPLES = RATE * 60
ACTIVE = ('recording', 'finishing', 'processing', 'paused')
log = logging.getLogger('Crj.capture')

class Capture(r.Base):
    __tablename__ = 'record_captures'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    record_id: Mapped[str] = mapped_column(ForeignKey('record_sessions.id'), index=True)
    owner_id: Mapped[str] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(20), default='recording')
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=r.utc_now)
    final_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template: Mapped[dict] = mapped_column(JSON)
    partial: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str] = mapped_column(String(400), default='')
    expected_revision: Mapped[int] = mapped_column(Integer)

class Chunk(r.Base):
    __tablename__ = 'record_capture_chunks'
    capture_id: Mapped[str] = mapped_column(ForeignKey('record_captures.id'), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    samples: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    pcm: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)

class Batch(r.Base):
    __tablename__ = 'record_capture_batches'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda:str(uuid4()))
    capture_id: Mapped[str] = mapped_column(ForeignKey('record_captures.id'), index=True)
    first: Mapped[int] = mapped_column(Integer)
    last: Mapped[int] = mapped_column(Integer)
    offset_samples: Mapped[int] = mapped_column(Integer)
    samples: Mapped[int] = mapped_column(Integer)
    audio_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    segment_id: Mapped[str] = mapped_column(String(36), default=lambda:str(uuid4()))
    asr_provider: Mapped[str] = mapped_column(String(30), default='')
    asr_model: Mapped[str] = mapped_column(String(200), default='')
    text: Mapped[str] = mapped_column(r.Text, default='')
    groups: Mapped[list] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(20), default='pending')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=r.utc_now)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token: Mapped[str] = mapped_column(String(36), default='')
    error: Mapped[str] = mapped_column(String(400), default='')
    __table_args__ = (UniqueConstraint('capture_id','first'),)

class Begin(r.AIRequest):
    id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    started_at: datetime
    captured_at: datetime | None = None
    @field_validator('started_at', 'captured_at')
    @classmethod
    def timezone_required(cls,v):
        if v is None:return v
        if v.tzinfo is None: raise ValueError('时间需要时区')
        return v.astimezone(timezone.utc)

class Finish(r.Input):
    count: int = Field(ge=0,le=10000)
    ended_at: datetime
    partial: bool = False
    confirmed: bool = False
    @field_validator('ended_at')
    @classmethod
    def timezone_required(cls,v):
        if v is None:return v
        if v.tzinfo is None: raise ValueError('时间需要时区')
        return v.astimezone(timezone.utc)


def own(session, record_id, cid, request):
    r.get_record(session,record_id,True)
    item=session.get(Capture,cid)
    if not item or item.record_id!=record_id or item.owner_id!=str(request.state.user['id']):
        raise HTTPException(404,'未找到当前账号的录音会话。')
    return item


def available_row(session, row):
    if session.scalar(select(Capture.id).where(Capture.record_id==row.id,Capture.state.in_(ACTIVE)).limit(1)):
        raise HTTPException(409,'连续录音或后台处理尚未结束，请先完成或结束当前录音流程。')


def wav(pcm):
    out=io.BytesIO()
    with wave.open(out,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(RATE);f.writeframes(pcm)
    return out.getvalue()


def snapshot(session,c):
    chunks=session.execute(select(Chunk.sequence,Chunk.samples).where(Chunk.capture_id==c.id).order_by(Chunk.sequence)).all()
    contiguous=0;samples=0
    for seq,n in chunks:
        if seq!=contiguous:break
        contiguous+=1;samples+=n
    batches=session.scalars(select(Batch).where(Batch.capture_id==c.id).order_by(Batch.first)).all()
    return {'id':c.id,'state':c.state,'error':c.error,'partial':c.partial,'saved_count':contiguous,
            'received_sequences':[seq for seq,_ in chunks], 'saved_seconds':samples/RATE,
            'final_count':c.final_count,'started_at':r.timestamp(c.started_at),'captured_at':r.timestamp(c.captured_at) if c.captured_at else None,
            'batches':[{'id':b.id,'state':b.state,'error':b.error,'text':b.text,'seconds':b.samples/RATE,
                        'offset_seconds':b.offset_samples/RATE,'completed_groups':len([g for g in b.groups if g.get('output')]),
                        'total_groups':len(b.groups),'facts':[f for g in b.groups for f in g.get('output',{}).get('key_facts',[])]} for b in batches]}


@r.router.post('/{record_id}/captures',status_code=201)
def begin(record_id:str, body:Begin, request:Request):
    r.confirmed_settings(request,body,'asr');r.confirmed_settings(request,body,'analysis')
    if not r.conversion_available():raise HTTPException(422,'连续转写需要服务器安装 FFmpeg。')
    with r.write_session(request.app.state.engine) as s:
        row=r.get_record(s,record_id,True)
        old=s.get(Capture,body.id)
        if old:return snapshot(s,own(s,record_id,body.id,request))
        r.mutable(row,body.expected_revision)
        if s.scalar(select(r.RecordAnalysis.id).where(r.RecordAnalysis.record_id==record_id,r.RecordAnalysis.status=='processing')):raise HTTPException(409,'已有生成任务，请等待完成。')
        existing=r.ordered_segments(s,row.id)
        if existing:
            prior=s.scalar(select(r.RecordAnalysis).where(r.RecordAnalysis.record_id==record_id,r.RecordAnalysis.status=='succeeded').order_by(r.RecordAnalysis.created_at.desc()))
            if not prior or prior.input_hash!=r.segment_hash(existing):
                raise HTTPException(409,'补录前请先根据当前文字生成模板，以便复用已有结果；当前文字不会丢弃。')
        c=Capture(id=body.id,record_id=record_id,owner_id=str(request.state.user['id']),started_at=body.started_at,captured_at=body.captured_at or body.started_at,
                  template=deepcopy(row.template_snapshot),expected_revision=row.revision)
        s.add(c);s.flush();r.audit(s,'capture_started',record_id=record_id,capture_id=c.id)
        return snapshot(s,c)


@r.router.get('/{record_id}/captures')
def list_captures(record_id:str,request:Request):
    with Session(request.app.state.engine) as s:
        r.get_record(s,record_id)
        return {'items':[snapshot(s,c) for c in s.scalars(select(Capture).where(Capture.record_id==record_id,Capture.owner_id==str(request.state.user['id'])).order_by(Capture.started_at))]}


@r.router.put('/{record_id}/captures/{cid}/chunks/{sequence}')
async def put_chunk(record_id:str,cid:str,sequence:int,request:Request):
    raw=await request.body()
    if sequence<0 or sequence>=10000 or not raw or len(raw)%2 or len(raw)>CHUNK_BYTES:
        raise HTTPException(422,'PCM 分片为空、长度非法或超过10秒。')
    digest=hashlib.sha256(raw).hexdigest()
    with r.write_session(request.app.state.engine) as s:
        c=own(s,record_id,cid,request);old=s.get(Chunk,(cid,sequence))
        if old:
            if old.digest!=digest:raise HTTPException(409,'相同分片序号对应不同内容，未覆盖原音。')
            return {'sequence':sequence,'sha256':digest}
        if shutil.disk_usage(request.app.state.store.directory).free < 64*1024*1024+len(raw):
            raise HTTPException(507,'后端可用空间不足，未确认保存，请保留浏览器缓存。')
        if c.state not in ('recording','finishing','paused'):raise HTTPException(409,'此会话已结束保存。')
        if c.final_count is not None and sequence>=c.final_count:raise HTTPException(409,'分片超出结束清单。')
        size=s.scalar(select(func.sum(Chunk.samples)).join(Capture).where(Capture.record_id==record_id,Capture.state.in_(ACTIVE))) or 0
        saved=s.scalar(select(func.sum(r.AudioClip.size_bytes)).where(r.AudioClip.record_id==record_id)) or 0
        if saved+size*2+len(raw)>MAX_BYTES:raise HTTPException(507,'本笔录连续录音已达500MiB，请保留本地备份。')
        s.add(Chunk(capture_id=cid,sequence=sequence,samples=len(raw)//2,digest=digest,pcm=raw));c.updated_at=r.utc_now()
    return {'sequence':sequence,'sha256':digest}


@r.router.post('/{record_id}/captures/{cid}/finish')
def finish(record_id:str,cid:str,body:Finish,request:Request):
    if body.partial and not body.confirmed:raise HTTPException(422,'须确认仅使用已保存部分生成。')
    with r.write_session(request.app.state.engine) as s:
        c=own(s,record_id,cid,request)
        if c.state in ('completed','partial','cancelled'):return snapshot(s,c)
        info=snapshot(s,c)
        if body.count!=info['saved_count'] or (not body.partial and len(info['received_sequences'])!=body.count):
            raise HTTPException(409,'分片尚未全部保存或有缺口，请补传后结束。')
        start=c.started_at.replace(tzinfo=timezone.utc) if c.started_at.tzinfo is None else c.started_at
        if body.ended_at<start:raise HTTPException(422,'结束时间不能早于开始时间。')
        if c.final_count is not None and c.final_count!=body.count:raise HTTPException(409,'会话结束清单已固定，不能改变录音范围。')
        c.final_count=body.count;c.ended_at=body.ended_at;c.partial=c.partial or body.partial;c.error=''
        c.state='finishing' if body.count else 'cancelled'
        return snapshot(s,c)


@r.router.post('/{record_id}/captures/{cid}/retry')
def retry(record_id:str,cid:str,body:r.AIRequest,request:Request):
    r.confirmed_settings(request,body,'asr');r.confirmed_settings(request,body,'analysis')
    with r.write_session(request.app.state.engine) as s:
        c=own(s,record_id,cid,request)
        if c.state not in ACTIVE:raise HTTPException(409,'会话已结束。')
        row=r.get_record(s,record_id,True)
        if row.status!='draft' or row.revision!=c.expected_revision:raise HTTPException(409,'笔录已变化，不能恢复旧任务，请保留音频后重新处理。')
        c.state='finishing' if c.final_count is not None else 'recording';c.error=''
        for b in s.scalars(select(Batch).where(Batch.capture_id==cid,Batch.state=='failed')):
            b.state='pending';b.attempts=0;b.retry_at=r.utc_now();b.error=''
        return snapshot(s,c)


@r.router.post('/{record_id}/captures/{cid}/batches/{batch_id}/skip')
def skip(record_id:str,cid:str,batch_id:str,body:r.AIRequest,request:Request):
    if not body.confirmed:raise HTTPException(422,'请确认跳过失败段的自动处理。')
    with r.write_session(request.app.state.engine) as s:
        c=own(s,record_id,cid,request);b=s.get(Batch,batch_id)
        if not b or b.capture_id!=cid:raise HTTPException(404,'未找到当前音频段。')
        if b.state!='failed' or c.state not in ACTIVE:raise HTTPException(409,'只能跳过已失败且未结束的处理段。')
        b.state='skipped';b.error='用户跳过自动处理；原音保留，需人工补充。';c.partial=True;c.error='存在跳过的处理段，最终结果将标记不完整。'
        r.audit(s,'capture_batch_skipped',record_id=record_id,capture_id=cid,batch_id=batch_id)
        return snapshot(s,c)


@r.router.get('/{record_id}/captures/{cid}/backup.wav')
def backup(record_id:str,cid:str,request:Request):
    engine=request.app.state.engine
    with Session(engine) as s:
        c=own(s,record_id,cid,request);info=snapshot(s,c);n=info['saved_count']
        length=round(info['saved_seconds']*RATE)*2
    import struct
    header=b'RIFF'+struct.pack('<I',36+length)+b'WAVEfmt '+struct.pack('<IHHIIHH',16,1,1,RATE,RATE*2,2,16)+b'data'+struct.pack('<I',length)
    def stream():
        yield header
        with Session(engine) as s:
            for chunk in s.scalars(select(Chunk).where(Chunk.capture_id==cid,Chunk.sequence<n).order_by(Chunk.sequence)).yield_per(1):yield chunk.pcm
    return StreamingResponse(stream(),media_type='audio/wav',headers={'Content-Disposition':'attachment; filename="saved-recording.wav"','Cache-Control':'no-store'})



def schedule(engine):
    with r.write_session(engine) as s:
        for c in s.scalars(select(Capture).where(Capture.state.in_(('recording','finishing'))).with_for_update()):
            row=r.get_record(s,c.record_id,True)
            if row.status!='draft' or row.revision!=c.expected_revision:
                c.state='paused';c.error='笔录已变化，自动处理暂停；请核对版本。';continue
            batches=s.scalars(select(Batch).where(Batch.capture_id==c.id).order_by(Batch.first)).all()
            first=batches[-1].last+1 if batches else 0
            offset=sum(b.samples for b in batches)
            chunks=s.execute(select(Chunk.sequence,Chunk.samples).where(Chunk.capture_id==c.id,Chunk.sequence>=first,Chunk.sequence < (c.final_count if c.final_count is not None else 10000)).order_by(Chunk.sequence)).all()
            n=0;last=first-1;cut_ready=False
            for seq,samples in chunks:
                if seq!=last+1:break
                n+=samples;last=seq
                if n>=min(RATE*45,BATCH_SAMPLES):
                    tail=array.array('h',s.get(Chunk,(c.id,seq)).pcm[-RATE//2:])
                    if tail and math.sqrt(sum(x*x for x in tail)/len(tail))<180:cut_ready=True;break
                # Give ongoing speech another bounded 30 seconds to reach a pause.
                if n>=BATCH_SAMPLES+min(RATE*30,BATCH_SAMPLES//2):cut_ready=True;break
            final=c.final_count is not None and last+1==c.final_count
            if n and (cut_ready or final):
                s.add(Batch(capture_id=c.id,first=first,last=last,offset_samples=offset,samples=n,groups=[]))
            elif final and all(b.state in ('done','skipped') for b in batches) and batches:
                c.state='processing'


def claim(engine):
    with r.write_session(engine) as s:
        now=r.utc_now()
        for b in s.scalars(select(Batch).join(Capture).where(Capture.state.in_(('recording','finishing')),Batch.state.in_(('pending','running'))).order_by((Batch.text != ''),Capture.started_at,Batch.first).with_for_update(skip_locked=True)):
            utc=lambda d:d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
            if b.state=='running' and b.lease_until and utc(b.lease_until)>now:continue
            if utc(b.retry_at)>now:continue
            # Ordered extraction preserves the immediately preceding context.
            earlier=s.scalar(select(Batch.id).where(Batch.capture_id==b.capture_id,Batch.first<b.first,Batch.state.notin_(('done','skipped'))).limit(1))
            if earlier and b.text:continue
            b.token=str(uuid4());b.state='running';b.lease_until=now+timedelta(minutes=5);b.attempts+=1
            return b.id,b.token
    return None


def batch_origin(batch):
    return f'capture:{batch.capture_id}:{batch.id}'


def batch_inputs(session, capture, batch):
    run=ar.origin_run(session,capture.record_id,batch_origin(batch))
    return ar.analysis_inputs(session,run) if run else [{'segment_id':batch.segment_id,'text':batch.text,'speaker':'unknown'}]


def process(engine,identifier,token,settings):
    def valid(s):
        b=s.get(Batch,identifier)
        if not b or b.token!=token or b.state!='running':raise RuntimeError('lease_lost')
        c=s.get(Capture,b.capture_id)
        if c.state not in ('recording','finishing'):raise RuntimeError('capture_stopped')
        return b,c
    try:
        for purpose in ('asr','analysis'):
            if not getattr(settings.ai.external_permissions,purpose):raise RecordAIError(403,'云端外发权限已关闭，请配置后重试。')
            provider_config(settings,purpose)
        with Session(engine) as s:
            b,c=valid(s);text=b.text;template=deepcopy(c.template)
            pcm=None
            if not text:
                chunks=list(s.scalars(select(Chunk).where(Chunk.capture_id==c.id,Chunk.sequence>=b.first,Chunk.sequence<=b.last).order_by(Chunk.sequence)))
                offset=s.scalar(select(func.coalesce(func.sum(Chunk.samples),0)).where(Chunk.capture_id==c.id,Chunk.sequence<b.first))
                if [x.sequence for x in chunks]!=list(range(b.first,b.last+1)) or sum(x.samples for x in chunks)!=b.samples or offset!=b.offset_samples:
                    raise RecordAIError(409,'识别批次音频区间不完整或偏移不一致，未发送音频。')
                pcm=b''.join(x.pcm for x in chunks)
        if not text:
            output=transcribe_audio(wav(pcm),'wav','audio/wav',settings);text=output['text'].strip()
            if not text:raise RecordAIError(422,'本段未识别到文字，请试听后重试；未跳过原音。')
            with r.write_session(engine) as s:
                b,c=valid(s)
                intervals=[{'batch_start_sample':0,'batch_end_sample':b.samples,'session_start_sample':b.offset_samples,'sample_rate':RATE}]
                ar.save_result(s,c.record_id,batch_origin(b),output,intervals=intervals)
                b.text=text;b.asr_provider,b.asr_model,_=provider_config(settings,'asr');b.state='pending';b.attempts=0;b.retry_at=r.utc_now();b.token=''
            return
        with r.write_session(engine) as s:
            b,c=valid(s)
            previous=s.scalar(select(Batch).where(Batch.capture_id==c.id,Batch.first<b.first).order_by(Batch.first.desc()))
            inputs=(batch_inputs(s,c,previous) if previous and previous.text else [])+batch_inputs(s,c,b)
            if not b.groups:
                pairs=pair_qa([SimpleNamespace(id=x['segment_id'],text=x['text'],speaker='unknown') for x in inputs],template)
                # Carry context for unfinished questions, but do not duplicate an identical answer.
                distinct=[]
                for pair in pairs:
                    duplicate=next((p for p in distinct if p['field_id']==pair['field_id'] and p['question']==pair['question'] and p['answer']==pair['answer'] and pair['possible_followup_of'] is None),None)
                    if duplicate:duplicate['sources']+=pair['sources']
                    else:distinct.append(pair)
                local=rule_facts(distinct if not any(p['possible_followup_of'] is not None for p in pairs) else pairs)
                matched={f['label'] for f in local}
                remaining={**template,'fields':[f for f in template['fields'] if f['id'] not in matched]}
                b.groups=([{'fields':[],'output':{'summary':'明确问答按原文匹配','key_facts':local,'issues':[]}}] if local else [])+([{'fields':remaining['fields']}] if remaining['fields'] else [])
            groups=deepcopy(b.groups)
            index=next((i for i,g in enumerate(groups) if 'output' not in g),None)
        if index is not None:
            group_template={**template,'fields':groups[index]['fields'],'grouped_generation':True,'all_field_ids':[f['id'] for f in template.get('fields',[])]}
            output=analyze_segments(inputs,settings,group_template)
            with r.write_session(engine) as s:
                b,c=valid(s);groups=deepcopy(b.groups);groups[index]['output']=output;groups[index]['input_hash']=r.content_hash(inputs);groups[index]['provider'],groups[index]['model'],_=provider_config(settings,'analysis');b.groups=groups
                b.state='pending';b.attempts=0;b.retry_at=r.utc_now();b.token=''
            return
        with r.write_session(engine) as s:
            b,c=valid(s);b.state='done';b.error='';b.token=''
    except Exception as exc:
        message=exc.message if isinstance(exc,RecordAIError) else '后台处理失败，已保留音频和成功检查点，请重试。'
        status=exc.status if isinstance(exc,RecordAIError) else 500
        log.warning('capture_job_failed batch_id=%s status=%s',identifier,status)
        with r.write_session(engine) as s:
            b=s.get(Batch,identifier)
            if not b or b.token!=token:return
            # Short live transcripts normally need one extraction call. Split only
            # a failed oversized group; completed groups remain checkpoints.
            groups=deepcopy(b.groups)
            large=next((i for i,g in enumerate(groups) if 'output' not in g and len(g['fields'])>10),None)
            if b.text and large is not None and status in (502,504):
                target=groups[large]
                b.groups=groups[:large]+[{'fields':f} for f in extraction_groups({'fields':target['fields']})]+groups[large+1:]
                b.state='pending';b.attempts=0;b.error='已保留转写，将失败的大组拆分后继续。';b.retry_at=r.utc_now()+timedelta(seconds=2);b.token='';return
            transient=status in (429,500,502,503,504)
            b.state='pending' if transient and b.attempts<3 else 'failed'
            b.error=message[:400];b.retry_at=r.utc_now()+timedelta(seconds=10*2**min(b.attempts,4));b.token=''
            if b.state=='failed':
                c=s.get(Capture,b.capture_id);c.error='有片段处理失败，成功结果已保留。'


def finalize(engine,cid,settings):
    with r.write_session(engine) as s:
        c=s.scalar(select(Capture).where(Capture.id==cid).with_for_update())
        if not c or c.state!='processing':return
        row=r.get_record(s,c.record_id,True)
        if row.status!='draft' or row.revision!=c.expected_revision:
            c.state='paused';c.error='笔录版本已变化，未覆盖当前内容。';return
        batches=s.scalars(select(Batch).where(Batch.capture_id==cid).order_by(Batch.first)).all()
        existing=r.ordered_segments(s,row.id)
        if sum(len(x.text) for x in existing)+sum(len(b.text) for b in batches)>r.LIMITS['max_total_text_chars']:
            c.state='paused';c.error='合并文字超过100000字上限，已保留全部转写，请分份整理。';return
        start=c.started_at.replace(tzinfo=timezone.utc) if c.started_at.tzinfo is None else c.started_at
        captured=c.captured_at or c.started_at
        captured=captured.replace(tzinfo=timezone.utc) if captured.tzinfo is None else captured
        sentence_count=sum(len(ar.sentences(s,run)) if (run:=ar.origin_run(s,row.id,batch_origin(b))) else (1 if b.text else 0) for b in batches)
        if len(existing)+sentence_count>r.LIMITS['max_segments']:
            c.state='paused';c.error='识别片段超过正文上限，逐句原始结果已保留。';return
        position=len(existing)
        for i,b in enumerate(batches):
            pcm=b''.join(x.pcm for x in s.scalars(select(Chunk).where(Chunk.capture_id==cid,Chunk.sequence>=b.first,Chunk.sequence<=b.last).order_by(Chunk.sequence)))
            raw=wav(pcm);audio=r.AudioClip(record_id=row.id,filename=f'连续录音-{cid[:8]}-{i+1:03}.wav',format='wav',mime_type='audio/wav',size_bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),duration_ms=round(b.samples/RATE*1000),raw_audio=raw)
            s.add(audio);s.flush();b.audio_id=audio.id
            s.add(r.AudioRecordingTime(audio_id=audio.id,started_at=start if i==0 else captured+timedelta(seconds=b.offset_samples/RATE),ended_at=captured+timedelta(seconds=(b.offset_samples+b.samples)/RATE)))
            provider,model=b.asr_provider or 'siliconflow',b.asr_model or '未完成转写'
            attempt=r.TranscriptionAttempt(record_id=row.id,audio_id=audio.id,status='succeeded' if b.text else 'failed',text=b.text,error='用户跳过本段自动处理' if b.state=='skipped' else '',provider=provider,model=model,input_revision=row.revision,finished_at=r.utc_now())
            s.add(attempt);s.flush()
            run=ar.origin_run(s,row.id,batch_origin(b))
            if run:
                position+=ar.project(s,run,row,audio.id,attempt.id,position)
            elif b.text:
                s.add(r.RecordSegment(id=b.segment_id,record_id=row.id,position=position,audio_id=audio.id,speaker='unknown',text=b.text,source='asr',transcription_id=attempt.id));position+=1
        s.flush();all_segments=r.ordered_segments(s,row.id);valid_ids={x.id for x in all_segments}
        if not all_segments:
            row.occurred_at=r.recording_bounds(s,row.id)[0]
            r.version(s,row,'capture_completed');c.state='partial';c.error='未获得可用转写，仅保存原音，请人工补充后生成。';return
        # Preserve distinct statements instead of allowing last-completed tasks to win.
        facts={};issues=[]
        previous=s.scalar(select(r.RecordAnalysis).where(r.RecordAnalysis.record_id==row.id,r.RecordAnalysis.status=='succeeded').order_by(r.RecordAnalysis.created_at.desc()))
        outputs=([{'key_facts':previous.key_facts,'issues':[]}] if previous and previous.input_hash==r.segment_hash(existing) else [])
        outputs += [g['output'] for b in batches if b.state=='done' for g in b.groups if 'output' in g]
        for out in outputs:
            issues.extend(out.get('issues',[]))
            for f in out.get('key_facts',[]):
                if not set(f['segment_ids'])<=valid_ids:continue
                bucket=facts.setdefault(f['label'],[])
                same=next((x for x in bucket if x['value']==f['value']),None)
                if same:same['segment_ids']=list(dict.fromkeys(same['segment_ids']+f['segment_ids']))
                else:bucket.append(deepcopy(f))
        merged=[]
        for key,values in facts.items():
            if len(values)==1:merged.append(values[0]);continue
            issues.append(f'字段 {key} 存在多次陈述，请核对是否为补充或更正。')
            combined='\n'.join(f'陈述{i+1}：{v["value"]}' for i,v in enumerate(values))
            if len(combined)>2000:
                issues.append(f'字段 {key} 的多次陈述过长，未自动回填，请查阅原文。');continue
            merged.append({'label':key,'value':combined,'segment_ids':list(dict.fromkeys(sid for v in values for sid in v['segment_ids']))})
        if c.partial:issues.insert(0,'录音不完整：仅依据已保存部分生成，缺失内容须补充核对。')
        provider,model,_=provider_config(settings,'analysis')
        analysis=r.RecordAnalysis(record_id=row.id,status='succeeded',summary='分段转写与增量提取已合并；多次陈述保留供人工核对。',key_facts=merged,issues=list(dict.fromkeys(issues)),input_segments=r.input_segments(all_segments),input_hash=r.segment_hash(all_segments),input_revision=row.revision,provider=provider,model=model,finished_at=r.utc_now())
        s.add(analysis);s.flush();add_candidates(s,row,analysis,merged)
        s.flush()
        for candidate in s.scalars(select(r.RecordFieldCandidate).where(r.RecordFieldCandidate.analysis_id==analysis.id)):
            values=facts.get(candidate.field_id,[])
            if len(values)>1 and not candidate.previous_value:candidate.previous_value=values[0]['value']
        row.template_snapshot=r.with_recording_times(s,row,r.filled_template(row.template_snapshot,merged,analysis.input_hash))
        if c.partial:
            row.template_snapshot={**row.template_snapshot,'capture_incomplete':True}
        row.occurred_at=r.recording_bounds(s,row.id)[0]
        r.version(s,row,'capture_completed');c.state='partial' if c.partial else 'completed';c.error=''


def worker(engine,store,stop):
    while not stop.is_set():
        try:
            schedule(engine)
            with store.lock:settings=store.current.model_copy(deep=True)
            claimed=claim(engine)
            if claimed:process(engine,*claimed,settings)
            else:
                with Session(engine) as s:ids=list(s.scalars(select(Capture.id).where(Capture.state=='processing')))
                for cid in ids:finalize(engine,cid,settings)
                stop.wait(1)
        except Exception:
            log.error('capture_worker_iteration_failed: storage or task state unavailable')
            stop.wait(5)


def start_worker(engine,store):
    import os
    stop=threading.Event()
    if os.environ.get('CRJ_CAPTURE_EMBEDDED','1')=='0':return stop,[]
    threads=[threading.Thread(target=worker,args=(engine,store,stop),name=f'record-capture-{i}',daemon=True) for i in range(2)]
    for thread in threads:thread.start()
    return stop,threads


def purge_audio(session,record_id,audio_id):
    # Removing any finalized chunk invalidates and purges the mixed capture outputs.
    ids=list(session.scalars(select(Batch.capture_id).where(Batch.audio_id==audio_id)))
    for cid in ids:
        for b in session.scalars(select(Batch).where(Batch.capture_id==cid)):
            if b.audio_id==audio_id:
                for chunk in session.scalars(select(Chunk).where(Chunk.capture_id==cid,Chunk.sequence>=b.first,Chunk.sequence<=b.last)):session.delete(chunk)
                session.delete(b)
            else:b.groups=[]
        capture=session.get(Capture,cid);capture.state='cancelled';capture.error='关联音频已删除，连续录音合并结果已清除。'

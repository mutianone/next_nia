"""Explicit text Q/A pairing, immutable candidates and append-only review events."""
import re
from collections import defaultdict
from fastapi import HTTPException, Request
from pydantic import Field
from sqlalchemy import select
from . import records as r
from .record_templates import SYSTEM_FIELDS

from .record_selection import ACOUSTIC_MARKER, answer_blocks, officer_question_contexts, repeated_particle_cut, role_ranges, source_units
from .record_quality import mixed_answer

FOLLOWUP = re.compile(r'你刚才|刚才说|你是说|你的意思|是否就是说|再确认|补充说明')


def normalize(text):
    return re.sub(r'[\W_]+', '', text, flags=re.UNICODE)


def pair_qa(segments, template):
    segments = list(segments)
    aliases = defaultdict(set)
    for field in template.get('fields', []):
        if field['id'].startswith('q'):
            for alias in [field['label'], *field.get('question_aliases', [])]:
                aliases[normalize(alias)].add(field['id'])
    turns = []
    inputs = r.input_segments(segments)
    contexts = officer_question_contexts(inputs)
    units = source_units(inputs)
    blocks = answer_blocks(inputs, units)
    ambiguous = defaultdict(list)
    for u in units:
        if blocks.get(u['id']) == ():
            ambiguous[u['segment_id']].append((u['start'],u['end']))
    leadins = {s['segment_id'] for context in contexts.values() for s in context[:-1]}
    for segment in segments:
        if segment.id in leadins:
            continue
        if segment.id in contexts:
            block = contexts[segment.id]
            if block:
                turns.append({'role':'question','text':'\n'.join(s['text'] for s in block),
                              'sources':[{'segment_id':s['segment_id'], 'start':0, 'end':len(s['text']), 'precision':'exact'} for s in block],
                              'role_source':'manual_label'})
            else:
                turns.append({'role':'unknown'})
            continue
        if segment.speaker == 'unknown':
            from .record_asr_results import metadata
            if getattr(segment, 'voice_review', False) or metadata(segment).get('voice_review'):
                turns.append({'role':'unknown'})
                continue
        text = segment.text
        for start, end, role in role_ranges(text, segment.speaker):
            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
            if start == end:
                continue
            content = text[start:end]
            if (ACOUSTIC_MARKER.search(content) or role not in ('interviewer', 'interviewee')
                    or any(a<end and b>start for a,b in ambiguous.get(segment.id, ()))):
                turns.append({'role': 'unknown'})
                continue
            turns.append({'role': 'question' if role == 'interviewer' else 'answer',
                          'text': content, 'source': {'segment_id': segment.id, 'start': start,
                          'end': end, 'precision': 'exact'},
                          'role_source': 'manual_label' if content == text.strip() else 'text_marker'})
    pairs=[];pending=None;previous=None
    for turn in turns:
        if turn['role']=='question':
            if pending: pairs.append(pending)
            matches=aliases.get(normalize(turn['text']),set())
            field=next(iter(matches)) if len(matches)==1 else None
            follows=previous is not None and bool(FOLLOWUP.search(turn['text']))
            # Follow-up relation is a suggestion. Only exact question matches enter rule filling.
            pending={'question':turn['text'],'answer':'','field_id':field,'possible_followup_of':previous if follows else None,
                     'sources':turn['sources'] if 'sources' in turn else [turn['source']],'role_source':turn['role_source']}
        elif turn['role'] == 'unknown':
            if pending:
                pairs.append(pending)
            pending = previous = None
        elif pending:
            cut = repeated_particle_cut(pending['question'], turn['text']) if not pending['answer'] else None
            if cut is not None:
                turn['text'] = turn['text'][cut:]
                turn['source']['start'] += cut
            if mixed_answer(pending['question'], turn['text'], confirmed=True):
                # The rest of this turn may qualify an earlier answer.
                pending = previous = None
                continue
            pending['answer'] += ('\n' if pending['answer'] else '') + turn['text']
            pending['sources'].append(turn['source'])
            previous=len(pairs)
    if pending:pairs.append(pending)
    return pairs


def rule_facts(pairs):
    grouped=defaultdict(list)
    for pair in pairs:
        if pair['field_id'] and pair['answer']:
            grouped[pair['field_id']].append(pair)
    # A follow-up needs contextual interpretation, so do not bypass the cloud model.
    followup_fields = {pairs[p['possible_followup_of']]['field_id'] for p in pairs if p['possible_followup_of'] is not None}
    last_field=None
    for pair in pairs:
        if pair['field_id']: last_field=pair['field_id']
        elif pair['answer'] and last_field: followup_fields.add(last_field)
    result=[]
    for field, items in grouped.items():
        if field in followup_fields: continue
        # Multiple answers are not silently collapsed into an invented definitive answer.
        value='\n'.join((f'第{i+1}次回答：' if len(items)>1 else '')+p['answer'] for i,p in enumerate(items))
        if len(value)>2000:continue  # Preserve the full original for AI/manual handling.
        result.append({'label':field,'value':value,'segment_ids':list(dict.fromkeys(s['segment_id'] for p in items for s in p['sources']))})
    return result


def add_candidates(session, row, attempt, facts):
    if session.scalar(select(r.RecordFieldCandidate.id).where(r.RecordFieldCandidate.analysis_id==attempt.id).limit(1)):
        return
    segments=r.ordered_segments(session,row.id);by_id={s.id:s for s in segments}
    pairs=pair_qa(segments,row.template_snapshot)
    template_key=row.template_id+':'+str(row.template_snapshot.get('version',''))
    if not session.scalar(select(r.RecordQAPair.id).where(r.RecordQAPair.record_id==row.id,r.RecordQAPair.input_hash==attempt.input_hash,r.RecordQAPair.template_key==template_key).limit(1)):
        for index,pair in enumerate(pairs):
            session.add(r.RecordQAPair(record_id=row.id,input_hash=attempt.input_hash,template_key=template_key,position=index,data=pair))
    exact={f['label']:f for f in rule_facts(pairs)}
    existing=row.template_snapshot.get('values',{})
    allowed = {f['id'] for f in row.template_snapshot.get('fields', [])}
    for fact in facts:
        key=fact['label']
        if key not in allowed: continue
        if key in SYSTEM_FIELDS:continue
        is_rule=key in exact and fact['value']==exact[key]['value']
        sources=([s for pair in pairs if pair['field_id']==key for s in pair['sources']] if is_rule else
                 [{'segment_id':sid,'start':0,'end':len(by_id[sid].text),'precision':'segment'} for sid in fact['segment_ids']])
        if fact.get('evidence_spans'):
            sources = [source for answer in fact['evidence_spans'] for source in [*answer.get('question_sources', []), answer]]
        # Keep earlier exact answers as candidate versions when contextual analysis
        # produces a later interpretation. They are never discarded or auto-confirmed.
        earlier=[pair for pair in pairs if pair['field_id']==key and pair['answer']]
        if len(earlier)==1 and earlier[0]['answer']==fact['value']: earlier=[]
        previous=existing.get(key,'')
        for position,pair in enumerate(earlier):
            session.add(r.RecordFieldCandidate(record_id=row.id,analysis_id=attempt.id,field_id=key,value=pair['answer'],
                previous_value=previous,input_hash=attempt.input_hash,template_id=row.template_id,
                template_version=str(row.template_snapshot.get('version','')),origin='rule',sources=pair['sources'],position=position))
            previous=pair['answer']
        session.add(r.RecordFieldCandidate(record_id=row.id,analysis_id=attempt.id,field_id=key,value=fact['value'],
                    previous_value=existing.get(key,'') or (earlier[-1]['answer'] if earlier else ''),input_hash=attempt.input_hash,template_id=row.template_id,
                    template_version=str(row.template_snapshot.get('version','')),origin='rule' if is_rule else 'ai',
                    sources=sources,position=len(earlier)))
    session.flush()


def candidate_data(session,row):
    candidates=session.scalars(select(r.RecordFieldCandidate).where(r.RecordFieldCandidate.record_id==row.id)
                             .order_by(r.RecordFieldCandidate.created_at.desc(),r.RecordFieldCandidate.id.desc())).all()
    digest=r.segment_hash(r.ordered_segments(session,row.id));result=[]
    latest_analysis=session.scalar(select(r.RecordAnalysis.id).where(r.RecordAnalysis.record_id==row.id, r.RecordAnalysis.status=='succeeded').order_by(r.RecordAnalysis.created_at.desc(), r.RecordAnalysis.id.desc()).limit(1))
    reviews=session.scalars(select(r.RecordFieldReview).join(r.RecordFieldCandidate)
                            .where(r.RecordFieldCandidate.record_id==row.id).order_by(r.RecordFieldReview.created_at.desc())).all()
    by_candidate=defaultdict(list)
    for review in reviews:by_candidate[review.candidate_id].append(review)
    positions={}
    for item in candidates:
        if item.analysis_id==latest_analysis:positions[item.field_id]=max(positions.get(item.field_id,-1),item.position)
    for item in candidates:
        events=by_candidate[item.id];decision=events[0].decision if events else None
        stale=item.input_hash!=digest or item.template_id!=row.template_id or item.template_version!=str(row.template_snapshot.get('version',''))
        current_value=row.template_snapshot.get('values',{}).get(item.field_id,'')
        protected=item.field_id in row.template_snapshot.get('locked_fields',[])
        conflict=(bool(item.previous_value) and item.previous_value!=item.value) or (protected and current_value!=item.value)
        status='stale' if stale else decision or ('conflict' if conflict else 'pending')
        latest=item.analysis_id==latest_analysis and item.position==positions.get(item.field_id) and not stale
        attempt=session.get(r.RecordAnalysis,item.analysis_id)
        originals={s['segment_id']:s['text'] for s in attempt.input_segments}
        previews=[{'quote':originals.get(s['segment_id'],'')[s['start']:s['end']], 'question':s.get('question',''), 'precision':s['precision']} for s in item.sources[:3]]
        result.append({'id':item.id,'field_id':item.field_id,'value':item.value,'previous_value':item.previous_value,
                       'status':status,'latest':latest,'current_value':current_value,'protected':protected,'origin':item.origin,'template_version':item.template_version,
                       'created_at':r.timestamp(item.created_at),'sources':item.sources,
                       'evidence_preview':previews,
                       'reviews':[{'decision':e.decision,'note':e.note,'created_at':r.timestamp(e.created_at)} for e in events]})
    qa=session.scalars(select(r.RecordQAPair).where(r.RecordQAPair.record_id==row.id,r.RecordQAPair.input_hash==digest,r.RecordQAPair.template_key==row.template_id+':'+str(row.template_snapshot.get('version','')))
                      .order_by(r.RecordQAPair.position)).all()
    return {'candidates':result,'qa_pairs':[{'id':p.id,**p.data} for p in qa]}


class ReviewInput(r.Input):
    expected_revision:int=Field(ge=1)
    decision:str=Field(pattern='^(confirmed|rejected)$')
    note:str=Field(default='',max_length=1000)
    replace_protected:bool=False


@r.router.post('/{record_id}/candidates/{candidate_id}/review')
def review(record_id:str,candidate_id:str,body:ReviewInput,request:Request):
    with r.write_session(request.app.state.engine) as session:
        row=r.get_record(session,record_id,True);r.mutable(row,body.expected_revision)
        candidate=session.get(r.RecordFieldCandidate,candidate_id)
        if candidate is None or candidate.record_id!=record_id:raise HTTPException(404,'未找到当前笔录的候选答案。')
        digest=r.segment_hash(r.ordered_segments(session,row.id))
        if candidate.input_hash!=digest or candidate.template_id!=row.template_id:raise HTTPException(409,'来源文字或模板已变化，请重新生成候选。')
        template=dict(row.template_snapshot)
        if template.get('source_hash')!=digest:raise HTTPException(409,'请先根据当前文字重新生成。')
        current=next((c for c in candidate_data(session,row)['candidates'] if c['id']==candidate_id),None)
        if not current or not current['latest']:raise HTTPException(409,'已有较新候选，请查看最新候选及历史后再决定。')
        if current['status']==body.decision:return r.detail(session,row)
        if current['status'] in ('confirmed','rejected'):raise HTTPException(409,'该候选已审核；原审核记录保留，请生成新候选或人工修订。')
        values=dict(template.get('values',{}));locks=set(template.get('locked_fields',[]));sources=dict(template.get('sources',{}))
        if body.decision=='confirmed':
            if candidate.field_id in locks and values.get(candidate.field_id,'')!=candidate.value and not body.replace_protected:
                raise HTTPException(409,'该字段已有人工保护值，需明确确认替换后再采用候选。')
            values[candidate.field_id]=candidate.value;locks.add(candidate.field_id)
            sources[candidate.field_id]=list(dict.fromkeys(s['segment_id'] for s in candidate.sources))
        elif candidate.field_id not in locks and values.get(candidate.field_id)==candidate.value:
            # The rejected suggestion must not remain in the generated document.
            values[candidate.field_id]=candidate.previous_value
            locks.add(candidate.field_id)
            sources.pop(candidate.field_id,None)
        row.template_snapshot={**template,'values':values,'locked_fields':sorted(locks),'sources':sources}
        session.add(r.RecordFieldReview(candidate_id=candidate_id,decision=body.decision,note=body.note,
                    actor_id=(getattr(request.state,'user',None) or {}).get('id')))
        r.version(session,row,'candidate_'+body.decision)
        return r.detail(session,row)


@r.router.get('/{record_id}/candidates/{candidate_id}/evidence')
def evidence(record_id:str,candidate_id:str,request:Request):
    with r.write_session(request.app.state.engine) as session:
        row=r.get_record(session,record_id)
        candidate=session.get(r.RecordFieldCandidate,candidate_id)
        if candidate is None or candidate.record_id!=record_id:raise HTTPException(404,'未找到候选答案。')
        attempt=session.get(r.RecordAnalysis,candidate.analysis_id)
        originals={s['segment_id']:s['text'] for s in attempt.input_segments}
        segments={s.id:s for s in r.ordered_segments(session,row.id)}
        current=candidate.input_hash==r.segment_hash(list(segments.values()))
        sources=[]
        for source in candidate.sources:
            item=segments.get(source['segment_id']);text=originals.get(source['segment_id'],'')
            sources.append({**source,'quote':text[source['start']:source['end']],
                            'audio_id':item.audio_id if current and item else None,
                            'start_ms':item.start_ms if current and item else None})
        return {'sources':sources,'stale':not current,'provider':attempt.provider,'model':attempt.model}


def finalize_reviews(session, row, request):
    """Explicit whole-document review confirms remaining unchanged suggestions."""
    candidates=[c for c in candidate_data(session,row)['candidates'] if c['latest']]
    if any(c['status']=='conflict' for c in candidates):
        raise HTTPException(422,'有存在差异的候选尚未处理，请先逐项采用或驳回，再核对全文。')
    for candidate in candidates:
        if candidate['status']=='pending':
            session.add(r.RecordFieldReview(candidate_id=candidate['id'],
                decision='confirmed' if candidate['current_value']==candidate['value'] else 'manual',
                actor_id=(getattr(request.state,'user',None) or {}).get('id'),note='全文人工核对时确认最终字段内容。'))


def purge_candidates(session, record_id):
    reviews=session.scalars(select(r.RecordFieldReview).join(r.RecordFieldCandidate).where(r.RecordFieldCandidate.record_id==record_id)).all()
    for review in reviews: session.delete(review)
    session.flush()
    for model in (r.RecordFieldCandidate,r.RecordQAPair):
        for item in session.scalars(select(model).where(model.record_id==record_id)):session.delete(item)
    session.flush()

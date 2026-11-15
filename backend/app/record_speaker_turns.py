"""Build provenance-preserving turns; never edit recognition sentences."""
from uuid import uuid5, NAMESPACE_URL
from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base
from .record_speaker_roles import sentence_parts, scope


class SpeakerTurn(Base):
    __tablename__ = 'record_speaker_turns'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(36), index=True)
    draft_id: Mapped[str] = mapped_column(String(36), index=True)
    position: Mapped[int] = mapped_column()
    data: Mapped[dict] = mapped_column(JSON)


def suggest_intent(text):
    """Conservative text hints only. Neither punctuation nor hints confirm roles."""
    t=text.strip().lstrip('问答：: ')
    if t.startswith(('我们是', '现告知', '你有权', '请阅读', '应当如实')):return 'INSTRUCTION'
    if t.startswith(('你说的是', '您说的是', '什么意思', '能再说')):return 'CLARIFICATION'
    if t.startswith(('具体几点', '具体哪里', '刚才说的', '你刚才说')):return 'FOLLOW_UP'
    if t.startswith(('你叫', '你住', '你什么', '你哪', '你是否', '你认识', '请介绍', '说一下', '事情经过')):return 'QUESTION'
    return 'UNKNOWN'


def build_turns(session, runs_and_sentences):
    result=[]
    for run, sentences in runs_and_sentences:
        for sentence in sentences:
            d=sentence.data
            parts,revision=sentence_parts(session,sentence)
            for part in parts:
                whole=part['start']==0 and part['end']==len(d['text'])
                text=d['text'][part['start']:part['end']]
                source={'sentence_id':sentence.id,'recognition_run_id':run.id,'start':part['start'],'end':part['end'],
                        'audio_id':run.audio_id,'channel_id':d.get('channel_id'),'speaker_id':d.get('speaker_id'),
                        'start_ms':d.get('start_ms') if whole else None,'end_ms':d.get('end_ms') if whole else None,
                        'listen_start_ms':d.get('start_ms'),'session_intervals':d.get('session_intervals',[]) if whole else [],
                        'source_intervals':d.get('session_intervals',[]),'timestamp_source':d.get('timestamp_source','UNKNOWN'),'timestamp_unit':'ms',
                        'review_revision':revision,'origin_key':run.origin_key,'role':part['role']}
                intent=part['intent'];role=part['role']
                current={'id':str(uuid5(NAMESPACE_URL,f'{sentence.id}:{part["start"]}:{part["end"]}:{revision}')),
                         'role':role,'intent':intent,'suggested_intent':suggest_intent(text),'text':text,'sources':[source],
                         'status':'CONFIRMED' if role!='UNKNOWN' and intent!='UNKNOWN' else 'REVIEW_REQUIRED'}
                # A continuation requires positive human intent, confirmed roles,
                # same provider speaker/channel/run, and a contiguous source interval.
                previous=result[-1] if result else None
                if previous and role!='UNKNOWN' and previous['role']==role and previous['status']=='CONFIRMED' and current['status']=='CONFIRMED':
                    a=previous['sources'][-1];b=source
                    same_voice=scope(a['recognition_run_id'],a['channel_id'],a['speaker_id'])==scope(b['recognition_run_id'],b['channel_id'],b['speaker_id'])
                    adjacent=a['end_ms'] is not None and b['start_ms'] is not None and 0<=b['start_ms']-a['end_ms']<=2000
                    discontinuous=len(a['source_intervals'])>1 or len(b['source_intervals'])>1
                    if a['source_intervals'] and b['source_intervals']:
                        discontinuous=discontinuous or not 0<=b['source_intervals'][0]['start_ms']-a['source_intervals'][-1]['end_ms']<=2000
                    answer_continuation=role=='INTERVIEWEE' and previous['intent'] in {'ANSWER','CONTINUATION'} and intent=='CONTINUATION'
                    explicit_continuation=intent=='CONTINUATION' and previous['intent'] not in {'QUESTION','FOLLOW_UP'}
                    if same_voice and a['speaker_id'] is not None and adjacent and not discontinuous and (answer_continuation or explicit_continuation):
                        previous['sources'].append(source);previous['text']+='\n'+text
                        continue
                result.append(current)
    return result

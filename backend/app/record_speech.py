"""Validate provider turn boundaries without guessing semantic speaker roles."""
import math
import re


def normalize_turns(payload):
    from .records_ai import RecordAIError
    segments=payload.get('segments')
    if segments is None:return []
    try:
        if not isinstance(segments,list) or not segments or len(segments)>500:raise ValueError()
        turns=[];previous=-1
        for segment in segments:
            text=segment['text'];speaker=str(segment['speaker']);start=segment['start'];end=segment['end']
            if not isinstance(text,str) or not text.strip() or not re.fullmatch(r'[A-Za-z0-9_-]{1,32}',speaker):raise ValueError()
            if any(isinstance(t,bool) or not isinstance(t,(float,int)) or not math.isfinite(t) for t in (start,end)):raise ValueError()
            if start<previous or start<0 or end<=start or end>86400:raise ValueError()
            previous=start
            # Some providers repeat labels in the aggregate text. Never use that aggregate to reconstruct boundaries.
            turns.append({'speaker':'unknown','speaker_id':speaker,'text':text.strip(),
                          'start_ms':round(start*1000),'end_ms':round(end*1000),
                          'timestamp_source':'PROVIDER'})
            if turns[-1]['end_ms']<=turns[-1]['start_ms']:raise ValueError()
        if sum(len(t['text']) for t in turns)>20000:raise ValueError()
        return turns
    except (KeyError,ValueError,TypeError):
        raise RecordAIError(502,'说话人分段或时间戳无效，本次结果未应用。') from None

"""Resolve model-selected utterance IDs to immutable source text, never model prose."""
import re


ROLE_MARKER = re.compile(r'(?:^|(?<=[。！？!?\n]))\s*(问|答|询问人|被询问人|民警|当事人|翻译)[:：]\s*')
ACOUSTIC_MARKER = re.compile(r'^\[说话人 ([A-Za-z0-9_-]{1,32})\]\s*')


def source_units(segments):
    units=[]
    for segment in segments:
        text=segment['text']; markers=list(ROLE_MARKER.finditer(text))
        ranges=[]
        if markers:
            if markers[0].start(): ranges.append((0,markers[0].start(),'unknown'))
            for i,m in enumerate(markers):
                role='interviewer' if m[1] in ('问','询问人','民警') else 'interpreter' if m[1]=='翻译' else 'interviewee'
                ranges.append((m.end(),markers[i+1].start() if i+1<len(markers) else len(text),role))
        else: ranges=[(0,len(text),segment.get('speaker','unknown'))]
        for begin,finish,role in ranges:
            for match in re.finditer(r'[^。！？!?\n]+[。！？!?\n]*',text[begin:finish]):
                start,end=begin+match.start(),begin+match.end()
                while start<end and text[start].isspace(): start+=1
                while end>start and text[end-1].isspace(): end-=1
                acoustic=ACOUSTIC_MARKER.match(text[start:end])
                if acoustic:
                    start+=acoustic.end(); role='unconfirmed_voice'
                if start==end:continue
                units.append({'id':f'u{len(units)+1:04d}','segment_id':segment['segment_id'],
                    'start':start,'end':end,'text':text[start:end],'role':role})
    return units


def resolve_selections(data, segments):
    if not isinstance(data,dict): return data
    if 'qa_pairs' not in data:
        if not any(isinstance(f,dict) and 'qa_ids' in f for f in data.get('key_facts',[])): return data
        data['qa_pairs']=paired_options(segments)
    units=source_units(segments); indexed={u['id']:(i,u) for i,u in enumerate(units)}
    pairs=data.pop('qa_pairs')
    if not isinstance(pairs,list) or len(pairs)>128: raise ValueError('invalid pairs')
    table={}
    def span(ids):
        if not isinstance(ids,list) or not ids or len(ids)>100: raise ValueError('invalid units')
        positions=[indexed[x][0] for x in ids]
        if positions!=list(range(positions[0],positions[0]+len(ids))): raise ValueError('noncontiguous units')
        selected=[indexed[x][1] for x in ids]
        if len({u['segment_id'] for u in selected})!=1: raise ValueError('span crosses clips')
        first,last=selected[0],selected[-1]
        text=next(s['text'] for s in segments if s['segment_id']==first['segment_id'])
        return first['segment_id'],text[first['start']:last['end']]
    for pair in pairs:
        if not isinstance(pair,dict) or set(pair)!={'id','question_ids','answer_ids'}: raise ValueError('invalid pair')
        if not isinstance(pair['id'],str) or pair['id'] in table: raise ValueError('duplicate pair')
        qid,question=span(pair['question_ids']); aid,answer=span(pair['answer_ids'])
        if indexed[pair['answer_ids'][0]][0]!=indexed[pair['question_ids'][-1]][0]+1: raise ValueError('unpaired units')
        table[pair['id']]={'segment_id':aid,'question_segment_id':qid,'question':question,'answer':answer,
                          'start':indexed[pair['answer_ids'][0]][1]['start'],
                          'end':indexed[pair['answer_ids'][-1]][1]['end'],
                          'question_start':indexed[pair['question_ids'][0]][1]['start']}
    for fact in data.get('key_facts',[]):
        if 'qa_ids' not in fact: continue
        if set(fact)!={'label','qa_ids'} or not str(fact['label']).startswith('q'): raise ValueError('invalid selection')
        ids=fact.pop('qa_ids')
        if not isinstance(ids,list) or not ids or len(ids)>30 or len(set(ids))!=len(ids): raise ValueError('invalid qa refs')
        evidence=[table[x] for x in ids]
        fact.update(value='\n'.join(e['answer'] for e in evidence), evidence=evidence,
                    segment_ids=list(dict.fromkeys(s for e in evidence for s in (e['question_segment_id'],e['segment_id']))))
    return data


def paired_options(segments):
    """Confirmed turn boundaries outrank punctuation. Unknown voices never imply roles."""
    units=source_units(segments); pairs=[];i=0
    while i<len(units):
        unit=units[i]; role=unit['role']
        if role=='interviewer':
            questions=[unit];j=i+1
            while j<len(units) and units[j]['role']=='interviewer' and units[j]['segment_id']==unit['segment_id']:
                questions.append(units[j]);j+=1
            answers=[]
            while j<len(units) and units[j]['role']=='interviewee' and (not answers or units[j]['segment_id']==answers[0]['segment_id']):
                answers.append(units[j]);j+=1
            i=j if answers else i+len(questions)
        elif role=='unknown' and re.search(r'[？?]$',unit['text']):
            questions=[unit];answers=[];j=i+1
            while j<len(units):
                following=units[j]
                if following['role']!='unknown' or re.search(r'[？?]',following['text']):break
                if answers and following['segment_id']!=answers[0]['segment_id']:break
                answers.append(following);j+=1
            i+=1
        else:i+=1;continue
        if answers:
            pairs.append({'id':f'p{len(pairs)+1:04d}','question_ids':[u['id'] for u in questions],
                          'answer_ids':[u['id'] for u in answers]})
    return pairs


def resolve_independently(data, segments):
    """A bad field cannot discard independently valid selections."""
    import copy
    if not isinstance(data,dict) or not isinstance(data.get('key_facts'),list): return data
    if len(data['key_facts'])>128: raise ValueError('too many facts')
    result=copy.deepcopy(data); result.pop('qa_pairs',None)
    result['key_facts']=[]
    for fact in data['key_facts']:
        if not isinstance(fact,dict) or 'qa_ids' not in fact:
            result['key_facts'].append(fact); continue
        single={'key_facts':[copy.deepcopy(fact)]}
        if 'qa_pairs' in data:
            # Validate only referenced pairs; duplicates still rejected.
            single['qa_pairs']=[p for p in data['qa_pairs'] if isinstance(p,dict) and p.get('id') in fact.get('qa_ids',[])]
        try:
            result['key_facts'].extend(resolve_selections(single,segments)['key_facts'])
        except (ValueError,KeyError,TypeError,IndexError):
            # Existing quality gate blocks this placeholder before any persistence of candidates.
            result['key_facts'].append({'label':fact.get('label',''), 'value':'编号选择未通过校验',
                                       'segment_ids':[segments[0]['segment_id']], 'evidence':[]})
    return result

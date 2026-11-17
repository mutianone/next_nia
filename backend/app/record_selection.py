"""Resolve model-selected utterance IDs to immutable source text, never model prose."""
from itertools import groupby, pairwise
import re

from .record_qa_boundaries import contains_question_prompt, question_answer_cuts, question_tail_cut


ROLE_MARKER = re.compile(r'(?:^|(?<=[。！？!?，,；;\s])|(?<=[吗嗎呢])(?=答\s*[:：]))\s*(被询问人|询问人|民警|当事人|翻译|问|答)\s*[:：]\s*')
ACOUSTIC_MARKER = re.compile(r'\[说话人 ([A-Za-z0-9_-]{1,32})\]\s*')


QUOTED_SPEECH = re.compile(r'“[^”]*(?:”|$)|「[^」]*(?:」|$)|『[^』]*(?:』|$)|"[^"]*(?:"|$)')


def role_ranges(text, role):
    """Yield source ranges, including unassigned speech before a role marker."""
    # A reported conversation can itself contain literal 问： and 答：.
    quoted = QUOTED_SPEECH.finditer(text)
    span = next(quoted, None)
    markers = []
    for marker in ROLE_MARKER.finditer(text):
        while span and span.end() <= marker.start(1):
            span = next(quoted, None)
        if not span or marker.start(1) < span.start():
            markers.append(marker)
    if not markers:
        yield 0, len(text), role
        return
    if markers[0].start():
        yield 0, markers[0].start(), 'unknown'
    for i, marker in enumerate(markers):
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        role = ('interviewer' if marker[1] in ('问', '询问人', '民警')
                else 'interpreter' if marker[1] == '翻译' else 'interviewee')
        yield marker.end(), end, role


def repeated_particle_cut(question, answer):
    # Word questions may be echoed naturally ("多少我记不清"). Only a
    # duplicated question-particle ending qualifies for automatic extraction.
    if re.search(r'[吗嗎][，。,.？！?!；;：:\s]*$', question):
        cut = question_tail_cut(question, answer)
        if cut is not None:
            tail = answer[:cut].strip()
            if re.fullmatch(r'[^否不没无未非别莫勿难，。！？?!；;\s]{0,2}[吗嗎]', tail):
                if tail != question.rstrip('。？！?! ').strip():
                    return cut
    return None


def source_units(segments):
    units=[]
    for segment in segments:
        text = segment['text']
        role = segment.get('speaker', 'unknown')
        if role == 'unknown' and segment.get('speaker_id') is not None:
            role = 'unconfirmed_voice'
        ranges = role_ranges(text, role)
        for begin,finish,role in ranges:
            if ACOUSTIC_MARKER.search(text, begin, finish):
                role = 'unconfirmed_voice'
            if role == 'interviewee' and units and units[-1]['role'] == 'interviewer':
                cut = repeated_particle_cut(units[-1]['text'], text[begin:finish])
                if cut is not None:
                    begin += cut
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


def source_spans(units):
    """Exact text ranges for an ordered sequence of units."""
    spans = []
    for sid, group in groupby(units, key=lambda u: u['segment_id']):
        clip = list(group)
        spans.append((sid, clip[0]['start'], clip[-1]['end']))
    return tuple(spans)


def answer_blocks(segments, units):
    """Index multi-clip replies by unit. An empty block requires boundary review."""
    from .record_speaker_turns import same_contiguous_voice
    positions = {s['segment_id']: i for i, s in enumerate(segments)}
    blocks = {}
    for role, sequence in groupby(units, key=lambda u: u['role']):
        if role != 'interviewee':
            continue
        members = list(sequence)
        spans = source_spans(members)
        if len(spans) == 1:
            continue
        indices = [positions[sid] for sid, _, _ in spans]
        # Historical manually entered clips can describe different topics and
        # have no ASR run from which to verify a shared speaker.
        if not any(segments[i].get('recognition_run_id') for i in indices):
            continue
        verified = all(b == a+1 and same_contiguous_voice(segments[a], segments[b]) for a,b in pairwise(indices))
        block = spans if verified else ()
        for u in members:
            blocks[u['id']] = block
    return blocks


def resolve_selections(data, segments):
    if not isinstance(data,dict): return data
    if 'qa_pairs' not in data:
        if not any(isinstance(f,dict) and 'qa_ids' in f for f in data.get('key_facts',[])): return data
        data['qa_pairs']=paired_options(segments)
    units=source_units(segments); indexed={u['id']:(i,u) for i,u in enumerate(units)}
    blocks=answer_blocks(segments,units)
    pairs=data.pop('qa_pairs')
    if not isinstance(pairs,list) or len(pairs)>128: raise ValueError('invalid pairs')
    table={}
    texts = {s['segment_id']:s['text'] for s in segments}
    def selected(ids):
        if not isinstance(ids,list) or not ids or len(ids)>100: raise ValueError('invalid units')
        first=indexed[ids[0]][0]
        members=units[first:first+len(ids)]
        if ids!=[u['id'] for u in members]: raise ValueError('noncontiguous units')
        return members
    for pair in pairs:
        if not isinstance(pair,dict) or set(pair)!={'id','question_ids','answer_ids'}: raise ValueError('invalid pair')
        if not isinstance(pair['id'],str) or pair['id'] in table: raise ValueError('duplicate pair')
        questions = selected(pair['question_ids'])
        answers = selected(pair['answer_ids'])
        if indexed[answers[0]['id']][0] != indexed[questions[-1]['id']][0]+1:
            raise ValueError('unpaired units')
        (qid, start, end), = source_spans(questions)
        spans = source_spans(answers)
        block = blocks.get(answers[0]['id'])
        if (block is not None or len(spans)>1) and spans != block:
            raise ValueError('incomplete or unverified reply')
        evidence = [{'segment_id':sid, 'question_segment_id':qid, 'question':texts[qid][start:end],
                     'answer':texts[sid][a:b], 'start':a, 'end':b, 'question_start':start} for sid,a,b in spans]
        table[pair['id']] = evidence
    for fact in data.get('key_facts',[]):
        if 'qa_ids' not in fact: continue
        if set(fact)!={'label','qa_ids'} or not str(fact['label']).startswith('q'): raise ValueError('invalid selection')
        ids=fact.pop('qa_ids')
        if not isinstance(ids,list) or not ids or len(ids)>30 or len(set(ids))!=len(ids): raise ValueError('invalid qa refs')
        evidence=[item for x in ids for item in table[x]]
        value = '\n'.join(e['answer'] for e in evidence)
        source_ids = list(dict.fromkeys(s for e in evidence for s in (e['question_segment_id'], e['segment_id'])))
        if len(evidence)>30 or len(source_ids)>30 or len(value)>2000:
            raise ValueError('reply exceeds field limits')
        fact.update(value=value, evidence=evidence, segment_ids=source_ids)
    return data


def is_question_unit(text):
    return bool(re.search(r'[？?]$|[吗嗎][。！!\s]*$', text))


def officer_question_contexts(segments):
    """Whole, adjacent, role-confirmed ASR clips preceding a respondent."""
    from .record_speaker_turns import same_contiguous_voice
    contexts, block = {}, []
    for s in segments:
        if s.get('speaker') == 'interviewer' and not ROLE_MARKER.search(s['text']) and not ACOUSTIC_MARKER.search(s['text']):
            if block and not same_contiguous_voice(block[-1], s):
                block = []
            block.append(s)
        else:
            if len(block)>1 and s.get('speaker')=='interviewee':
                # An empty entry withholds this candidate from automatic filling.
                count = sum(is_question_unit(clause) for part in block for clause in re.findall(r'[^。！？!?\n]+[。！？!?]*', part['text']))
                last = block[-1]
                same_recording = all(s.get(key)==last.get(key) for key in ('recognition_run_id','channel_id'))
                contexts[last['segment_id']] = block if count<=1 and same_recording and len(block)<30 else []
            block = []
    return contexts


def paired_options(segments):
    """Confirmed turn boundaries outrank punctuation. Unknown voices never imply roles."""
    units=source_units(segments); pairs=[];i=0
    contexts=officer_question_contexts(segments)
    blocks=answer_blocks(segments, units)
    while i<len(units):
        unit=units[i]; role=unit['role']
        if role=='interviewer':
            questions=[unit];j=i+1
            while j<len(units) and units[j]['role']=='interviewer' and units[j]['segment_id']==unit['segment_id']:
                questions.append(units[j]);j+=1
            answers=[]
            while j<len(units) and units[j]['role']=='interviewee':
                if answers and units[j]['segment_id']!=answers[0]['segment_id'] and answers[0]['id'] not in blocks:
                    break
                answers.append(units[j]);j+=1
            i=j
            if answers and blocks.get(answers[0]['id']) == ():
                continue
        elif role=='unknown' and is_question_unit(unit['text']):
            questions=[unit];answers=[];j=i+1
            while j<len(units):
                following=units[j]
                if following['role']!='unknown' or is_question_unit(following['text']) or question_answer_cuts(following['text']):break
                if answers and following['segment_id']!=answers[0]['segment_id']:break
                answers.append(following);j+=1
            i+=1
        else:i+=1;continue
        if answers and contexts.get(unit['segment_id']) != [] and not (role=='unknown' and any(contains_question_prompt(u['text']) for u in answers)):
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

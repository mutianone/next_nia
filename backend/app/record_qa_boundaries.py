"""Lossless, review-only boundaries for possible answer/question transitions."""
import re


# A boundary is a source offset, never a speaker identification. Keep negative
# and qualified response prefixes intact (e.g. "不确定，但我记得……").
_RESPONSE = re.compile(
    r'(?:答(?:复)?[：:]|我|是的|对的|没错|不是|没有|不确定|不清楚|不知道|'
    r'不记得|记不清|记不住|确定|确认|知道|清楚|听明白|明白|记得|有的|嗯|'
    r'(?:是|对|没|有|不)(?=$|[，。,.！!\s]))'
)
_QUESTION_END = re.compile(r'(?:[？?]|[吗嗎][？?。.!！，,：:；;]*)\s*')
_QUESTION_WORD = re.compile(
    r'(?:多少|哪[里儿个天]|什么|几[个岁次点天]|怎么[样办]|对不对|是不是|有没有|记不记得|清不清楚)$'
)
# A brief reply can also share its span with the next question. The question
# must have both a recognizable opening and an interrogative ending.
_NEXT_QUESTION = re.compile(
    r'(?:(?:是的|对的|没错)[，,。.\s]*|(?:没有|不是|我确定|我不确定|不清楚|不知道)[，,。.]\s*)'
    r'(?P<question>(?:你|您|确定|确认|是否|有没有)[^。？！?；;\n]{0,60}?(?:吗|嗎|[？?]))'
)
_QUOTES = ('“', '”', '「', '」', '『', '』', '"', '问我', '问他', '问她')


def question_answer_cuts(text):
    """Possible starts of replies inside one ASR span, in original coordinates."""
    if any(mark in text for mark in _QUOTES):
        return []
    return [m.end() for m in _QUESTION_END.finditer(text) if _RESPONSE.match(text, m.end())]


def short_reply_question_cuts(text):
    if any(mark in text for mark in _QUOTES):
        return []
    return [match.start('question') for match in _NEXT_QUESTION.finditer(text)
            if match.start() == 0 or text[match.start() - 1] in '。？！?!，,吗嗎：: \n']


def question_tail_cut(question, answer):
    """Match a repeated interrogative suffix against the adjacent question.

    A bare shared noun (e.g. 护照 in 护照我带了) is also a natural way to
    answer. Require interrogative wording as well as a separate reply onset.
    """
    if any(mark in answer for mark in _QUOTES):
        return None
    punctuation = '，。,.？！?!；;：: \t\r\n'
    stem = question.rstrip(punctuation)
    if not stem or not (stem.endswith(('吗', '嗎', '呢')) or _QUESTION_WORD.search(stem)):
        return None
    start = len(answer) - len(answer.lstrip())
    for length in range(min(12, len(stem)), 0, -1):
        suffix = stem[-length:]
        if not answer.startswith(suffix, start):
            continue
        # A truncated suffix such as 不对 from 对不对 can itself be the
        # interviewee's denial. Only flag a suffix that is still interrogative.
        if not suffix.endswith(('吗', '嗎', '呢')) and not _QUESTION_WORD.search(suffix):
            continue
        tail_end = start + length
        cut = len(answer) - len(answer[tail_end:].lstrip(punctuation))
        if _RESPONSE.match(answer, cut):
            return cut
    return None


def split_candidates(sources,provider=None,model=None,key=None):
    splits=[]
    for source in sources:
        text=source['text']
        # Quoted/indirect questions are not reliable speaker transitions.
        if any(mark in text for mark in _QUOTES):continue
        cuts=set(question_answer_cuts(text))
        cuts.update(short_reply_question_cuts(text))
        for m in re.finditer(r'[，。；](?=(?:你|您|[^，。？！；]{1,8}本人)[^，。？！；]{0,30}[？?])',text):
            before=text[:m.end()]
            if re.search(r'(?:我|他|她|没有|不是|不清楚|记不住)',before):cuts.add(m.end())
        # Missing punctuation can merge an uncertainty answer, a travel question,
        # and the next answer. These are proposals, never confirmed roles.
        m=re.search(r'(?:记不住|不清楚|不知道|不确定)((?:来|去|到|离开|进入)[^，。？！；]{0,14}(?:之后|以后)[^，。？！；]{0,12}(?:哪里|哪些地方)[，？?])',text)
        if m:
            cuts.add(m.start(1));cuts.add(m.end(1))
        cuts=sorted(c for c in cuts if 0<c<len(text))
        if 0 < len(cuts) <= 7:
            edges=[0,*cuts,len(text)]
            splits.append({'segment_id':source['id'],'parts':[text[a:b] for a,b in zip(edges,edges[1:])]})
    return validate_splits({'splits':splits},sources)


def validate_splits(data,sources):
    indexed={s['id']:s for s in sources};splits={}
    if not isinstance(data,dict) or set(data)!={'splits'} or not isinstance(data['splits'],list):raise ValueError()
    for item in data['splits']:
        sid=item['segment_id']
        if sid not in indexed or sid in splits:raise ValueError()
        if 'starts' in item:
            anchors=item['starts'];text=indexed[sid]['text']
            if not isinstance(anchors,list) or not 1<=len(anchors)<=7:raise ValueError()
            if any(not isinstance(a,str) or not a or text.count(a)!=1 for a in anchors):raise ValueError()
            cuts=[text.index(a) for a in anchors]
            if cuts!=sorted(set(cuts)) or cuts[0]==0:raise ValueError()
            edges=[0,*cuts,len(text)]
            parts=[text[a:b] for a,b in zip(edges,edges[1:])]
        else:parts=item['parts']
        if sid not in indexed or sid in splits or not isinstance(parts,list) or not 2<=len(parts)<=8:raise ValueError()
        if any(not isinstance(p,str) or not p.strip() for p in parts):raise ValueError()
        if ''.join(parts)!=indexed[sid]['text']:raise ValueError()
        splits[sid]=parts
    clean=[];origins={}
    for source in sources:
        for i,part in enumerate(splits.get(source['id'],[source['text']])):
            sid=f'boundary-{len(clean)}';origins[sid]=source['id']
            clean.append({**source,'id':sid,'text':part})
    if len(clean)>500:raise ValueError()
    return clean,origins


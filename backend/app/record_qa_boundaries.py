"""Lossless, review-only boundaries for possible answer/question transitions."""
import re


def split_candidates(sources,provider=None,model=None,key=None):
    splits=[]
    for source in sources:
        text=source['text']
        # Quoted/indirect questions are not reliable speaker transitions.
        if any(mark in text for mark in ('“','”','「','」','"','问我','问他','问她')):continue
        cuts=set()
        for m in re.finditer(r'[，。；](?=(?:你|您|[^，。？！；]{1,8}本人)[^，。？！；]{0,30}[？?])',text):
            before=text[:m.end()]
            if re.search(r'(?:我|他|她|没有|不是|不清楚|记不住)',before):cuts.add(m.end())
        # Missing punctuation can merge an uncertainty answer, a travel question,
        # and the next answer. These are proposals, never confirmed roles.
        m=re.search(r'(?:记不住|不清楚|不知道|不确定)((?:来|去|到|离开|进入)[^，。？！；]{0,14}(?:之后|以后)[^，。？！；]{0,12}(?:哪里|哪些地方)[，？?])',text)
        if m:
            cuts.add(m.start(1));cuts.add(m.end(1))
        cuts=sorted(c for c in cuts if 0<c<len(text))
        if cuts:
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


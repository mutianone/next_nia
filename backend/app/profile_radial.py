"""Read-only radial projections. Explicit statements only; never infer kinship."""
import re
from sqlalchemy import select
from .people import Person

ALIASES = {'父亲':'父亲','爸爸':'父亲','母亲':'母亲','妈妈':'母亲','丈夫':'丈夫','老公':'丈夫','妻子':'妻子','老婆':'妻子','介绍人':'介绍人','介绍来的人':'介绍人','哥哥':'哥哥','弟弟':'弟弟','姐姐':'姐姐','妹妹':'妹妹','儿子':'儿子','女儿':'女儿'}
ROLE = '|'.join(sorted(ALIASES, key=len, reverse=True))
NAME = r'([\u4e00-\u9fff·]{2,12}|[A-Za-z][A-Za-z .\-]{1,60})'
# Require an explicit first-person relationship and a clear end to the name.
PATTERN = re.compile(r'(?:我(?:的)?|本人(?:的)?)('+ROLE+r')(?:的姓名)?(?:叫做|名叫|叫|是|为|：|:)\s*'+NAME+r'(?=[，。；、,;\n]|$)')

def build_radial(session, profile):
    person = profile['person']
    nodes = []
    def add(role, name, source, target=None, verified=False, key=None):
        name=name.strip()
        if not name or name in ('不知道','不清楚','不详','没有','未填写','无') or any(x in name for x in ('不是','不知道','已经','在中国','是中国','没有','工作')): return
        # Without an existing explicit link, names are only navigation candidates.
        identity=key or (target or name,role)
        existing=next((n for n in nodes if n['_key']==identity),None)
        if existing is None and target is None:
            linked=[n for n in nodes if n['target_id'] and n['name']==name and n['role']==role]
            if len(linked)==1: existing=linked[0]
        if existing:
            if source not in existing['sources']:existing['sources'].append(source)
            return
        candidates=[]
        if not target:
            matches=session.scalars(select(Person).where(Person.id!=person['id'],Person.archived==False,Person.name==name).order_by(Person.id).limit(20))
            candidates=[{'id':p.id,'name':p.name,'nationality':p.nationality,'document_hint':('••••'+p.document_number[-4:]) if p.document_number else '未填写'} for p in matches]
        nodes.append({'_key':identity,'id':f'relation-{len(nodes)}','role':role,'name':name,'target_id':target,'verified':verified,'sources':[source],'candidates':candidates})
    for r in profile['relations']:
        if r['archived'] or r['other']['archived']:continue
        # Reverse directional labels must not be presented as this person's father/mother.
        role=r['label'] if r['person_id']==person['id'] else '关联人员（反向登记）'
        add(role,r['other']['name'],{'title':'关系登记','text':r['source']},r['other']['id'],r['verified'])
    for f in profile['facts']:
        if f['archived']:continue
        role=ALIASES.get(f['label'].removesuffix('姓名').strip())
        if role and re.fullmatch(NAME,f['value'].strip()):add(role,f['value'],{'title':f['label'],'text':f['source']},verified=f['verified'])
    for source in profile['sources']:
        if source['type']!='record':continue
        text=source.get('excerpt','')
        # Standard interview question/answer pairs also provide explicit attribution.
        qa=re.compile(r'问[:：][^\n？?]*(?:你|您)(?:的)?('+ROLE+r')(?:叫(?:什么)?名字|叫什么|姓名是什么)[^\n]*[\n]+\s*答[:：]\s*'+NAME+r'(?=[，。；,;\n]|$)')
        for match in qa.finditer(text):
            add(ALIASES[match[1]],match[2],{'title':source['title'],'record_id':source['id'],'text':match[0]})
        for line in text.splitlines():
            if re.match(r'^\s*(问|询问人)[:：]',line):continue
            for intro in re.finditer(r'(?:是|由)'+NAME+r'介绍我(?:来|入)',line):
                add('介绍人',intro[1],{'title':source['title'],'record_id':source['id'],'text':line.strip()})
            for m in PATTERN.finditer(line):
                add(ALIASES[m[1]],m[2],{'title':source['title'],'record_id':source['id'],'text':line.strip()})
    for n in nodes:n.pop('_key')
    return {'relations':nodes, 'personal': resolve_personal(profile)}


# Only explicit self-descriptions are eligible. Questions, guesses and relative
# dates are not promoted to personal facts. Conflicts stay visible for review.
PERSONAL_PATTERNS = {
 'name': r'我(?:的中文名|的名字|名字)?叫([^，。；？?\n]{2,20})',
 'english_name': r'我的(?:英文名|外文名)(?:是|叫)\s*([A-Za-z ]{2,60})',
 'nationality': r'我的国籍是([^，。；？?\n]{1,20})',
 'address': r'我(?:现在|目前)?(?:居住在|住在|的现住址是)([^，。；？?\n]{2,100})',
 'phone': r'我的(?:联系电话|手机号|电话号码)是\s*([0-9 +－-]{5,25})',
 'purpose': r'我(?:这次)?来(?:中国|华)的(?:原因|目的)是([^，。；？?\n]{1,60})',
 'document_number': r'我的护照(?:号码|号)是\s*([A-Za-z0-9]{5,30})',
 'gender': r'我的性别是(男|女)',
 'birth_date': r'我的出生日期是(\d{4}年\d{1,2}月\d{1,2}日)',
 'entry_date': r'我(?:这次)?的入境日期是(\d{4}年\d{1,2}月\d{1,2}日)',
 'exit_date': r'我(?:这次)?的出境日期是(\d{4}年\d{1,2}月\d{1,2}日)',
}
TEMPLATE_KEYS = {'name':'name','address':'address','phone':'contact','gender':'gender'}
EMPTY = ('', 'unknown', '未填写', '不详', '未知', '未登记')
def normalize_address(value):
    value = re.sub(r'^我(?:现在|目前)?(?:居住在|住在|的现住址是)', '', value).strip()
    endpoint = re.match(r'(.+?(?:路|街|巷).+?号(?:\d+(?:室|楼))?)', value)
    return endpoint[1] if endpoint else value

def resolve_personal(profile):
    person=profile['person']; result={}
    for field, pattern in PERSONAL_PATTERNS.items():
        stored=person.get(field)
        if field in ('entry_date','exit_date'):
            dates=[s.get(field) for s in person.get('stays',[]) if s.get(field)]
            stored=max(dates) if dates else None
        if stored is not None and str(stored).strip() not in EMPTY:
            result[field]={'value':stored,'source':'person','sources':[],'conflict':False}
            continue
        candidates=[]
        for source in profile.get('sources',[]):
            if source['type']!='record':continue
            text=source.get('excerpt','')
            for line in text.splitlines():
                if re.match(r'^\s*(问|询问人)[:：]',line):continue
                for match in re.finditer(pattern,line):
                    value=match[1].strip()
                    if field=='address':
                        # ASR may omit punctuation between a house number and
                        # the officer repeating it. Stop at the complete address.
                        value=normalize_address(value)
                    if any(t in value for t in ('你现在','你是','还是','叫什么')):continue
                    if value in EMPTY or any(t in value for t in ('不确定','可能','记不清','不知道')):continue
                    if field=='gender':value={'男':'male','女':'female'}[value]
                    candidates.append({'value':value,'record_id':source['id'],'title':source['title'],'text':match[0]})
            # Current generated template values also cover free-form Q/A. The
            # endpoint only supplies values whose transcript fingerprint matches.
            key=TEMPLATE_KEYS.get(field)
            value=source.get('template_values',{}).get(key,'').strip() if key else ''
            if value and value not in EMPTY:
                if field=='gender':value={'男':'male','女':'female'}.get(value,value)
                if field=='address':value=normalize_address(value)
                candidates.append({'value':value,'record_id':source['id'],'title':source['title'],'text':'笔录模板字段：'+key})
        values=list(dict.fromkeys(c['value'] for c in candidates))
        if values:
            result[field]={'value':values[0] if len(values)==1 else '', 'source':'record','sources':candidates,'conflict':len(values)>1}
    return result

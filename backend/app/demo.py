"""Offline presentation datasets, with an internal ownership ledger and atomic cleanup."""
import hashlib
import json
import random
import wave
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from sqlalchemy import DateTime, Integer, JSON, delete, func, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from .database import Base, AuditEvent
from .people import InputModel, PeopleRoute, Person, PersonStay, business_today, timestamp, utc_now
from .alerts import Alert, AlertHistory, AlertRule, StayFacts, assess
from .documents import DocumentTemplate, DocumentDraft, DocumentHistory, template_json
from .knowledge import KnowledgeDocument, _build_chunks
from .profiles import ProfileFact, ProfileRelation
from .records import (RecordSession, AudioClip, RecordSegment, RecordAnalysis, RecordVersion,
                      TranscriptionAttempt, TEMPLATES, render_text, detail)

ASSETS = Path(__file__).resolve().parents[1] / 'assets'
SOURCE = 'https://www.nia.gov.cn/n794014/n1050181/n1050479/c1013311/content.html'


class DemoDataset(Base):
    __tablename__ = 'demo_datasets'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    manifest: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Confirmation(InputModel):
    confirmed: bool = False
    batch_id: str | None = Field(default=None, max_length=36)


router = APIRouter(prefix='/api/demo-data', tags=['演示资料管理'], route_class=PeopleRoute)


def require_test(request, confirmation=None):
    if request.app.state.active_settings.environment.mode != 'test':
        raise HTTPException(409, '演示资料功能仅在测试版开放；正式数据库不会被修改。')
    if confirmation is not None and not confirmation.confirmed:
        raise HTTPException(422, '请确认本次资料操作。')


@contextmanager
def dataset_session(engine):
    with Session(engine, expire_on_commit=False) as session:
        try:
            if engine.dialect.name == 'sqlite':
                session.execute(text('BEGIN IMMEDIATE'))
            else:
                # Currently test mode is SQLite; keep locking correct for future adapters.
                for table in Base.metadata.sorted_tables:
                    session.execute(text(f'LOCK TABLE "{table.name}" IN SHARE ROW EXCLUSIVE MODE'))
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise


def status(session):
    batch = session.get(DemoDataset, 1)
    if batch is None:
        return {'active': False, 'batch_id': None, 'created_at': None, 'counts': {}}
    counts = {}
    for name, ids in batch.manifest['rows'].items():
        table = Base.metadata.tables[name]
        counts[name] = session.scalar(select(func.count()).select_from(table).where(list(table.primary_key)[0].in_(ids)))
    return {'active': True, 'batch_id': batch.manifest['batch_id'], 'created_at': timestamp(batch.created_at), 'counts': counts}


@router.get('')
def get_status(request: Request):
    with Session(request.app.state.engine) as session:
        return {'enabled': request.app.state.active_settings.environment.mode == 'test', **status(session)}


NAMES = [
    ('安娜·莱昂','Anna Leon','西班牙','female'),('马丁·罗森','Martin Rosen','德国','male'),
    ('索菲亚·莫里','Sofia Mori','意大利','female'),('卢卡斯·韦伯','Lucas Weber','德国','male'),
    ('艾米莉·杜兰','Emily Durand','法国','female'),('亚历克斯·格林','Alex Green','英国','male'),
    ('米娅·安德森','Mia Anderson','加拿大','female'),('丹尼尔·科斯塔','Daniel Costa','葡萄牙','male'),
    ('丽娜·阿里','Lina Ali','马来西亚','female'),('伊森·布鲁克','Ethan Brooke','澳大利亚','male'),
    ('艾拉·贝内特','Ella Bennett','新西兰','female'),('诺亚·佩雷拉','Noah Pereira','巴西','male'),
    ('娜迪娅·萨米尔','Nadia Samir','埃及','female'),('阿迪·维拉','Adi Wira','印度尼西亚','male'),
    ('克拉拉·诺瓦','Clara Nova','西班牙','female'),('奥利弗·里德','Oliver Reed','英国','male'),
    ('莉莉·陈','Lily Chen','新加坡','female'),('卡里姆·纳赛尔','Karim Nasser','阿联酋','male'),
    ('朱莉娅·贝克','Julia Beck','德国','female'),('雨果·勒鲁','Hugo Leroux','法国','male'),
    ('林美恩','Mei Eun Lim','马来西亚','female'),('山崎悠真','Yuma Yamazaki','日本','male'),
    ('金智妍','Jiyeon Kim','韩国','female'),('朴俊河','Junha Park','韩国','male'),
    ('佐藤真央','Mao Sato','日本','female'),('托马斯·米勒','Thomas Miller','加拿大','male'),
    ('艾琳·沃克','Erin Walker','澳大利亚','female'),('拉斐尔·席尔瓦','Rafael Silva','巴西','male'),
    ('莉娅·费尔南德斯','Lia Fernandes','葡萄牙','female'),('阿米尔·哈桑','Amir Hassan','埃及','male'),
    ('维拉·桑托斯','Vera Santos','菲律宾','female'),('阿尔文·谭','Alvin Tan','新加坡','male'),
    ('卡米拉·马丁','Camila Martin','西班牙','female'),('路易斯·贝尔','Louis Bell','英国','male'),
    ('梅丽莎·吴','Melissa Wu','新西兰','female'),('安德烈·洛佩斯','Andre Lopes','葡萄牙','male'),
]
SCENARIOS = ['study', 'marriage', 'residence', 'violation']
PURPOSES = {'study':'学习', 'marriage':'探亲', 'residence':'工作', 'violation':'商务访问'}
ADDRESSES = {'study':'海州市云川大学国际学生公寓', 'marriage':'海州市滨河社区梧桐苑', 'residence':'海州市高新园区人才公寓', 'violation':'海州市临港路悦庭酒店'}


def populate(session, today):
    rng = random.Random(28617)
    batch_id = str(uuid4())
    rows = defaultdict(list)

    def add(row):
        session.add(row)
        session.flush()
        pk = list(row.__table__.primary_key)[0].name
        rows[row.__tablename__].append(getattr(row, pk))
        return row

    def moment(days, hour=10):
        # Stable local working-hour timestamps, never later than generation time.
        value = datetime.combine(today-timedelta(days=days), time(hour, 15), ZoneInfo('Asia/Shanghai')).astimezone(timezone.utc)
        return min(value, utc_now()-timedelta(minutes=5))

    people, stays, alerts = [], [], {}
    rule = session.get(AlertRule, 1) or AlertRule(upcoming_days=30, urgent_days=7)
    existing_numbers = set(session.scalars(select(Person.document_number)))
    for i, (name, english, country, gender) in enumerate(NAMES):
        scenario = SCENARIOS[i % 4]
        number = f'{chr(65+i%24)}{rng.randrange(10000000,99999999)}'
        while number in existing_numbers:
            number = f'{chr(65+i%24)}{rng.randrange(10000000,99999999)}'
        existing_numbers.add(number)
        entered = today-timedelta(days=(i*7)%29+1)
        # Latest week has varying entries; older, closed stays make history realistic.
        p = add(Person(id=str(uuid4()), code=f'NIA-{today.year}-{batch_id[:4].upper()}{i+1:03}', name=name,
            english_name=english, nationality=country, gender=gender, birth_date=date(1985+i%15, i%12+1, i%25+1),
            document_number=number, document_expiry=today+timedelta(days=900+i*39),
            address=f'{ADDRESSES[scenario]} {101+i*3}室', purpose=PURPOSES[scenario], source=f'综合窗口登记表 {today.year}-{i+1:04}',
            verification='pending' if i%8==7 else 'verified', notes='纸质材料已按受理序号整理，后续变更以补充材料为准。',
            archived=i>=34, created_at=moment((today-entered).days), updated_at=moment(i%6)))
        until = today+timedelta(days=[-3,-1,0,3,7,18,45,90,120][i%9])
        if until < entered: until = entered+timedelta(days=1)
        exited = max(entered+timedelta(days=1),today-timedelta(days=i%6)) if i%6==5 else None
        stay = add(PersonStay(id=str(uuid4()), person_id=p.id, entry_date=entered, permitted_until=None if i%11==10 else until,
            exit_date=exited, permit_type={'study':'学习类居留证件','marriage':'Q2 签证','residence':'工作类居留证件','violation':'M 签证'}[scenario],
            permit_number=f'{today.year}{i+31:06}', purpose=p.purpose, source=f'护照入境页与停留居留证件核对表 {i+1:04}',
            created_at=p.created_at, updated_at=p.updated_at))
        facts = add(StayFacts(stay_id=stay.id, visa_valid_until=today+timedelta(days=180+i), verified=p.verification=='verified',
            extension_status='pending' if i%12==8 else 'none', receipt_valid_until=today+timedelta(days=8) if i%12==8 else None,
            source='证件原件、住宿登记回执及窗口材料核对', updated_at=p.updated_at))
        candidate = assess(p, stay, facts, [stay.id] if not exited else [], rule, today)
        if candidate:
            alert = add(Alert(id=str(uuid4()), person_id=p.id, stay_id=stay.id, **candidate,
                status=['open','in_progress','open','closed'][i%4], auto_closed=False,
                created_at=moment(i%12), updated_at=moment(i%3), last_seen_at=utc_now()))
            alerts[i] = alert
            add(AlertHistory(alert_id=alert.id, action='created', note=candidate['reason'], created_at=alert.created_at))
            if alert.status!='open':
                add(AlertHistory(alert_id=alert.id, action='close' if alert.status=='closed' else 'start',
                    note='已核对现有材料并登记后续跟进安排。', created_at=alert.updated_at))
        if i < 18:
            add(PersonStay(person_id=p.id, entry_date=entered-timedelta(days=150+i*2), permitted_until=entered-timedelta(days=60),
                exit_date=entered-timedelta(days=72+i%13), permit_type='短期停留', source='历史出入境记录及当事人提交材料',
                purpose='交流访问', created_at=moment((today-entered).days+150), updated_at=moment((today-entered).days+60)))
        for kind,label,value in [('purpose','来华事由',p.purpose+'，已登记接待单位与在华安排。'),('residence','现居住地',p.address)]:
            add(ProfileFact(person_id=p.id, kind=kind, label=label, value=value, source_type='stay', source_id=stay.id,
                source=stay.source, source_snapshot={'id':stay.id,'source_type':'stay','entry_date':str(entered),'permitted_until':str(stay.permitted_until) if stay.permitted_until else None,'captured_at':timestamp(p.updated_at)},
                verified=p.verification=='verified', review_note='已核对登记表与提交材料。' if p.verification=='verified' else '',
                occurred_on=entered, created_at=p.created_at, updated_at=p.updated_at))
        add(AuditEvent(action='person.created', details={'person_id':p.id}, created_at=p.created_at))
        people.append(p); stays.append(stay)
    for a,b in [(0,4),(0,8),(4,12),(1,5),(1,9),(5,13),(2,6),(2,10),(6,14),(3,7),(3,11),(7,15)]:
        label = '同校学习' if a%4==0 else '家庭联系' if a%4==1 else '同单位任职' if a%4==2 else '同项目接待'
        add(ProfileRelation(person_id=people[a].id, target_id=people[b].id, label=label,
            source='双方登记表及随附联系材料', verified=True, review_note='已逐项核对双方登记的联系关系。', created_at=moment(6), updated_at=moment(2)))

    # Authentic public legal excerpts; no invented legislation or full-law claim.
    articles = [
        ('停留期限与延期','第二十九条','外国人所持签证注明的停留期限不超过一百八十日的，持证人凭签证并按照签证注明的停留期限在中国境内停留。\n需要延长签证停留期限的，应当在签证注明的停留期限届满七日前向停留地县级以上地方人民政府公安机关出入境管理机构申请，按照要求提交申请事由的相关材料。经审查，延期理由合理、充分的，准予延长停留期限；不予延长停留期限的，应当按期离境。\n延长签证停留期限，累计不得超过签证原注明的停留期限。'),
        ('居留期限延长','第三十二条','在中国境内居留的外国人申请延长居留期限的，应当在居留证件有效期限届满三十日前向居留地县级以上地方人民政府公安机关出入境管理机构提出申请，按照要求提交申请事由的相关材料。经审查，延期理由合理、充分的，准予延长居留期限；不予延长居留期限的，应当按期离境。'),
        ('住宿登记','第三十九条','外国人在中国境内旅馆住宿的，旅馆应当按照旅馆业治安管理的有关规定为其办理住宿登记，并向所在地公安机关报送外国人住宿登记信息。\n外国人在旅馆以外的其他住所居住或者住宿的，应当在入住后二十四小时内由本人或者留宿人，向居住地的公安机关办理登记。'),
        ('在华工作许可','第四十一条','外国人在中国境内工作，应当按照规定取得工作许可和工作类居留证件。任何单位和个人不得聘用未取得工作许可和工作类居留证件的外国人。\n外国人在中国境内工作管理办法由国务院规定。'),
    ]
    for i,(topic,article,body) in enumerate(articles):
        identifier = str(uuid4()); content = f'{article}　{body}'; raw=content.encode()
        sections,chunks=_build_chunks(identifier,[(content,None)])
        add(KnowledgeDocument(id=identifier,title=f'中华人民共和国出境入境管理法 · {topic}（节选）', version='2012年公布文本',authority='全国人民代表大会常务委员会',
            doc_number='中华人民共和国主席令第五十七号', category='法律', source=SOURCE, series_key=f'{batch_id}-article-{i}',
            published_on=date(2012,6,30), effective_from=date(2013,7,1), status='published', allow_external=True,
            filename=f'{topic}.txt',file_type='txt',size_bytes=len(raw),raw_file=raw,sha256=hashlib.sha256(raw).hexdigest(),chunk_count=len(chunks),created_at=moment(20-i)))
        for row in sections+chunks:add(row)

    template=add(DocumentTemplate(id=str(uuid4()),name='停留居留材料核对记录',version='1.0',kind='internal',status='approved',
        source='综合窗口内部材料核对样式',review_note='仅用于内部材料整理，不代替执法决定或法定文书。',
        body='停留居留材料核对记录\n\n姓名：{{person_name}}\n国籍：{{nationality}}\n证件号码：{{document_number}}\n入境日期：{{entry_date}}\n登记期限：{{permitted_until}}\n\n核对内容：{{facts}}\n后续安排：{{decision}}\n承办人：{{officer}}\n记录日期：{{document_date}}',created_at=moment(25),updated_at=moment(25)))
    for i,p in enumerate(people[:16]):
        stay=stays[i]; happened=moment(i%10)
        snap={key:getattr(p,key) for key in ('id','code','name','nationality','document_number','verification','source','archived')}
        snap.update(captured_at=timestamp(happened),stay={'id':stay.id,'source':stay.source,**{k:str(getattr(stay,k)) if getattr(stay,k) else None for k in ('entry_date','permitted_until','exit_date')}})
        document=add(DocumentDraft(template_id=template.id,person_id=p.id,stay_id=stay.id,alert_id=alerts[i].id if i in alerts else None,
            title=f'{p.name} · 停留居留材料核对记录',status='reviewed' if i%3==0 else 'draft',template_snapshot=template_json(template),person_snapshot=snap,
            fields={'document_date':str(today-timedelta(days=i%10)),'facts':f'已收取{p.purpose}相关材料，核对证件原件、入境记录和住宿登记。尚需补充的资料已向当事人说明。','decision':'补齐接待单位联系人资料，并按登记时间继续核对。','officer':['陈明、周颖','林远、李清'][i%2]},
            review_note='已逐项核对所列材料及记录内容。' if i%3==0 else '',reviewed_at=happened if i%3==0 else None,created_at=happened,updated_at=happened))
        add(DocumentHistory(document_id=document.id,action='reviewed' if i%3==0 else 'created',revision=1,status=document.status,note=document.review_note,created_at=happened))
        add(AuditEvent(action='document.created',details={'document_id':document.id},created_at=happened))

    scripts=json.loads((ASSETS/'interviews.json').read_text())
    for i,p in enumerate(people[:12]):
        scenario=SCENARIOS[i%4]; happened=moment(i%8)
        record=add(RecordSession(title=f'{p.name} · {PURPOSES[scenario]}事项询问',scenario=scenario,template_id=scenario,
            template_snapshot=next(x for x in TEMPLATES if x['id']==scenario),person_id=p.id,person_snapshot={'id':p.id,'name':p.name,'code':p.code},
            location=f'综合服务中心第 {i%3+1} 询问室',interviewers=['陈明、周颖','林远、李清'][i%2],occurred_at=happened,created_at=happened,updated_at=happened))
        raw=(ASSETS/(scenario+'.wav')).read_bytes()
        with wave.open(str(ASSETS/(scenario+'.wav'))) as audio:duration=int(audio.getnframes()/audio.getframerate()*1000)
        clip=add(AudioClip(record_id=record.id,filename=f'询问录音_{today:%Y%m%d}_{i+1:02}.wav',format='wav',mime_type='audio/wav',size_bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),raw_audio=raw,duration_ms=duration,created_at=happened))
        # Supplied written notes are manual source material, never AI successes.
        segments=[]
        for position,(speaker,content) in enumerate(scripts[scenario]):
            segments.append(add(RecordSegment(record_id=record.id,position=position,audio_id=clip.id,speaker=speaker,text=content,source='manual')))
        if i%3==0:
            record.reviewed_text=render_text(record,segments);record.status='reviewed';record.review_note='已结合原始录音逐项核对文字、角色及整理内容。';record.reviewed_at=happened
            session.flush()
        record_detail=detail(session,record)
        add(RecordVersion(record_id=record.id,revision=record.revision,action='reviewed' if record.status=='reviewed' else 'created',note=record.review_note,snapshot=record_detail,created_at=happened))
        add(AuditEvent(action='records.created',details={'record_id':record.id},created_at=happened))
    add(DemoDataset(id=1,manifest={'batch_id':batch_id,'rows':dict(rows)},created_at=utc_now()))
    # The ledger itself is deleted separately, and never included in its own JSON.
    return status(session)


@router.post('/generate')
def generate(body: Confirmation, request: Request):
    require_test(request,body)
    with dataset_session(request.app.state.engine) as session:
        if session.get(DemoDataset,1):
            return {'created':False,**status(session)}
        try:
            result=populate(session,business_today())
        except (OSError, wave.Error, json.JSONDecodeError):
            raise HTTPException(503,'本地演示资料文件缺失或损坏，本次生成已撤销。请检查随项目附带的录音与文字资料。') from None
        session.add(AuditEvent(action='demo.generated',details={'batch_id':result['batch_id'],'people':result['counts']['people']}))
        return {'created':True,**result}


def cleanup(session, batch):
    owned={name:set(ids) for name,ids in batch.manifest['rows'].items()}
    # Follow references only from a removed parent to its dependent rows. Never
    # traverse back to unrelated people, shared templates, accounts or settings.
    links = [(fk.parent, fk.column.table.name, None)
             for table in Base.metadata.sorted_tables for fk in table.foreign_keys]
    links += [(ProfileFact.source_id, parent, ProfileFact.source_type == kind)
              for kind, parent in [('record', 'record_sessions'), ('document', 'document_drafts'), ('stay', 'person_stays')]]
    links += [(DocumentDraft.alert_id, 'alerts', None),
              (DocumentDraft.copied_from, 'document_drafts', None)]
    changed = True
    while changed:
        changed = False
        for column, parent, condition in links:
            parents = owned.get(parent, set())
            if not parents:
                continue
            table = column.table
            pk = list(table.primary_key)[0]
            query = select(pk).where(column.in_(parents))
            if condition is not None:
                query = query.where(condition)
            selected = set(session.scalars(query))
            existing = owned.setdefault(table.name, set())
            if selected - existing:
                existing.update(selected)
                changed = True
    # Saved answers keep citation snapshots rather than database foreign keys.
    # Remove only turns citing deleted laws, retaining unrelated conversation turns.
    from .assistant_history import AssistantConversation, AssistantMessage
    documents = owned.get('knowledge_documents', set())
    affected_conversations = set()
    if documents:
        for message in session.scalars(select(AssistantMessage)):
            data = json.loads(message.data)
            if any(source.get('document_id') in documents
                   for source in (data.get('answer') or {}).get('sources', [])):
                owned.setdefault('assistant_messages', set()).add(message.id)
                affected_conversations.add(message.conversation_id)
        for conversation_id in affected_conversations:
            remaining = session.scalar(select(AssistantMessage.id).where(
                AssistantMessage.conversation_id == conversation_id,
                AssistantMessage.id.not_in(owned.get('assistant_messages', set()))).limit(1))
            if remaining is None:
                owned.setdefault('assistant_conversations', set()).add(conversation_id)
    for table in reversed(Base.metadata.sorted_tables):
        ids=owned.get(table.name)
        if ids:session.execute(delete(table).where(list(table.primary_key)[0].in_(ids)))
    session.delete(batch)


@router.post('/clear')
def clear(body: Confirmation, request: Request):
    require_test(request,body)
    with dataset_session(request.app.state.engine) as session:
        batch=session.get(DemoDataset,1)
        if batch is None:return {'cleared':False,**status(session)}
        if body.batch_id!=batch.manifest['batch_id']:
            raise HTTPException(409,'资料批次已变化，请刷新后重新确认。')
        cleanup(session,batch)
        session.add(AuditEvent(action='demo.cleared',details={'batch_id':body.batch_id}))
        session.flush()
        return {'cleared':True,**status(session)}

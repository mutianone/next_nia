"""Local aggregate reporting with explicit date and archive semantics."""
import csv
from datetime import date, datetime, time, timedelta, timezone
from io import StringIO
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .people import PeopleRoute, Person, PersonStay, business_today, csv_cell, timestamp, utc_now
from .alerts import Alert, AlertRule
from .documents import DocumentDraft
from .records import RecordSession
from .knowledge import KnowledgeDocument

router = APIRouter(prefix='/api/analytics', tags=['业务统计'], route_class=PeopleRoute)


def report(engine, start: date | None, end: date | None, include_archived: bool):
    end = end or business_today(); start = start or end-timedelta(days=29)
    if start < date(1900, 1, 1) or end > date(9998, 12, 31) or start > end or (end-start).days > 365:
        raise HTTPException(422, '请选择前后有序且不超过 366 天的统计区间。')
    zone = ZoneInfo('Asia/Shanghai')
    lower = datetime.combine(start, time.min, zone).astimezone(timezone.utc)
    upper = datetime.combine(end+timedelta(days=1), time.min, zone).astimezone(timezone.utc)
    conditions = [] if include_archived else [Person.archived.is_(False)]
    people_ids = select(Person.id).where(*conditions)
    with Session(engine) as session:
        def count(model, *where):
            return session.scalar(select(func.count()).select_from(model).where(*where)) or 0
        def groups(model, column, *where):
            return [{'label': label or '未填写', 'value': value} for label, value in session.execute(select(column, func.count()).select_from(model).where(*where).group_by(column).order_by(func.count().desc(), column)).all()]
        stay_scope = [PersonStay.person_id.in_(people_ids)]
        doc_scope = [DocumentDraft.person_id.in_(people_ids)] + ([] if include_archived else [DocumentDraft.status != 'archived'])
        record_scope = [(RecordSession.person_id.is_(None) | RecordSession.person_id.in_(people_ids))] + ([] if include_archived else [RecordSession.status != 'archived'])
        alert_scope = [Alert.person_id.in_(people_ids)]
        def created(model): return [model.created_at >= lower, model.created_at < upper]
        entries = dict(session.execute(select(PersonStay.entry_date, func.count()).where(*stay_scope, PersonStay.entry_date.between(start, end)).group_by(PersonStay.entry_date)).all())
        exits = dict(session.execute(select(PersonStay.exit_date, func.count()).where(*stay_scope, PersonStay.exit_date.between(start, end)).group_by(PersonStay.exit_date)).all())
        trend = [{'date': (day := start+timedelta(days=i)).isoformat(), 'entries': entries.get(day, 0), 'exits': exits.get(day, 0)} for i in range((end-start).days+1)]
        rule = session.get(AlertRule, 1)
        return {
            'start': start, 'end': end, 'include_archived': include_archived, 'generated_at': timestamp(utc_now()),
            'last_scan_at': timestamp(rule.last_scan_at) if rule and rule.last_scan_at else None,
            'stock': {'people': count(Person, *conditions), 'verified': count(Person, *conditions, Person.verification == 'verified'), 'stays': count(PersonStay, *stay_scope), 'pending_alerts': count(Alert, *alert_scope, Alert.status.in_(['open', 'in_progress'])), 'documents': count(DocumentDraft, *doc_scope), 'records': count(RecordSession, *record_scope), 'knowledge': count(KnowledgeDocument, KnowledgeDocument.status == 'published')},
            'period': {'new_people': count(Person, *conditions, *created(Person)), 'entries': sum(entries.values()), 'exits': sum(exits.values()), 'documents': count(DocumentDraft, *doc_scope, *created(DocumentDraft)), 'records': count(RecordSession, *record_scope, *created(RecordSession)), 'alerts': count(Alert, *alert_scope, *created(Alert))},
            'trend': trend, 'nationalities': groups(Person, Person.nationality, *conditions), 'purposes': groups(Person, Person.purpose, *conditions),
            'alert_status': groups(Alert, Alert.status, *alert_scope, *created(Alert)),
            'document_status': groups(DocumentDraft, DocumentDraft.status, *doc_scope, *created(DocumentDraft)),
            'record_status': groups(RecordSession, RecordSession.status, *record_scope, *created(RecordSession)),
            'definitions': ['存量与国籍、事由分布取当前档案状态，不受日期筛选影响。', '入出境趋势使用已登记的实际入境/离境日期，一条停留记录各计一次；未知日期不计。', '区间新增按北京时间创建时间统计；状态分布为区间内创建记录的当前状态，不能还原历史某日状态。', '默认排除归档人员及归档文书、笔录；无关联人员的笔录计入。已发布法规存量独立统计。', '预警统计取最近扫描结果，反映待核查事项，不代表违法人数。']}


@router.get('')
def get_report(request: Request, start: date | None = None, end: date | None = None, include_archived: bool = False):
    return report(request.app.state.engine, start, end, include_archived)


@router.get('/export')
def export_report(request: Request, start: date | None = None, end: date | None = None, include_archived: bool = False):
    data = report(request.app.state.engine, start, end, include_archived)
    stream = StringIO(newline=''); writer = csv.writer(stream)
    writer.writerow(['类别', '指标/日期', '数量', '补充口径'])
    writer.writerow(['区间', str(data['start']), str(data['end']), 'Asia/Shanghai'])
    writer.writerow(['归档口径', '包含' if include_archived else '排除', '', data['generated_at']])
    for group in ['stock', 'period', 'nationalities', 'purposes', 'alert_status', 'document_status', 'record_status']:
        rows = data[group].items() if isinstance(data[group], dict) else [(item['label'], item['value']) for item in data[group]]
        for label, value in rows: writer.writerow([group, csv_cell(label), value, ''])
    for item in data['trend']:
        writer.writerow(['入境', item['date'], item['entries'], '已登记实际日期'])
        writer.writerow(['离境', item['date'], item['exits'], '已登记实际日期'])
    for definition in data['definitions']: writer.writerow(['口径', definition, '', ''])
    return Response(b'\xef\xbb\xbf'+stream.getvalue().encode(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="nia-analytics.csv"'})

"""Read-only, deterministic summaries of this person's recorded evidence."""
from collections import Counter, defaultdict

from sqlalchemy import select

from .alerts import AlertRule, StayFacts, assess, business_today, exclusion_reason
from .people import PersonStay


def build_insights(session, person, profile):
    today = business_today()
    stays = list(session.scalars(select(PersonStay).where(PersonStay.person_id == person.id).order_by(PersonStay.entry_date.desc(), PersonStay.id)))
    supplements = {f.stay_id: f for f in session.scalars(select(StayFacts).where(StayFacts.stay_id.in_([s.id for s in stays])))} if stays else {}
    rule = session.get(AlertRule, 1) or AlertRule(upcoming_days=30, urgent_days=7)
    active = [s.id for s in stays if s.entry_date <= today and (not s.exit_date or s.exit_date > today)]
    deadlines, travel, checks = [], [], []
    for stay in stays:
        supplement = supplements.get(stay.id)
        completed = bool(stay.exit_date and stay.exit_date <= today)
        future = stay.entry_date > today
        travel.append({'id': stay.id, 'entry': stay.entry_date, 'exit': stay.exit_date,
                       'status': 'future' if future else 'completed' if completed else 'unclosed',
                       'days': (stay.exit_date - stay.entry_date).days if completed else None, 'source': stay.source})
        excluded = exclusion_reason(person, stay, today)
        result = None if excluded else assess(person, stay, supplement, active, rule, today)
        deadline = result['deadline'] if result else None
        if not excluded and not result:
            # assess returns no candidate only when all applicable facts pass and
            # the effective deadline lies outside the configured reminder window.
            deadline = supplement.extension_until if supplement and supplement.extension_status == 'approved' else stay.permitted_until
        deadlines.append({'id': stay.id, 'entry': stay.entry_date, 'registered_until': stay.permitted_until,
                          'visa_until': supplement.visa_valid_until if supplement else None,
                          'effective_until': deadline,
                          'days_remaining': (deadline - today).days if deadline else None,
                          'category': 'excluded' if excluded else result['category'] if result else 'outside_window',
                          'reason': excluded or (result['reason'] if result else '登记期限尚未进入提醒窗口；按当前事实计算，仍需人工核验。'),
                          'source': result['source_summary'] if result else '；'.join([f'人员资料：{person.source}', f'停留记录：{stay.source}'] + ([f'补充事实：{supplement.source}'] if supplement and supplement.source else []))})
    completed = [s for s in stays if s.exit_date and s.exit_date <= today]
    for i, stay in enumerate(stays):
        # Unknown/future exit is an open interval, not an assumed departure.
        if stay.entry_date > today:
            continue
        for other in stays[i+1:]:
            if other.entry_date > today:
                continue
            if max(stay.entry_date, other.entry_date) < min(stay.exit_date or today, other.exit_date or today, today):
                checks.append({'kind': 'overlap', 'title': '登记停留区间存在交叠', 'detail': f'{stay.entry_date} 与 {other.entry_date} 的记录日期交叠，可能为重复或历史修订，请核对来源。', 'source_ids': [stay.id, other.id]})
    if len(active) > 1:
        checks.append({'kind': 'open_stays', 'title': '多条记录尚未登记已发生的离境', 'detail': f'共 {len(active)} 条；未登记离境不等于确认仍在境内。', 'source_ids': active})
    facts = [f for f in profile['facts'] if not f['archived']]
    grouped = defaultdict(list)
    for fact in facts:
        grouped[(fact['kind'], fact['label'].strip())].append(fact)
    for (_, label), rows in grouped.items():
        if len({r['value'].strip() for r in rows}) > 1:
            checks.append({'kind': 'values', 'title': f'同名事实存在不同内容：{label}', 'detail': '可能为不同时点或不同来源的陈述，请结合事实日期核对，不自动判定矛盾。', 'source_ids': [r['id'] for r in rows]})
    sources = {s['id']: s for s in profile['sources']}
    stay_map = {s.id: s for s in stays}
    for fact in facts:
        snapshot = fact.get('source_snapshot', {})
        source = sources.get(fact.get('source_id'))
        changed = bool(source and snapshot.get('revision') is not None and source.get('revision') != snapshot['revision'])
        if fact['source_type'] == 'stay' and fact.get('source_id') in stay_map:
            stay = stay_map[fact['source_id']]
            changed = any(key in snapshot and str(snapshot[key] or '') != str(getattr(stay, key) or '') for key in ('entry_date', 'exit_date', 'permitted_until'))
        if changed:
            checks.append({'kind': 'source_updated', 'title': f'事实引用的来源已更新：{fact["label"]}', 'detail': '原始快照仍保留；请核对事实内容是否需要同步修订。', 'source_ids': [fact['id'], fact['source_id']]})
    fields = [(key, label, bool(getattr(person, key))) for key, label in [('name', '姓名'), ('nationality', '国籍'), ('document_number', '证件号码'), ('birth_date', '出生日期'), ('document_expiry', '证件有效期'), ('phone', '联系电话'), ('address', '居住地址'), ('purpose', '来华事由'), ('source', '建档来源')]]
    history = []
    for kind, label in [('record', '询问笔录'), ('document', '业务文书'), ('alert', '期限核查')]:
        rows = [r for r in profile['timeline'] if r['kind'] == kind]
        history.append({'kind': kind, 'label': label, 'total': len(rows), 'statuses': dict(Counter(r['status'] for r in rows)), 'latest_at': max((r['date'] for r in rows), default=None)})
    return {'as_of': today, 'travel': travel, 'entries': sum(s.entry_date <= today for s in stays), 'exits': len(completed),
            'unclosed': len(active), 'deadlines': deadlines,
            'document': {'expiry': person.document_expiry, 'days_remaining': (person.document_expiry-today).days if person.document_expiry else None},
            'completeness': {'filled': sum(v for _, _, v in fields), 'total': len(fields), 'fields': [{'key': k, 'label': l, 'present': v} for k, l, v in fields], 'verified_facts': sum(f['verified'] for f in facts), 'fact_count': len(facts)},
            'history': history, 'checks': checks,
            'scope': '仅汇总本人的已登记资料，包含历史归档业务记录；日期按北京时间计算。字段填写率不代表材料齐备或法律合规；一致性检查仅覆盖日期交叠、同名事实与来源更新。'}

"""Describe unresolved material using verified template answers, without cloud calls."""
from collections import defaultdict
from .record_dual_prefill import prefill, engine_clauses, question_key, surface


def classified_groups(template, groups):
    probe = {**template, 'values': {}, 'locked_fields': [], 'structured_fill_sources': {}}
    verified, _ = prefill(probe, groups)
    values = {field: value for field, value in verified.get('values', {}).items()
              if surface(value) == surface(template.get('values', {}).get(field, ''))}
    aliases = defaultdict(set)
    for field in template.get('fields', []):
        if field['id'] in values:
            for label in [field['label'], *field.get('question_aliases', []), *field.get('match_rules', [])]:
                aliases[surface(label)].add(field['id'])
    coverage = {}
    for engine in ('siliconflow', 'volcengine', 'local'):
        good, bad, current, ids, answer = set(), set(), None, set(), []
        def finish():
            if current and surface(''.join(answer)) == surface(values[current]):
                good.update(ids)
            else:
                bad.update(ids)
        for clause, sources, _ in engine_clauses(groups, engine):
            if clause is None:
                finish()
                current, ids, answer = None, set(), []
                continue
            key = question_key(clause)
            targets = aliases.get(key, set())
            if len(targets) == 1:
                finish()
                current, ids, answer = next(iter(targets)), set(sources), []
            elif not key and not current:
                good.update(sources)
            else:
                ids.update(sources)
                answer.append(clause)
        finish()
        coverage[engine] = good - bad
    from .record_asr_judge import metrics
    results = []
    for sid, group in groups:
        matching = sorted((c for c in group['candidates'] if sid in coverage[c['provider']]),
                          key=lambda c: tuple(metrics(c).values()))
        corroborated = sum(sid in covered for covered in coverage.values()) >= 2
        resolved = bool(matching and corroborated and not group.get('errors'))
        results.append({**group, 'needs_review': not resolved,
                        'automatic_choice': matching[0]['provider'] if resolved else None})
    return results


def run_groups(session, record, run):
    from sqlalchemy import select
    from .record_asr_results import sentences
    from .records import RecordSegment
    originals = sentences(session, run)
    groups = classified_groups(record.template_snapshot, [(s.id, s.data['dual_review']) for s in originals])
    projected = list(session.scalars(select(RecordSegment).where(RecordSegment.record_id == record.id,
                                      RecordSegment.audio_id == run.audio_id).order_by(RecordSegment.position)))
    changed = len(projected) != len(originals) or any(
        p.id != s.id or p.text != s.data['text'] or p.speaker != 'unknown'
        or p.start_ms != s.data.get('start_ms') or p.end_ms != s.data.get('end_ms')
        for p, s in zip(projected, originals))
    if changed or run.interval_map:
        return [{**g, 'needs_review': True, 'automatic_choice': None} for g in groups]
    return groups


def summary(session, record):
    from sqlalchemy import select
    from .record_asr_results import RecognitionRun
    runs = session.scalars(select(RecognitionRun).where(RecognitionRun.record_id == record.id,
                           RecognitionRun.provider == 'dual', RecognitionRun.applied.is_(True)))
    pending, automatic = 0, 0
    for run in runs:
        groups = run_groups(session, record, run)
        pending += sum(g['needs_review'] for g in groups)
        automatic += int(bool(groups) and not any(g['needs_review'] for g in groups))
    return {'pending_groups': pending, 'ready_runs': automatic}

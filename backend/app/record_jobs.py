"""Durable, single-process generation queue with validated per-group checkpoints.

Provider credentials live only in the worker's in-memory settings snapshot. Restart
requires explicit resumption; completed groups are never sent again on resume.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import logging
import time

from fastapi import HTTPException, Request
from sqlalchemy import select

from . import records as r
from .records_ai import RecordAIError, analyze_segments
from .record_templates import extraction_groups, SYSTEM_FIELDS
from .record_quality import POLICY
from .record_cloud import retry
from .record_candidates import add_candidates, pair_qa, rule_facts

ACTIVE = ('queued', 'running')
logger = logging.getLogger('Crj.record_jobs')


GenerationJob = r.GenerationJob

def public(job):
    return {'id': job.id, 'status': job.status, 'error': job.error,
            'completed': sum(g['status'] == 'succeeded' for g in job.groups),
            'total': len(job.groups),
            'groups': [{k: g.get(k) for k in ('name', 'status', 'attempts', 'seconds', 'error', 'retry_at', 'retries')} for g in job.groups]}


def latest(session, record_id):
    job = session.scalar(select(GenerationJob).join(r.RecordAnalysis, GenerationJob.analysis_id == r.RecordAnalysis.id)
                         .where(GenerationJob.record_id == record_id)
                         .order_by(r.RecordAnalysis.created_at.desc(), GenerationJob.id.desc()).limit(1))
    return public(job) if job else None


def recover(engine):
    with r.write_session(engine) as session:
        for job in session.scalars(select(GenerationJob).where(GenerationJob.status.in_(ACTIVE))):
            job.status = 'interrupted'
            job.error = '服务重启，已完成分组保留。确认后可继续未完成分组。'


def unchanged(session, row, attempt):
    return row.status == 'draft' and row.revision == attempt.input_revision and r.segment_hash(r.ordered_segments(session, row.id)) == attempt.input_hash


def fail(engine, identifier, message, status='failed'):
    with r.write_session(engine) as session:
        job = session.get(GenerationJob, identifier)
        if not job or job.status not in ACTIVE:
            return
        job.status, job.error = status, message[:400]
        attempt = session.get(r.RecordAnalysis, job.analysis_id)
        attempt.status, attempt.error, attempt.finished_at = ('stale' if status == 'stale' else 'failed'), message[:400], r.utc_now()
        r.audit(session, 'generation_failed', record_id=job.record_id, attempt_id=attempt.id, status=status)


def retry_notice(engine, identifier, index, attempt, delay, status):
    with r.write_session(engine) as session:
        job=session.get(GenerationJob,identifier)
        if not job or job.status not in ACTIVE:return
        row=r.get_record(session,job.record_id,True)
        if not unchanged(session,row,session.get(r.RecordAnalysis,job.analysis_id)):
            raise RecordAIError(409,'笔录已变化，停止重试。')
        groups=deepcopy(job.groups)
        groups[index].update(retries=attempt,retry_at=time.time()+delay,error=f"{'服务限流' if status==429 else '服务暂时不可用'}，{delay}秒后自动重试（{attempt}/2）")
        job.groups=groups


def run(engine, identifier, settings):
    try:
        while True:
            with r.write_session(engine) as session:
                job = session.get(GenerationJob, identifier)
                if not job or job.status not in ACTIVE:
                    return
                row = r.get_record(session, job.record_id, True)
                attempt = session.get(r.RecordAnalysis, job.analysis_id)
                if not unchanged(session, row, attempt):
                    raise RecordAIError(409, '笔录已变化，未应用生成结果；请重新生成。')
                groups = deepcopy(job.groups)
                index = next((i for i, group in enumerate(groups) if group['status'] != 'succeeded'), None)
                if index is None:
                    outputs = [g['output'] for g in groups]
                    facts = [f for output in outputs for f in output['key_facts'] if f['label'] not in SYSTEM_FIELDS]
                    attempt.summary = '\n'.join(output['summary'] for output in outputs)[:6000]
                    attempt.key_facts = facts
                    attempt.issues = list(dict.fromkeys(issue for output in outputs for issue in output['issues']))
                    attempt.status, attempt.finished_at = 'succeeded', r.utc_now()
                    add_candidates(session, row, attempt, facts)
                    checks = [c for output in outputs for c in output.get('quality_checks', [])]
                    job.template = {**job.template, 'quality_checks': checks, 'quality_policy': POLICY}
                    row.template_snapshot = r.with_recording_times(session, row, r.filled_template(job.template, facts, attempt.input_hash))
                    job.status, job.error = 'succeeded', ''
                    r.version(session, row, 'analyzed')
                    return
                group = groups[index]
                group.update(status='running', error='', attempts=group['attempts'] + 1)
                job.status, job.groups = 'running', groups
                template = {**job.template, 'fields': group['fields'], 'grouped_generation': True, 'all_field_ids': [f['id'] for f in job.template['fields']]}
                inputs = deepcopy(attempt.input_segments)
            started = time.monotonic()
            try:
                def analyze_current():
                    with r.write_session(engine) as check_session:
                        current_job=check_session.get(GenerationJob,identifier)
                        current_row=r.get_record(check_session,job.record_id,True)
                        if current_job.status not in ACTIVE or not unchanged(check_session,current_row,check_session.get(r.RecordAnalysis,current_job.analysis_id)):
                            raise RecordAIError(409,'笔录已变化，停止生成。')
                    return analyze_segments(inputs, settings, template)
                output = retry(analyze_current, lambda attempt, delay, status: retry_notice(engine, identifier, index, attempt, delay, status))
            except RecordAIError as error:
                with r.write_session(engine) as session:
                    job = session.get(GenerationJob, identifier)
                    groups = deepcopy(job.groups)
                    groups[index].update(status='failed', error=error.message[:400], seconds=round(time.monotonic()-started, 2))
                    job.groups = groups
                raise
            with r.write_session(engine) as session:
                job = session.get(GenerationJob, identifier)
                groups = deepcopy(job.groups)
                groups[index].update(status='succeeded', error='', retry_at=None, output=output, seconds=round(time.monotonic()-started, 2))
                job.groups = groups
            logger.info('generation_group_finished job_id=%s group=%s seconds=%.2f', identifier, index + 1, time.monotonic()-started)
    except RecordAIError as error:
        logger.warning('generation_failed job_id=%s status=%s', identifier, error.status)
        fail(engine, identifier, error.message, 'stale' if error.status == 409 else 'failed')
    except Exception as error:
        # Exception class only: SDK exceptions can contain requests or credentials.
        logger.error('generation_internal_failure job_id=%s type=%s', identifier, type(error).__name__)
        fail(engine, identifier, '后台生成未完成，已保存完成分组。请重试并查看后台任务日志。')


def dispatch(request, identifier, settings):
    try:
        request.app.state.record_executor.submit(run, request.app.state.engine, identifier, settings)
    except RuntimeError:
        fail(request.app.state.engine, identifier, '服务正在停止，请重启后继续任务。')


@r.router.post('/{record_id}/generation-jobs', status_code=202)
def start(record_id: str, body: r.AIRequest, request: Request):
    settings, provider, model = r.confirmed_settings(request, body, 'analysis')
    with r.write_session(request.app.state.engine) as session:
        row = r.get_record(session, record_id, True)
        r.mutable(row, body.expected_revision)
        existing = session.scalar(select(GenerationJob).where(GenerationJob.record_id == record_id, GenerationJob.status.in_(ACTIVE)))
        if existing:
            return public(existing)
        if session.scalar(select(r.RecordAnalysis.id).where(r.RecordAnalysis.record_id == record_id, r.RecordAnalysis.status == 'processing')):
            raise HTTPException(409, '本笔录已有生成请求，请等待完成。')
        if len(session.scalars(select(GenerationJob.id).where(GenerationJob.status.in_(ACTIVE))).all()) >= 8:
            raise HTTPException(429, '生成队列已满，请稍后提交。')
        segments = r.ordered_segments(session, record_id)
        inputs = r.input_segments(segments)
        if not inputs or sum(len(s['text']) for s in inputs) > r.LIMITS['max_analyze_chars']:
            raise HTTPException(422, '请先保存修订文字，单次最多30000字。')
        template = deepcopy(row.template_snapshot)
        digest=r.segment_hash(segments)
        previous=session.scalar(select(GenerationJob).join(r.RecordAnalysis,GenerationJob.analysis_id==r.RecordAnalysis.id).where(GenerationJob.record_id==record_id,GenerationJob.status=='succeeded').order_by(r.RecordAnalysis.created_at.desc()).limit(1))
        if previous and previous.template.get('quality_policy')==POLICY and not any(c['status']=='blocked' for c in previous.template.get('quality_checks',[])):
            prior=session.get(r.RecordAnalysis,previous.analysis_id)
            def signature(t):
                locks=t.get('locked_fields',[])
                return (t.get('fields'),t.get('version'),t.get('word_asset'),sorted(locks),{k:t.get('values',{}).get(k,'') for k in locks})
            if prior.input_hash==digest and (prior.provider,prior.model)==(provider,model) and signature(previous.template)==signature(template) and template.get('source_hash')==digest:
                return {**public(previous),'reused':True}
        template['quality_policy']=POLICY
        if template.get('id') in {t['id'] for t in r.CASE_TEMPLATES} and not template.get('word_asset'):
            template = deepcopy(next(t for t in r.CASE_TEMPLATES if t['id'] == template['id']))
        fields = template.get('fields', [])
        if not fields:
            raise HTTPException(422, '模板没有可回填字段。')
        local_facts = rule_facts(pair_qa(segments, template))
        local_ids = {f['label'] for f in local_facts}
        remaining = {**template, 'fields': [f for f in fields if f['id'] not in local_ids]}
        groups = [{'name': f'第 {i + 1} 组（{len(group)} 项）', 'fields': group,
                   'status': 'pending', 'attempts': 0, 'error': ''} for i, group in enumerate(extraction_groups(remaining))]
        if local_facts:
            groups.insert(0, {'name': '标准问题精确匹配', 'fields': [], 'status': 'succeeded', 'attempts': 0, 'seconds': 0, 'error': '', 'output': {'summary': '按明确问答标记匹配标准问题，答案保留原文。', 'key_facts': local_facts, 'issues': []}})
        attempt = r.RecordAnalysis(record_id=record_id, input_revision=row.revision, input_segments=inputs,
                                   input_hash=r.segment_hash(segments), provider=provider, model=model)
        session.add(attempt)
        session.flush()
        job = GenerationJob(record_id=record_id, analysis_id=attempt.id, status='queued', template=template, groups=groups)
        session.add(job)
        session.flush()
        identifier, result = job.id, public(job)
        r.audit(session, 'generation_queued', record_id=record_id, attempt_id=attempt.id, groups=len(groups))
    dispatch(request, identifier, settings)
    return result


@r.router.post('/{record_id}/generation-jobs/{job_id}/resume', status_code=202)
def resume(record_id: str, job_id: str, body: r.AIRequest, request: Request):
    settings, provider, model = r.confirmed_settings(request, body, 'analysis')
    with r.write_session(request.app.state.engine) as session:
        row = r.get_record(session, record_id, True)
        r.mutable(row, body.expected_revision)
        job = session.get(GenerationJob, job_id)
        if not job or job.record_id != record_id:
            raise HTTPException(404, '未找到生成任务。')
        if job.status in ACTIVE:
            return public(job)
        if job.status not in ('failed', 'interrupted'):
            raise HTTPException(409, '此任务不能继续，请重新生成。')
        if job.template.get('quality_policy') != POLICY:
            raise HTTPException(409,'生成校验规则已升级，请重新生成，不能复用旧分组。')
        attempt = session.get(r.RecordAnalysis, job.analysis_id)
        if not unchanged(session, row, attempt) or (provider, model) != (attempt.provider, attempt.model):
            raise HTTPException(409, '文字、模板、人工修改或模型已变化，请重新生成，不能复用旧分组。')
        if session.scalar(select(r.RecordAnalysis.id).where(r.RecordAnalysis.record_id == record_id, r.RecordAnalysis.status == 'processing')):
            raise HTTPException(409, '本笔录已有生成请求，请等待完成。')
        if len(session.scalars(select(GenerationJob.id).where(GenerationJob.status.in_(ACTIVE))).all()) >= 8:
            raise HTTPException(429, '生成队列已满，请稍后继续。')
        job.status, job.error = 'queued', ''
        attempt.status, attempt.error, attempt.finished_at = 'processing', '', None
        result = public(job)
    dispatch(request, job_id, settings)
    return result

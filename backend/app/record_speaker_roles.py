"""Explicit, versioned human roles. Provider speaker numbers are never identities."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import JSON, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base
from .record_asr_results import RecognitionRun, RecognitionSentence, fingerprint

ROLES = {'OFFICER', 'INTERVIEWEE', 'OTHER', 'UNKNOWN'}
INTENTS = {'QUESTION', 'ANSWER', 'FOLLOW_UP', 'CONTINUATION', 'INSTRUCTION', 'CLARIFICATION', 'OTHER', 'UNKNOWN'}


class SpeakerRoleMapping(Base):
    __tablename__ = 'record_speaker_role_mappings'
    __table_args__ = (UniqueConstraint('scope', 'revision'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    record_id: Mapped[str] = mapped_column(String(36), index=True)
    recognition_run_id: Mapped[str] = mapped_column(String(36), index=True)
    channel_id: Mapped[object] = mapped_column(JSON)
    speaker_id: Mapped[object] = mapped_column(JSON)
    scope: Mapped[str] = mapped_column(String(64), index=True)
    business_role: Mapped[str] = mapped_column(String(20))
    mapping_source: Mapped[str] = mapped_column(String(20), default='MANUAL')
    mapping_status: Mapped[str] = mapped_column(String(20))
    confirmed_by: Mapped[str] = mapped_column(String(100))
    confirmed_at: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column()


class SentenceRoleReview(Base):
    __tablename__ = 'record_sentence_role_reviews'
    __table_args__ = (UniqueConstraint('sentence_id', 'revision'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    record_id: Mapped[str] = mapped_column(String(36), index=True)
    sentence_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column()
    parts: Mapped[list] = mapped_column(JSON)
    confirmed_by: Mapped[str] = mapped_column(String(100))
    confirmed_at: Mapped[str] = mapped_column(String(40))


def now():
    return datetime.now(timezone.utc).isoformat()


def scope(run, channel, speaker):
    return fingerprint([run, channel, speaker])


def latest_mapping(session, run, channel, speaker):
    return session.scalar(select(SpeakerRoleMapping).where(SpeakerRoleMapping.scope == scope(run, channel, speaker)).order_by(SpeakerRoleMapping.revision.desc()))


def latest_review(session, sentence_id):
    return session.scalar(select(SentenceRoleReview).where(SentenceRoleReview.sentence_id == sentence_id).order_by(SentenceRoleReview.revision.desc()))


def map_role(session, record_id, run_id, channel, speaker, role, expected, actor):
    if role not in ROLES or speaker is None or not actor:
        raise ValueError('角色、说话人或操作人无效。')
    run = session.get(RecognitionRun, run_id)
    if run is None or run.record_id != record_id:
        raise ValueError('识别运行不属于当前笔录。')
    rows = session.scalars(select(RecognitionSentence).where(RecognitionSentence.run_id == run_id))
    if not any(scope(run_id, x.data.get('channel_id'), x.data.get('speaker_id')) == scope(run_id, channel, speaker) for x in rows):
        raise ValueError('该运行中不存在指定通道和说话人。')
    old = latest_mapping(session, run_id, channel, speaker)
    if expected != (old.revision if old else 0):
        raise ValueError('说话人映射版本冲突，请刷新后核对。')
    row = SpeakerRoleMapping(record_id=record_id, recognition_run_id=run_id, channel_id=channel, speaker_id=speaker,
        scope=scope(run_id, channel, speaker), business_role=role, mapping_status='CONFIRMED' if role != 'UNKNOWN' else 'REVIEW_REQUIRED',
        confirmed_by=actor, confirmed_at=now(), revision=expected + 1)
    session.add(row); session.flush()
    return row


def review_sentence(session, record_id, sentence_id, parts, expected, actor):
    sentence = session.get(RecognitionSentence, sentence_id)
    run = session.get(RecognitionRun, sentence.run_id) if sentence else None
    if not run or run.record_id != record_id or not actor:
        raise ValueError('识别句子不属于当前笔录。')
    old = latest_review(session, sentence_id)
    if expected != (old.revision if old else 0):
        raise ValueError('逐句修订版本冲突，请刷新后核对。')
    cursor = 0
    if not parts or len(parts) > 100:
        raise ValueError('请提供完整的发言范围。')
    for part in parts:
        if set(part) != {'start', 'end', 'role', 'intent'} or type(part['start']) is not int or type(part['end']) is not int:
            raise ValueError('发言范围格式无效。')
        if part['start'] != cursor or not cursor < part['end'] <= len(sentence.data['text']):
            raise ValueError('拆分必须按顺序完整覆盖原文，不得重复或遗漏。')
        if part['role'] not in ROLES or part['intent'] not in INTENTS:
            raise ValueError('角色或讲话意图无效。')
        cursor = part['end']
    if cursor != len(sentence.data['text']):
        raise ValueError('拆分未完整覆盖原文。')
    row = SentenceRoleReview(record_id=record_id, sentence_id=sentence_id, revision=expected + 1,
        parts=parts, confirmed_by=actor, confirmed_at=now())
    session.add(row); session.flush()
    return row


def sentence_parts(session, sentence):
    """Per-sentence correction wins over the latest batch mapping, including UNKNOWN."""
    review = latest_review(session, sentence.id)
    if review:
        return review.parts, f'review:{review.id}'
    d = sentence.data
    mapping = latest_mapping(session, sentence.run_id, d.get('channel_id'), d.get('speaker_id')) if d.get('speaker_id') is not None else None
    return [{'start': 0, 'end': len(d['text']), 'role': mapping.business_role if mapping else 'UNKNOWN', 'intent': 'UNKNOWN'}], f'mapping:{mapping.id}' if mapping else 'unreviewed'

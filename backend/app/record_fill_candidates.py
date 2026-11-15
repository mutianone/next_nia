"""Independent immutable shadow material and append-only human reviews."""
from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base

class FillBatch(Base):
    __tablename__='record_fill_batches'
    __table_args__=(UniqueConstraint('record_id','signature'),)
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    record_id:Mapped[str]=mapped_column(String(36),index=True)
    draft_id:Mapped[str]=mapped_column(String(36),index=True)
    draft_revision:Mapped[int]=mapped_column()
    signature:Mapped[str]=mapped_column(String(64))
    source_digest:Mapped[str]=mapped_column(String(64))
    template:Mapped[dict]=mapped_column(JSON)
    created_by:Mapped[str]=mapped_column(String(100))
    created_at:Mapped[str]=mapped_column(String(40))

class FillCandidate(Base):
    __tablename__='record_fill_candidates'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    record_id:Mapped[str]=mapped_column(String(36),index=True)
    batch_id:Mapped[str]=mapped_column(String(36),index=True)
    position:Mapped[int]=mapped_column()
    data:Mapped[dict]=mapped_column(JSON)
    revision:Mapped[int]=mapped_column(default=1)

class FillReview(Base):
    __tablename__='record_fill_reviews'
    __table_args__=(UniqueConstraint('candidate_id','revision'),)
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    candidate_id:Mapped[str]=mapped_column(String(36),index=True)
    revision:Mapped[int]=mapped_column()
    data:Mapped[dict]=mapped_column(JSON)
    actor_id:Mapped[str]=mapped_column(String(100))
    created_at:Mapped[str]=mapped_column(String(40))

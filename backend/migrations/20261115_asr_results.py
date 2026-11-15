"""Add immutable recognition sidecars; never alter/backfill legacy rows.
Usage: python -m backend.migrations.20261115_asr_results sqlite:////path/db.sqlite3
Stop application/workers and back up database before applying in production.
Application Base.metadata.create_all also creates these same additive tables.
"""
import sys
from sqlalchemy import create_engine
from backend.app.record_asr_results import RecognitionRun, RecognitionSentence


def upgrade(engine):
    with engine.begin() as connection:
        RecognitionRun.__table__.create(connection, checkfirst=True)
        RecognitionSentence.__table__.create(connection, checkfirst=True)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Provide database URL; credentials must not be logged.')
    engine = create_engine(sys.argv[1], hide_parameters=True)
    upgrade(engine)
    engine.dispose()

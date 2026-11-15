"""Add shadow tables only. Run explicitly on the intended database after backup."""
import sys
from sqlalchemy import create_engine
from backend.app.record_fill_candidates import FillBatch,FillCandidate,FillReview
TABLES=(FillBatch,FillCandidate,FillReview)
def upgrade(engine):
    with engine.begin() as connection:
        for model in TABLES:model.__table__.create(connection,checkfirst=True)
if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('Provide database URL.')
    engine=create_engine(sys.argv[1],hide_parameters=True);upgrade(engine);engine.dispose()

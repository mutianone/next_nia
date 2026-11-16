"""Add controlled-apply tables. Explicit execution only; feature remains off."""
import sys
from sqlalchemy import create_engine
from backend.app.record_fill_apply import FillApplySetting,FillApplication,FillApplicationItem
TABLES=(FillApplySetting,FillApplication,FillApplicationItem)
def upgrade(engine):
    with engine.begin() as connection:
        for model in TABLES:model.__table__.create(connection,checkfirst=True)
if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('Provide database URL.')
    engine=create_engine(sys.argv[1],hide_parameters=True);upgrade(engine);engine.dispose()

"""Add isolated QA draft tables. Existing record_qa_pairs belongs to formal fill.
No production execution is performed by this migration module on import.
Usage: python -m backend.migrations.20261115_qa_drafts '<database URL>'
"""
import sys
from sqlalchemy import create_engine
from backend.app.record_speaker_roles import SpeakerRoleMapping,SentenceRoleReview
from backend.app.record_speaker_turns import SpeakerTurn
from backend.app.record_qa_state_machine import QAPair
from backend.app.record_qa_adhesion import QAReviewIssue
from backend.app.record_qa_drafts import QADraft

TABLES=(SpeakerRoleMapping,SentenceRoleReview,SpeakerTurn,QAPair,QAReviewIssue,QADraft)
def upgrade(engine):
    with engine.begin() as connection:
        for model in TABLES:model.__table__.create(connection,checkfirst=True)

if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('Provide database URL.')
    engine=create_engine(sys.argv[1],hide_parameters=True);upgrade(engine);engine.dispose()

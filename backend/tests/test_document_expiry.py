from datetime import date, timedelta
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from backend.app.database import Base, application_engine
from backend.app.people import Person
from backend.app.expiry_decisions import ExpiryDecision
from backend.app.alerts import Alert, ExpiryDocumentTask, scan_alerts
from sqlalchemy import create_engine

def test_expiry_boundaries_and_idempotent_tasks(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'expiry.sqlite3'))
    Base.metadata.create_all(engine)
    today = date(2026,10,27)
    with Session(engine) as session:
        for days in [4,3,0,-1,-7,-8]:
            session.add(Person(id=str(days), code=str(days), name='测试', nationality='测试', document_type='passport', document_number=str(days), source='测试', document_expiry=today+timedelta(days=days), verification='pending'))
        session.commit()
    result = scan_alerts(engine, today=today)
    assert result['created'] == 5
    scan_alerts(engine, today=today)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Alert)) == 5
        tasks = list(session.scalars(select(ExpiryDocumentTask)))
        drafts = list(session.scalars(select(ExpiryDecision)))
        assert len(drafts) == 2
        assert drafts[0].fields['person_name'] == '测试'
        assert not drafts[0].fields['legal_basis']
        assert {t.person_id for t in tasks} == {'-7', '-8'}
        assert tasks[0].status == 'draft_created'
        person = session.get(Person, '-8'); person.document_expiry = today+timedelta(days=30)
        session.commit()
    scan_alerts(engine, today=today)
    with Session(engine) as session:
        assert session.scalar(select(ExpiryDocumentTask).where(ExpiryDocumentTask.person_id == '-8')).status == 'cancelled'
        assert session.scalar(select(Alert).where(Alert.person_id == '-8')).status == 'closed'

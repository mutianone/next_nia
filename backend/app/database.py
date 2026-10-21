from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from sqlalchemy import DateTime, Integer, JSON, String, URL, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .schemas import PostgresSettings, Settings, reveal


class Base(DeclarativeBase):
    pass


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


def postgres_url(config: PostgresSettings) -> URL:
    return URL.create("postgresql+psycopg", username=config.username, password=reveal(config.password), host=config.host, port=config.port, database=config.database)


def postgres_engine(config: PostgresSettings):
    return create_engine(postgres_url(config), connect_args={"connect_timeout": 5}, pool_pre_ping=True, hide_parameters=True)


def probe_postgres(config: PostgresSettings) -> int:
    engine = postgres_engine(config)
    started = monotonic()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return round((monotonic() - started) * 1000)
    finally:
        engine.dispose()


def application_engine(settings: Settings, data_dir: Path):
    if settings.environment.mode == "production":
        return postgres_engine(settings.environment.postgres)
    return create_engine(URL.create("sqlite", database=str(data_dir / "test.sqlite3")), connect_args={"check_same_thread": False}, hide_parameters=True)


def write_audit(engine, action: str, details: dict) -> None:
    # Call sites pass allowlisted metadata only, never request bodies or credentials.
    with Session(engine) as session:
        session.add(AuditEvent(action=action, details=details))
        session.commit()

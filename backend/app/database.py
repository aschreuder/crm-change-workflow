"""
PostgreSQL connection, sessions and tables (SQLAlchemy 2.0).

Start the database:   docker compose up -d
Create the tables:    python database.py

The connection settings come from .env (see .env.example).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any, Optional

from dotenv import load_dotenv
from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine,
    func, inspect, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from schemas import (
    AuditEventType,
    ChangeRequestStatus,
    Decision,
    Priority,
    RiskLevel,
    SalesforceObject,
    UserRole,
)

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

load_dotenv()


def build_database_url() -> str:
    if url := os.getenv("DATABASE_URL"):
        return url
    user = os.getenv("POSTGRES_USER", "cr_user")
    password = os.getenv("POSTGRES_PASSWORD", "cr_password")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    name = os.getenv("POSTGRES_DB", "change_requests")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"


DATABASE_URL = build_database_url()

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # drop dead connections, e.g. after the container restarts
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one session per request.

    Usage in a route:
        def list_requests(db: Session = Depends(get_db)): ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_connection() -> bool:
    """Return True if the database answers. Useful for /health."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


def pg_enum(enum_cls: type[PyEnum], name: str) -> Enum:
    """Store the enum's value (e.g. "Needs info") rather than its name (NEEDS_INFO)."""
    return Enum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class UserORM(Base):
    __tablename__ = "users"

    account_id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    role: Mapped[UserRole] = mapped_column(
        pg_enum(UserRole, "user_role"), nullable=False, default=UserRole.REQUESTER
    )


class ChangeRequestORM(Base):
    __tablename__ = "change_requests"

    account_id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    business_justification: Mapped[str] = mapped_column(Text, nullable=False)
    requested_change: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100))
    priority: Mapped[Optional[Priority]] = mapped_column(pg_enum(Priority, "priority"))
    risk_level: Mapped[Optional[RiskLevel]] = mapped_column(pg_enum(RiskLevel, "risk_level"))
    status: Mapped[ChangeRequestStatus] = mapped_column(
        pg_enum(ChangeRequestStatus, "change_request_status"),
        nullable=False,
        default=ChangeRequestStatus.DRAFT,
        index=True,
    )
    requester_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.account_id"), nullable=False, index=True
    )
    assigned_approver_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("users.account_id"), index=True
    )
    ai_summary: Mapped[Optional[str]] = mapped_column(Text)
    ai_missing_information: Mapped[Optional[str]] = mapped_column(Text)
    # Set by the business rules on validation; NULL = not validated since the last upload.
    requires_approval: Mapped[Optional[bool]] = mapped_column(Boolean)
    approval_reasons: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    requester: Mapped[UserORM] = relationship(foreign_keys=[requester_id])
    assigned_approver: Mapped[Optional[UserORM]] = relationship(foreign_keys=[assigned_approver_id])
    decisions: Mapped[list[ApprovalDecisionORM]] = relationship(
        back_populates="change_request", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list[AuditEventORM]] = relationship(
        back_populates="change_request",
        cascade="all, delete-orphan",
        order_by="AuditEventORM.created_at",
    )
    items: Mapped[list[ChangeRequestItemORM]] = relationship(
        back_populates="change_request",
        cascade="all, delete-orphan",
        order_by="ChangeRequestItemORM.row_number",
    )


class ChangeRequestItemORM(Base):
    """One uploaded CSV row: a single field change on a single Salesforce record."""
    __tablename__ = "change_request_items"
    __table_args__ = (
        # The same record + field can appear only once per request.
        UniqueConstraint("change_request_id", "record_id", "field", name="uq_item_change_per_request"),
    )

    account_id: Mapped[uuid.UUID] = uuid_pk()
    change_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("change_requests.account_id", ondelete="CASCADE"), nullable=False, index=True
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Who/what the change is about (mandatory in the CSV)
    salesforce_account_id: Mapped[str] = mapped_column(String(18), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(100), nullable=False)

    # The change itself
    object_type: Mapped[SalesforceObject] = mapped_column(
        pg_enum(SalesforceObject, "salesforce_object"), nullable=False
    )
    record_id: Mapped[str] = mapped_column(String(18), nullable=False)
    field: Mapped[str] = mapped_column(String(40), nullable=False)
    current_value: Mapped[str] = mapped_column(Text, nullable=False, default="")  # "" = blank in Salesforce
    new_value: Mapped[str] = mapped_column(Text, nullable=False)

    # Problems found by the latest validation (empty list = passed)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)

    change_request: Mapped[ChangeRequestORM] = relationship(back_populates="items")


# Lets clash checks find other requests changing the same record + field quickly.
Index("ix_items_record_field", ChangeRequestItemORM.record_id, ChangeRequestItemORM.field)


class ApprovalDecisionORM(Base):
    __tablename__ = "approval_decisions"

    account_id: Mapped[uuid.UUID] = uuid_pk()
    change_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("change_requests.account_id", ondelete="CASCADE"), nullable=False, index=True
    )
    approver_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.account_id"), nullable=False)
    decision: Mapped[Decision] = mapped_column(pg_enum(Decision, "decision"), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    change_request: Mapped[ChangeRequestORM] = relationship(back_populates="decisions")
    approver: Mapped[UserORM] = relationship()


class AuditEventORM(Base):
    __tablename__ = "audit_events"

    account_id: Mapped[uuid.UUID] = uuid_pk()
    change_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("change_requests.account_id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[AuditEventType] = mapped_column(
        pg_enum(AuditEventType, "audit_event_type"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)  # user id or "system"/"ai"/"n8n"
    previous_value: Mapped[Optional[str]] = mapped_column(Text)
    new_value: Mapped[Optional[str]] = mapped_column(Text)
    event_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    change_request: Mapped[ChangeRequestORM] = relationship(back_populates="audit_events")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def init_db() -> None:
    """Create any tables that don't exist yet, and add new enum values to existing enum types.

    Existing tables are otherwise left alone: column changes need a migration tool (Alembic).
    """
    check_items_table_is_current()
    Base.metadata.create_all(bind=engine)
    add_missing_optional_columns()
    sync_enum_values()


def add_missing_optional_columns() -> None:
    """Add new optional (nullable) columns to tables that already exist.

    create_all() only creates missing tables, so a column added to a model later
    (e.g. change_requests.requires_approval) would otherwise be missing. Only
    nullable columns are added; anything else still needs a migration tool (Alembic).
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing or not column.nullable:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN IF NOT EXISTS '
                                  f'"{column.name}" {col_type}'))


def check_items_table_is_current() -> None:
    """Stop with clear instructions if change_request_items was created for an older CSV
    layout (create_all() can't change an existing table)."""
    if not inspect(engine).has_table("change_request_items"):
        return
    existing = {c["name"] for c in inspect(engine).get_columns("change_request_items")}
    expected = set(ChangeRequestItemORM.__table__.columns.keys())
    if existing != expected:
        raise RuntimeError(
            "The change_request_items table was created for an older CSV layout. Its rows can't be "
            "used with the current template, so remove it and restart (it is recreated automatically):\n"
            "    docker exec -it change-requests-db psql -U cr_user -d change_requests "
            "-c 'DROP TABLE change_request_items;'"
        )


def sync_enum_values() -> None:
    """create_all() doesn't add values to enum types that already exist, so do it here.

    Example: adding AuditEventType.CSV_UPLOADED to schemas.py after the database was created.
    """
    enum_types = {
        col.type.name: col.type.enums
        for table in Base.metadata.tables.values()
        for col in table.columns
        if isinstance(col.type, Enum) and col.type.name
    }
    with engine.begin() as conn:
        for type_name, values in enum_types.items():
            existing = set(conn.scalars(
                text("SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                     "WHERE t.typname = :name"), {"name": type_name}))
            for value in values:
                if value not in existing:
                    conn.execute(text(f'ALTER TYPE "{type_name}" ADD VALUE IF NOT EXISTS :v'
                                      .replace(":v", "'" + value.replace("'", "''") + "'")))


if __name__ == "__main__":
    if not check_connection():
        raise SystemExit(f"Cannot reach the database at {engine.url!r}. Is `docker compose up -d` running?")
    init_db()
    print("Tables ready:", ", ".join(Base.metadata.tables))
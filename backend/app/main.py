"""
Change Request Management API (FastAPI + PostgreSQL)

Start the database:  docker compose up -d
Run the API:         uvicorn main:app --reload
Docs:                http://127.0.0.1:8000/docs

Schemas live in schemas.py, tables and the connection in database.py, and the
CSV template and its checks in csv_template.py.
Workflow rules and AI assistance go in their own modules; the places to call
them are marked with TODO below.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from csv_template import MAX_FILE_BYTES, ParsedRow, parse_csv, summarise, template_csv
from database import (
    ApprovalDecisionORM,
    AuditEventORM,
    ChangeRequestItemORM,
    ChangeRequestORM,
    UserORM,
    check_connection,
    get_db,
    init_db,
)
from schemas import (
    ActorRequest,
    AssignRequest,
    AuditEvent,
    AuditEventType,
    ChangeRequest,
    ChangeRequestCreate,
    ChangeRequestDetail,
    ChangeRequestItem,
    ChangeRequestStatus,
    ChangeRequestUpdate,
    CsvUploadError,
    Decision,
    DecisionRequest,
    ImplementRequest,
    N8nRequestSubmittedPayload,
    Priority,
    ValidateResponse,
    User,
    UserCreate,
    ValidationIssue,
    ValidationReport,
    utcnow,
)
import business_rules
from validation import validate_request

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # creates missing tables on startup
    yield


app = FastAPI(
    title="Change Request API",
    version="1.0.0",
    description="Create, review, approve and track change requests.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

N8N_WEBHOOK_SECRET = os.getenv("N8N_WEBHOOK_SECRET", "change-me")

# TODO(workflow): move to your workflow-rules module.
# Statuses in which the requester may replace the CSV and validate.
EDITABLE_STATUSES = {ChangeRequestStatus.DRAFT, ChangeRequestStatus.VALIDATION_FAILED}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_or_404(db: Session, cr_id: UUID) -> ChangeRequestORM:
    cr = db.get(ChangeRequestORM, cr_id)
    if cr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Change request {cr_id} not found")
    return cr


def require_user(db: Session, user_id: UUID, label: str = "User") -> UserORM:
    user = db.get(UserORM, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{label} {user_id} not found")
    return user


_last_audit_time = utcnow()


def audit_timestamp() -> datetime:
    """Strictly increasing timestamps, so audit events written in the same transaction
    (e.g. CSV uploaded then status changed) keep their order. PostgreSQL's now() would give
    them all the same time."""
    global _last_audit_time
    ts = utcnow()
    if ts <= _last_audit_time:
        ts = _last_audit_time + timedelta(microseconds=1)
    _last_audit_time = ts
    return ts


def record_audit(
    db: Session,
    cr: ChangeRequestORM,
    event_type: AuditEventType,
    actor_id: UUID | str,
    previous_value: Optional[str] = None,
    new_value: Optional[str] = None,
    **metadata: Any,
) -> None:
    db.add(
        AuditEventORM(
            change_request_id=cr.account_id,
            event_type=event_type,
            actor_id=str(actor_id),
            previous_value=previous_value,
            new_value=new_value,
            event_metadata=metadata or None,
            created_at=audit_timestamp(),
        )
    )


def set_status(
    db: Session,
    cr: ChangeRequestORM,
    new_status: ChangeRequestStatus,
    event_type: AuditEventType,
    actor_id: UUID | str,
    **metadata: Any,
) -> None:
    # TODO(workflow): check the transition is allowed before changing it.
    previous = cr.status
    cr.status = new_status
    record_audit(db, cr, event_type, actor_id, previous.value, new_status.value, **metadata)


def record_decision(db: Session, cr: ChangeRequestORM, body: DecisionRequest, decision: Decision) -> None:
    require_user(db, body.approver_id, "Approver")
    db.add(
        ApprovalDecisionORM(
            change_request_id=cr.account_id,
            approver_id=body.approver_id,
            decision=decision,
            comment=body.comment,
        )
    )


def require_editable(cr: ChangeRequestORM, action: str) -> None:
    if cr.status not in EDITABLE_STATUSES:
        allowed = ", ".join(sorted(st.value for st in EDITABLE_STATUSES))
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Cannot {action} a request in status {cr.status.value} (allowed: {allowed})")


async def read_csv_or_error(file: UploadFile) -> tuple[list[ParsedRow], Optional[JSONResponse]]:
    """Run the format checks. Returns (rows, None), or ([], a 422 response listing every problem)."""
    raw = await file.read(MAX_FILE_BYTES + 1)
    rows, issues, row_count = parse_csv(raw)
    if not issues:
        return rows, None
    error = CsvUploadError(
        message=f"The CSV has {len(issues)} problem(s). Fix them and upload it again.",
        filename=file.filename, rows_checked=row_count, errors=issues,
    )
    return [], JSONResponse(status_code=422, content=error.model_dump(mode="json"))


def to_items(rows: list[ParsedRow]) -> list[ChangeRequestItemORM]:
    return [
        ChangeRequestItemORM(
            row_number=row.row_number,
            salesforce_account_id=row.salesforce_account_id,
            name=row.name,
            email=row.email,
            role=row.role,
            object_type=row.object_type,
            record_id=row.record_id,
            field=row.field,
            current_value=row.current_value,
            new_value=row.new_value,
            issues=[],
        )
        for row in rows
    ]


def save(db: Session, obj: Any) -> Any:
    """Commit the session and reload the object so DB-set fields (timestamps) are current."""
    db.commit()
    db.refresh(obj)
    return obj


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


@app.get("/health", tags=["system"])
def health():
    db_ok = check_connection()
    return {"status": "ok" if db_ok else "degraded", "database": db_ok, "timestamp": utcnow()}


# ---------------------------------------------------------------------------
# Users (needed because change requests reference real users)
# ---------------------------------------------------------------------------


@app.post("/users", response_model=User, status_code=status.HTTP_201_CREATED, tags=["users"])
def create_user(body: UserCreate, db: Session = Depends(get_db)):
    user = UserORM(**body.model_dump())
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"A user with email {body.email} already exists")
    db.refresh(user)
    return user


@app.get("/users", response_model=list[User], tags=["users"])
def list_users(db: Session = Depends(get_db)):
    return db.scalars(select(UserORM).order_by(UserORM.name)).all()


# ---------------------------------------------------------------------------
# CSV template
# ---------------------------------------------------------------------------


@app.get("/templates/change-request.csv", tags=["csv"],
         response_class=Response, responses={200: {"content": {"text/csv": {}}}})
def download_template():
    """The standard CSV template with example rows. Empty cells mean 'no change'."""
    return Response(
        content=template_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="change-request-template.csv"'},
    )


# ---------------------------------------------------------------------------
# Change requests
# ---------------------------------------------------------------------------


@app.post("/change-requests", response_model=ChangeRequest,
          status_code=status.HTTP_201_CREATED, tags=["change-requests"])
def create_change_request(body: ChangeRequestCreate, db: Session = Depends(get_db)):
    require_user(db, body.requester_id, "Requester")
    cr = ChangeRequestORM(**body.model_dump(), status=ChangeRequestStatus.DRAFT)
    db.add(cr)
    db.flush()  # assigns the id so the audit event can reference it
    record_audit(db, cr, AuditEventType.CREATED, body.requester_id, new_value=cr.status.value)
    return save(db, cr)


@app.post(
    "/change-requests/upload",
    response_model=ChangeRequestDetail,
    status_code=status.HTTP_201_CREATED,
    tags=["change-requests"],
    responses={422: {"model": CsvUploadError, "description": "The CSV was rejected; nothing was stored"}},
)
async def upload_change_request(
    file: UploadFile = File(..., description="The filled-in standard template (.csv)"),
    requester_id: UUID = Form(...),
    title: str = Form(..., min_length=3, max_length=200),
    business_justification: str = Form(..., min_length=1),
    description: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    priority: Optional[Priority] = Form(None),
    db: Session = Depends(get_db),
):
    """Upload the standard CSV template and create a DRAFT change request from it.

    The whole file is checked first. If anything is wrong, it is rejected (422) with
    every problem listed and nothing is stored. Validate the draft afterwards with
    POST /change-requests/{id}/validate.
    """
    require_user(db, requester_id, "Requester")
    rows, error = await read_csv_or_error(file)
    if error:
        return error

    cr = ChangeRequestORM(
        title=title,
        description=description,
        business_justification=business_justification,
        requested_change=summarise(rows),
        category=category,
        priority=priority,
        requester_id=requester_id,
        status=ChangeRequestStatus.DRAFT,
    )
    cr.items.extend(to_items(rows))
    db.add(cr)
    db.flush()  # assigns the id so the audit event can reference it
    record_audit(db, cr, AuditEventType.CREATED, requester_id, new_value=cr.status.value,
                 source="csv_upload", filename=file.filename, rows=len(rows))
    return save(db, cr)


@app.get("/change-requests", response_model=list[ChangeRequest], tags=["change-requests"])
def list_change_requests(
    status_filter: Optional[ChangeRequestStatus] = Query(None, alias="status"),
    requester_id: Optional[UUID] = None,
    assigned_approver_id: Optional[UUID] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = select(ChangeRequestORM).order_by(ChangeRequestORM.created_at.desc())
    if status_filter:
        query = query.where(ChangeRequestORM.status == status_filter)
    if requester_id:
        query = query.where(ChangeRequestORM.requester_id == requester_id)
    if assigned_approver_id:
        query = query.where(ChangeRequestORM.assigned_approver_id == assigned_approver_id)
    return db.scalars(query.offset(skip).limit(limit)).all()


@app.get("/change-requests/{id}", response_model=ChangeRequest, tags=["change-requests"])
def get_change_request(id: UUID, db: Session = Depends(get_db)):
    return get_or_404(db, id)


@app.patch("/change-requests/{id}", response_model=ChangeRequest, tags=["change-requests"])
def update_change_request(id: UUID, body: ChangeRequestUpdate, db: Session = Depends(get_db)):
    cr = get_or_404(db, id)
    # TODO(workflow): only allow edits in editable statuses (e.g. DRAFT, VALIDATION_FAILED).
    changes = body.model_dump(exclude_unset=True, exclude={"actor_id"})
    if not changes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")
    for field, value in changes.items():
        setattr(cr, field, value)
    record_audit(db, cr, AuditEventType.UPDATED, body.actor_id or cr.requester_id,
                 changed_fields=list(changes))
    return save(db, cr)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


@app.post("/change-requests/{id}/ai-analyze", response_model=ChangeRequest, tags=["actions"])
def ai_analyze(id: UUID, db: Session = Depends(get_db)):
    cr = get_or_404(db, id)
    # TODO(ai): call your AI module here and store its output, e.g.
    #   result = analyze(cr)
    #   cr.ai_summary = result.summary
    #   cr.ai_missing_information = result.missing_information
    #   record_audit(db, cr, AuditEventType.AI_ANALYZED, "ai", model=result.model)
    #   return save(db, cr)
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "AI analysis is not connected yet")


@app.get("/change-requests/{id}/items", response_model=list[ChangeRequestItem], tags=["change-requests"])
def list_items(id: UUID, db: Session = Depends(get_db)):
    """The uploaded changes in CSV order, each with the problems found by the latest validation."""
    return get_or_404(db, id).items


@app.put(
    "/change-requests/{id}/csv",
    response_model=ChangeRequestDetail,
    tags=["change-requests"],
    responses={422: {"model": CsvUploadError, "description": "The CSV was rejected; nothing was changed"}},
)
async def replace_csv(
    id: UUID,
    file: UploadFile = File(..., description="The corrected template (.csv)"),
    actor_id: Optional[UUID] = Form(None, description="Who uploads it; defaults to the requester"),
    db: Session = Depends(get_db),
):
    """Replace the changes of a DRAFT or VALIDATION_FAILED request with a corrected CSV.

    A VALIDATION_FAILED request goes back to DRAFT. Validate it again afterwards.
    """
    cr = get_or_404(db, id)
    require_editable(cr, "replace the CSV of")
    actor = actor_id or cr.requester_id
    require_user(db, actor, "Actor")
    rows, error = await read_csv_or_error(file)
    if error:
        return error

    cr.items.clear()
    db.flush()  # delete the old rows first, so the unique constraint isn't hit
    cr.items.extend(to_items(rows))
    cr.requested_change = summarise(rows)
    cr.requires_approval = None  # the old validation no longer applies
    cr.approval_reasons = None
    record_audit(db, cr, AuditEventType.CSV_UPLOADED, actor, new_value=file.filename, rows=len(rows))
    if cr.status != ChangeRequestStatus.DRAFT:
        set_status(db, cr, ChangeRequestStatus.DRAFT, AuditEventType.STATUS_CHANGED, actor,
                   reason="Corrected CSV uploaded")
    return save(db, cr)


@app.post("/change-requests/{id}/validate", response_model=ValidateResponse, tags=["actions"])
def validate(id: UUID, body: ActorRequest, db: Session = Depends(get_db)):
    """Validate a draft: data checks (Salesforce, other open requests) plus business rules.

    The request stays in DRAFT. The result is stored on the request
    (`requires_approval`, `approval_reasons`), on each item (`issues`) and in the
    audit trail, and is also available from GET /change-requests/{id}/validation.
    Run it again at any time, e.g. after replacing the CSV.
    """
    cr = get_or_404(db, id)
    require_editable(cr, "validate")
    if not cr.items:
        raise HTTPException(422, "This request has no changes. Create it by uploading a CSV "
                                 "to POST /change-requests/upload.")
    require_user(db, body.actor_id, "Actor")

    items = list(cr.items)
    rules = business_rules.evaluate(items)
    try:
        data_issues = validate_request(db, cr.account_id, items)
    except Exception as exc:  # report it instead of failing the whole call
        data_issues = [ValidationIssue(code="validation_error",
                                       message=f"Validation could not be completed: {exc}. Try again.")]
    report = ValidationReport.build(cr.account_id, len(items), rules.issues + data_issues,
                                    rules.requires_approval, rules.approval_reasons)

    by_row: dict[int, list[dict]] = {}
    for issue in report.issues:
        if issue.row is not None:
            by_row.setdefault(issue.row, []).append(issue.model_dump(mode="json"))
    for item in items:
        item.issues = by_row.get(item.row_number, [])
    cr.requires_approval = report.requires_approval
    cr.approval_reasons = [r.model_dump(mode="json") for r in report.approval_reasons]

    record_audit(
        db, cr,
        AuditEventType.VALIDATION_PASSED if report.valid else AuditEventType.VALIDATION_FAILED,
        body.actor_id,
        new_value="valid" if report.valid else "invalid",
        errors=report.error_count, warnings=report.warning_count,
        requires_approval=report.requires_approval, comment=body.comment,
        report=report.model_dump(mode="json"),
    )
    save(db, cr)
    # TODO(workflow): when submitting is switched back on, valid requests move on from here
    # (e.g. to READY_FOR_APPROVAL, routed to an approver when requires_approval is true).
    return ValidateResponse(change_request=ChangeRequest.model_validate(cr), validation=report)


@app.get("/change-requests/{id}/validation", response_model=ValidationReport, tags=["change-requests"])
def latest_validation(id: UUID, db: Session = Depends(get_db)):
    """The report from the latest validation (kept in the audit trail)."""
    get_or_404(db, id)
    event = db.scalar(
        select(AuditEventORM)
        .where(AuditEventORM.change_request_id == id)
        .where(AuditEventORM.event_type.in_([AuditEventType.VALIDATION_PASSED,
                                             AuditEventType.VALIDATION_FAILED]))
        .order_by(AuditEventORM.created_at.desc())
        .limit(1)
    )
    if event is None or not event.event_metadata or "report" not in event.event_metadata:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This request hasn't been validated yet.")
    return event.event_metadata["report"]


@app.post("/change-requests/{id}/assign", response_model=ChangeRequest, tags=["actions"])
def assign(id: UUID, body: AssignRequest, db: Session = Depends(get_db)):
    cr = get_or_404(db, id)
    require_user(db, body.approver_id, "Approver")
    previous = str(cr.assigned_approver_id) if cr.assigned_approver_id else None
    cr.assigned_approver_id = body.approver_id
    record_audit(db, cr, AuditEventType.ASSIGNED, body.actor_id, previous, str(body.approver_id))
    return save(db, cr)


@app.post("/change-requests/{id}/request-information", response_model=ChangeRequest, tags=["actions"])
def request_information(id: UUID, body: DecisionRequest, db: Session = Depends(get_db)):
    cr = get_or_404(db, id)
    record_decision(db, cr, body, Decision.NEEDS_INFO)
    set_status(db, cr, ChangeRequestStatus.DRAFT, AuditEventType.INFORMATION_REQUESTED,
               body.approver_id, comment=body.comment)
    return save(db, cr)


@app.post("/change-requests/{id}/approve", response_model=ChangeRequest, tags=["actions"])
def approve(id: UUID, body: DecisionRequest, db: Session = Depends(get_db)):
    cr = get_or_404(db, id)
    record_decision(db, cr, body, Decision.APPROVED)
    set_status(db, cr, ChangeRequestStatus.APPROVED, AuditEventType.APPROVED,
               body.approver_id, comment=body.comment)
    return save(db, cr)


@app.post("/change-requests/{id}/reject", response_model=ChangeRequest, tags=["actions"])
def reject(id: UUID, body: DecisionRequest, db: Session = Depends(get_db)):
    cr = get_or_404(db, id)
    record_decision(db, cr, body, Decision.REJECTED)
    set_status(db, cr, ChangeRequestStatus.REJECTED, AuditEventType.REJECTED,
               body.approver_id, comment=body.comment)
    return save(db, cr)


@app.post("/change-requests/{id}/implement", response_model=ChangeRequest, tags=["actions"])
def implement(id: UUID, body: ImplementRequest, db: Session = Depends(get_db)):
    cr = get_or_404(db, id)
    set_status(db, cr, ChangeRequestStatus(body.outcome.value), AuditEventType.IMPLEMENTED,
               body.actor_id, notes=body.notes)
    return save(db, cr)


@app.post("/change-requests/{id}/close", response_model=ChangeRequest, tags=["actions"])
def close(id: UUID, body: ActorRequest, db: Session = Depends(get_db)):
    cr = get_or_404(db, id)
    # There is no CLOSED status, so closing is recorded in the audit trail only.
    record_audit(db, cr, AuditEventType.CLOSED, body.actor_id, comment=body.comment)
    return save(db, cr)


# ---------------------------------------------------------------------------
# Audit & webhooks
# ---------------------------------------------------------------------------


@app.get("/change-requests/{id}/audit-events", response_model=list[AuditEvent], tags=["audit"])
def get_audit_events(id: UUID, db: Session = Depends(get_db)):
    get_or_404(db, id)
    query = (
        select(AuditEventORM)
        .where(AuditEventORM.change_request_id == id)
        .order_by(AuditEventORM.created_at)
    )
    return db.scalars(query).all()


@app.post("/webhooks/n8n/request-submitted", tags=["webhooks"])
def n8n_request_submitted(
    payload: N8nRequestSubmittedPayload,
    x_webhook_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    if x_webhook_secret != N8N_WEBHOOK_SECRET:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook secret")
    cr = get_or_404(db, payload.change_request_id)
    if payload.status and payload.status != cr.status:
        set_status(db, cr, payload.status, AuditEventType.STATUS_CHANGED, "n8n",
                   workflow_id=payload.workflow_id, data=payload.data)
    else:
        record_audit(db, cr, AuditEventType.WEBHOOK_RECEIVED, "n8n",
                     workflow_id=payload.workflow_id, data=payload.data)
    save(db, cr)
    return {"received": True, "change_request_id": cr.account_id, "status": cr.status}
"""
Change Request Management API (FastAPI + PostgreSQL)
 
Start the database:  docker compose up -d
Run the API:         uvicorn main:app --reload
Docs:                http://127.0.0.1:8000/docs
 
Schemas live in schemas.py, tables and the connection in database.py.
Workflow rules and AI assistance go in their own modules; the places to call
them are marked with TODO below.
"""
 
from __future__ import annotations
 
import os
from contextlib import asynccontextmanager
from typing import Any, Optional
from uuid import UUID
 
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
 
from database import (
    ApprovalDecisionORM,
    AuditEventORM,
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
    ChangeRequestStatus,
    ChangeRequestUpdate,
    Decision,
    DecisionRequest,
    ImplementRequest,
    N8nRequestSubmittedPayload,
    User,
    UserCreate,
    utcnow,
)
 
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
 
 
@app.post("/change-requests/{id}/submit", response_model=ChangeRequest, tags=["actions"])
def submit(id: UUID, body: ActorRequest, db: Session = Depends(get_db)):
    cr = get_or_404(db, id)
    # TODO(workflow): validate required fields before submitting.
    set_status(db, cr, ChangeRequestStatus.SUBMITTED, AuditEventType.SUBMITTED,
               body.actor_id, comment=body.comment)
    save(db, cr)
    # TODO(workflow): trigger the n8n validation workflow here.
    return cr
 
 
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
 


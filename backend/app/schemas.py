"""
Pydantic schemas for the Change Request API.

Tables: users, change_requests, change_request_items, approval_decisions, audit_events.
Each table has a Create model (request body), and a full model (response / stored record).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ORMModel(BaseModel):
    """Base for stored records; lets you build them from ORM objects later."""
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class UserRole(str, Enum):
    REQUESTER = "REQUESTER"
    CRM_ADMIN = "CRM_ADMIN"
    SALES_MANAGER = "SALES_MANAGER"
    REVOPS_MANAGER = "REVOPS_MANAGER"
    COMPLIANCE_MANAGER = "COMPLIANCE_MANAGER"


class Priority(str, Enum):
    LOW = "Low"
    MED = "Med"
    HIGH = "High"


class RiskLevel(str, Enum):
    LOW = "Low"
    MED = "Med"
    HIGH = "High"


class ChangeRequestStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    VALIDATING = "VALIDATING"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"


class Decision(str, Enum):
    APPROVED = "Approved"
    REJECTED = "Rejected"
    NEEDS_INFO = "Needs info"


class AuditEventType(str, Enum):
    CREATED = "Created"
    UPDATED = "Updated"
    CSV_UPLOADED = "CSV uploaded"
    VALIDATION_PASSED = "Validation passed"
    VALIDATION_FAILED = "Validation failed"
    AI_ANALYZED = "AI analyzed"
    SUBMITTED = "Submitted"
    STATUS_CHANGED = "Status changed"
    ASSIGNED = "Assigned"
    INFORMATION_REQUESTED = "Information requested"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    IMPLEMENTED = "Implemented"
    CLOSED = "Closed"
    WEBHOOK_RECEIVED = "Webhook received"


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------


class UserCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")  # unique
    role: UserRole = UserRole.REQUESTER


class User(ORMModel):
    account_id: UUID = Field(default_factory=uuid4)  # primary key
    name: str
    email: str
    role: UserRole


# ---------------------------------------------------------------------------
# change_requests
# ---------------------------------------------------------------------------


class ChangeRequestCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    description: Optional[str] = None  # original user input
    business_justification: str = Field(..., min_length=1)
    requested_change: str = Field(..., min_length=1)
    category: Optional[str] = None
    priority: Optional[Priority] = None
    risk_level: Optional[RiskLevel] = None
    requester_id: UUID


class ChangeRequestUpdate(BaseModel):
    """PATCH body: only the fields sent are changed."""
    title: Optional[str] = Field(None, min_length=3, max_length=200)
    description: Optional[str] = None
    business_justification: Optional[str] = Field(None, min_length=1)
    requested_change: Optional[str] = Field(None, min_length=1)
    category: Optional[str] = None
    priority: Optional[Priority] = None
    risk_level: Optional[RiskLevel] = None
    actor_id: Optional[UUID] = None  # who is editing; not stored on the request


class ChangeRequest(ORMModel):
    account_id: UUID = Field(default_factory=uuid4)  # primary key
    title: str
    description: Optional[str] = None
    business_justification: str
    requested_change: str
    category: Optional[str] = None
    priority: Optional[Priority] = None
    risk_level: Optional[RiskLevel] = None
    status: ChangeRequestStatus = ChangeRequestStatus.DRAFT
    requester_id: UUID  # FK -> users.account_id
    assigned_approver_id: Optional[UUID] = None  # FK -> users.account_id, null until routed
    ai_summary: Optional[str] = None
    ai_missing_information: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# approval_decisions
# ---------------------------------------------------------------------------


class ApprovalDecisionCreate(BaseModel):
    approver_id: UUID
    decision: Decision
    comment: str = Field(..., min_length=1)


class ApprovalDecision(ORMModel):
    account_id: UUID = Field(default_factory=uuid4)  # primary key
    change_request_id: UUID  # FK -> change_requests.account_id
    approver_id: UUID  # FK -> users.account_id
    decision: Decision
    comment: str
    decided_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# audit_events
# ---------------------------------------------------------------------------


class AuditEvent(ORMModel):
    account_id: UUID = Field(default_factory=uuid4)  # primary key
    change_request_id: UUID  # FK -> change_requests.account_id
    event_type: AuditEventType
    actor_id: str  # user id or a system actor such as "system", "ai", "n8n"
    previous_value: Optional[str] = None
    new_value: Optional[str] = None
    event_metadata: Optional[dict[str, Any]] = None  # workflow or AI metadata
    created_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Action request bodies (POST /change-requests/{id}/...)
# ---------------------------------------------------------------------------


class ActorRequest(BaseModel):
    """submit, close."""
    actor_id: UUID
    comment: Optional[str] = None


class AssignRequest(BaseModel):
    actor_id: UUID
    approver_id: UUID


class DecisionRequest(BaseModel):
    """approve, reject, request-information. The comment is required."""
    approver_id: UUID
    comment: str = Field(..., min_length=1)


class ImplementOutcome(str, Enum):
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"


class ImplementRequest(BaseModel):
    actor_id: UUID
    outcome: ImplementOutcome = ImplementOutcome.COMPLETED
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# change_request_items (one row of the uploaded CSV = one Salesforce account)
# ---------------------------------------------------------------------------


class ChangeRequestItem(ORMModel):
    """One uploaded CSV row. The four identifying fields are always filled in;
    the five changeable fields are None when the row doesn't change them."""
    account_id: UUID                 # primary key of this item
    change_request_id: UUID          # FK -> change_requests.account_id
    row_number: int                  # line in the CSV (header = line 1)

    # Identifying fields (mandatory in the CSV)
    salesforce_account_id: str       # the CSV's account_id column
    name: str
    email: str
    role: str

    # Changeable fields (optional in the CSV; None = no change)
    country: Optional[str] = None
    industry: Optional[str] = None
    account_owner: Optional[str] = None
    amount: Optional[Decimal] = None
    record_status: Optional[str] = None  # the CSV's status column


class ChangeRequestDetail(ChangeRequest):
    """A change request together with its uploaded rows."""
    items: list[ChangeRequestItem] = Field(default_factory=list)


class CsvIssue(BaseModel):
    """One problem found in an uploaded CSV."""
    row: Optional[int] = None      # CSV line number (header = line 1); None = whole file
    column: Optional[str] = None
    code: str                      # machine-readable, e.g. "missing_value", "duplicate_account"
    message: str                   # human-readable explanation


class CsvUploadError(BaseModel):
    """422 response body when an uploaded CSV is rejected. Nothing is stored."""
    message: str
    filename: Optional[str] = None
    rows_checked: int
    errors: list[CsvIssue]


class N8nRequestSubmittedPayload(BaseModel):
    """Callback from the n8n workflow after a request is submitted."""
    change_request_id: UUID
    status: Optional[ChangeRequestStatus] = None  # e.g. VALIDATING, VALIDATION_FAILED, READY_FOR_APPROVAL
    workflow_id: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)
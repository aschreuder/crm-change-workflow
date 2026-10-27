"""
Pydantic schemas for the Change Request API.

Tables: users, change_requests, change_request_items, approval_decisions, audit_events.
Each table has a Create model (request body), and a full model (response / stored record).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    AI_CONFIRMED = "AI analysis confirmed"
    AI_REJECTED = "AI analysis rejected"
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


class ApprovalReason(BaseModel):
    """Why a request needs business approval (see business_rules.py)."""
    code: str                      # e.g. "account_owner_change", "too_many_accounts"
    message: str
    rows: list[int] = Field(default_factory=list)  # CSV lines that triggered it, if any


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
    requires_approval: Optional[bool] = None  # set by the business rules; None = not validated yet
    approval_reasons: list[ApprovalReason] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @field_validator("approval_reasons", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return [] if value is None else value


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
# Validation issues
# ---------------------------------------------------------------------------


class IssueSeverity(str, Enum):
    ERROR = "error"      # blocks the request
    WARNING = "warning"  # shown to the user, does not block


class ValidationIssue(BaseModel):
    """One problem found in an uploaded CSV or during validation."""
    row: Optional[int] = None      # CSV line number (header = line 1); None = whole file/request
    column: Optional[str] = None
    code: str                      # machine-readable, e.g. "missing_value", "stale_current_value"
    message: str                   # human-readable explanation
    severity: IssueSeverity = IssueSeverity.ERROR


# ---------------------------------------------------------------------------
# change_request_items (one row of the uploaded CSV = one field change)
# ---------------------------------------------------------------------------


class SalesforceObject(str, Enum):
    ACCOUNT = "Account"
    OPPORTUNITY = "Opportunity"
    LEAD = "Lead"


class ChangeRequestItem(ORMModel):
    """One uploaded CSV row: a single field change on a single Salesforce record."""
    account_id: UUID                 # primary key of this item
    change_request_id: UUID          # FK -> change_requests.account_id
    row_number: int                  # line in the CSV (header = line 1)

    # Who/what the change is about (mandatory in the CSV)
    salesforce_account_id: str       # the CSV's account_id column (18-character Salesforce ID)
    name: str
    email: str
    role: str

    # The change itself
    object_type: SalesforceObject    # the CSV's object column
    record_id: str                   # 18-character Salesforce ID of the record to change
    field: str                       # country, industry, account_owner, amount or status
    current_value: str               # what Salesforce should have now ("" = blank)
    new_value: str

    # Problems found by the latest validation (empty when the row passed)
    issues: list[ValidationIssue] = Field(default_factory=list)


class ChangeRequestDetail(ChangeRequest):
    """A change request together with its uploaded rows."""
    items: list[ChangeRequestItem] = Field(default_factory=list)


class CsvUploadError(BaseModel):
    """422 response body when an uploaded CSV is rejected. Nothing is stored."""
    message: str
    filename: Optional[str] = None
    rows_checked: int
    errors: list[ValidationIssue]


class ValidationReport(BaseModel):
    """Result of validating a request: data checks plus business rules."""
    change_request_id: UUID
    valid: bool                    # False when there is at least one error
    rows_checked: int
    error_count: int
    warning_count: int
    issues: list[ValidationIssue]
    requires_approval: bool
    approval_reasons: list[ApprovalReason] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=utcnow)

    @classmethod
    def build(cls, change_request_id: UUID, rows_checked: int, issues: list[ValidationIssue],
              requires_approval: bool, approval_reasons: list[ApprovalReason]) -> "ValidationReport":
        errors = sum(i.severity == IssueSeverity.ERROR for i in issues)
        return cls(
            change_request_id=change_request_id,
            valid=errors == 0,
            rows_checked=rows_checked,
            error_count=errors,
            warning_count=len(issues) - errors,
            issues=sorted(issues, key=lambda i: (i.row or 0, i.column or "")),
            requires_approval=requires_approval,
            approval_reasons=approval_reasons,
        )


class ValidateResponse(BaseModel):
    """POST /change-requests/{id}/validate: the request after validation, plus the report."""
    change_request: ChangeRequest
    validation: ValidationReport


class N8nRequestSubmittedPayload(BaseModel):
    """Callback from the n8n workflow after a request is submitted."""
    change_request_id: UUID
    status: Optional[ChangeRequestStatus] = None  # e.g. VALIDATING, VALIDATION_FAILED, READY_FOR_APPROVAL
    workflow_id: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# ai_analyses (stored separately from the change request)
# ---------------------------------------------------------------------------


class ChangeCategory(str, Enum):
    """Categories the AI chooses from. Edit this list to match how your business groups changes."""
    OWNERSHIP_TRANSFER = "Ownership transfer"        # account owner changes, territory realignment
    DATA_CORRECTION = "Data correction"              # fixing country, industry or other master data
    DEAL_UPDATE = "Deal update"                      # opportunity amount changes
    LEAD_MANAGEMENT = "Lead management"              # lead status changes
    MIXED = "Mixed"                                  # several of the above, none dominant


class AIAnalysisOutput(BaseModel):
    """What the AI model must return. Also used as the structured-output schema sent to Claude,
    so it avoids constraints the API doesn't support (min/max length, numeric ranges)."""
    summary: str = Field(description="2-4 plain-English sentences: what changes, on how many records, and why.")
    category: ChangeCategory
    category_reason: str = Field(description="One sentence explaining the category.")
    risk_level: RiskLevel
    risk_reasons: list[str] = Field(description="Short reasons for the risk level, most important first.")
    missing_information: list[str] = Field(
        description="Information a reviewer would need but the request doesn't give. Empty if nothing is missing.")


class AIAnalysisStatus(str, Enum):
    PENDING_REVIEW = "PENDING_REVIEW"  # waiting for the requester to confirm or reject
    CONFIRMED = "CONFIRMED"            # requester accepted it (category + risk copied to the request)
    REJECTED = "REJECTED"              # requester rejected it
    SUPERSEDED = "SUPERSEDED"          # replaced by a newer analysis or a new CSV before review


class AIAnalysis(ORMModel):
    account_id: UUID                   # primary key
    change_request_id: UUID            # FK -> change_requests.account_id
    status: AIAnalysisStatus
    summary: str
    category: ChangeCategory
    category_reason: str
    risk_level: RiskLevel
    risk_reasons: list[str]
    missing_information: list[str]
    model: str                         # e.g. "claude-sonnet-5-5", or "mock"
    requested_by: UUID                 # FK -> users.account_id
    created_at: datetime
    reviewed_by: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None
    review_comment: Optional[str] = None
    # What the requester confirmed; differs from category/risk_level when they overrode the AI.
    confirmed_category: Optional[ChangeCategory] = None
    confirmed_risk_level: Optional[RiskLevel] = None


class AIAnalysisConfirm(BaseModel):
    """Confirm an analysis. Leave category / risk_level out to accept the AI's suggestion,
    or set them to correct it."""
    actor_id: UUID                     # must be the requester
    category: Optional[ChangeCategory] = None
    risk_level: Optional[RiskLevel] = None
    comment: Optional[str] = None


class AIAnalysisReject(BaseModel):
    actor_id: UUID                     # must be the requester
    comment: Optional[str] = None
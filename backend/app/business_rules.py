"""
Business rules, checked by POST /change-requests/{id}/validate.

Rules that decide whether a request needs business approval:
1. Any account_owner change requires approval.
2. A request affecting more than MAX_ACCOUNTS_WITHOUT_APPROVAL accounts requires approval.

Rules that make a request invalid:
3. The same record and field may be changed only once per request.

Approval rules don't make a request invalid: they only set `requires_approval`
and say why. Add new rules as functions and list them in `evaluate()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from csv_template import check_duplicates_in_file
from schemas import ApprovalReason, SalesforceObject, ValidationIssue

MAX_ACCOUNTS_WITHOUT_APPROVAL = 25
FIELDS_REQUIRING_APPROVAL = {"account_owner"}


class ChangeRow(Protocol):
    row_number: int
    salesforce_account_id: str
    object_type: SalesforceObject
    record_id: str
    field: str


@dataclass
class BusinessRuleResult:
    requires_approval: bool = False
    approval_reasons: list[ApprovalReason] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Approval rules
# ---------------------------------------------------------------------------


def rule_field_requires_approval(rows: list[ChangeRow]) -> list[ApprovalReason]:
    reasons = []
    for field_name in sorted(FIELDS_REQUIRING_APPROVAL):
        lines = [r.row_number for r in rows if r.field == field_name]
        if lines:
            reasons.append(ApprovalReason(
                code=f"{field_name}_change",
                message=f"Changes {field_name} on {len(lines)} record(s) (line(s) "
                        f"{', '.join(map(str, lines))}). {field_name} changes need business approval.",
                rows=lines,
            ))
    return reasons


def rule_too_many_accounts(rows: list[ChangeRow]) -> list[ApprovalReason]:
    # Salesforce IDs: the first 15 characters identify the record.
    accounts = {r.salesforce_account_id[:15] for r in rows}
    if len(accounts) > MAX_ACCOUNTS_WITHOUT_APPROVAL:
        return [ApprovalReason(
            code="too_many_accounts",
            message=f"Affects {len(accounts)} accounts. Requests affecting more than "
                    f"{MAX_ACCOUNTS_WITHOUT_APPROVAL} accounts need business approval.",
        )]
    return []


# ---------------------------------------------------------------------------
# Validity rules
# ---------------------------------------------------------------------------


def rule_one_change_per_record_field(rows: list[ChangeRow]) -> list[ValidationIssue]:
    return check_duplicates_in_file(rows)


# ---------------------------------------------------------------------------
# All rules
# ---------------------------------------------------------------------------


def evaluate(rows: list[ChangeRow]) -> BusinessRuleResult:
    reasons = rule_field_requires_approval(rows) + rule_too_many_accounts(rows)
    return BusinessRuleResult(
        requires_approval=bool(reasons),
        approval_reasons=reasons,
        issues=rule_one_change_per_record_field(rows),
    )
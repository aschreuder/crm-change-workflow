"""
Validation run on submit (the format checks already ran on upload, in csv_template.py).

A change is identified by (record_id, field). The checks:

1. Duplicate in file   - the same record + field appears twice in the request.
2. Open-request clash  - another request that is still in progress (submitted,
                         validating, ready for approval or approved) already
                         changes the same record + field.
3. Salesforce          - the record exists; an opportunity belongs to the row's
                         account; the change hasn't already been made (Salesforce
                         already has new_value); and current_value matches what
                         Salesforce has now.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional, Protocol
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from csv_template import ALLOWED_FIELDS, EMPTY_TOKEN, check_duplicates_in_file, same_sf_id
from salesforce import SalesforceClient, SalesforceUnavailable, get_salesforce_client
from schemas import ChangeRequestStatus, IssueSeverity, SalesforceObject, ValidationIssue

# Requests in these statuses "own" the record + field pairs they change.
OPEN_STATUSES = {
    ChangeRequestStatus.SUBMITTED,
    ChangeRequestStatus.VALIDATING,
    ChangeRequestStatus.READY_FOR_APPROVAL,
    ChangeRequestStatus.APPROVED,
}


class ChangeRow(Protocol):
    """A stored change_request_items row (or anything shaped like one)."""
    row_number: int
    salesforce_account_id: str
    object_type: SalesforceObject
    record_id: str
    field: str
    current_value: str
    new_value: str


def _show(value: Optional[str]) -> str:
    return EMPTY_TOKEN if not value else f"'{value}'"


# ---------------------------------------------------------------------------
# 2. Clashes with other open requests
# ---------------------------------------------------------------------------


def check_open_request_conflicts(db: Session, change_request_id: UUID,
                                 rows: list[ChangeRow]) -> list[ValidationIssue]:
    # Imported here because database.py imports this project's modules too.
    from database import ChangeRequestItemORM, ChangeRequestORM

    if not rows:
        return []
    by_key = {(r.record_id.upper(), r.field): r for r in rows}
    stmt = (
        select(ChangeRequestItemORM.record_id, ChangeRequestItemORM.field,
               ChangeRequestORM.account_id, ChangeRequestORM.title, ChangeRequestORM.status)
        .join(ChangeRequestORM, ChangeRequestItemORM.change_request_id == ChangeRequestORM.account_id)
        .where(ChangeRequestORM.account_id != change_request_id)
        .where(ChangeRequestORM.status.in_(OPEN_STATUSES))
        .where(tuple_(ChangeRequestItemORM.record_id, ChangeRequestItemORM.field)
               .in_([(r.record_id, r.field) for r in rows]))
    )
    issues = []
    for record_id, field, other_id, other_title, other_status in db.execute(stmt):
        row = by_key.get((record_id.upper(), field))
        if row is None:
            continue
        issues.append(ValidationIssue(
            row=row.row_number, column="record_id", code="conflicts_with_open_request",
            message=f"{SalesforceObject(row.object_type).value} {row.record_id} '{row.field}' is already "
                    f"being changed by request '{other_title}' ({other_id}, status "
                    f"{ChangeRequestStatus(other_status).value}). Wait until that request is finished.",
        ))
    return issues


# ---------------------------------------------------------------------------
# 3. Salesforce
# ---------------------------------------------------------------------------


def check_against_salesforce(client: Optional[SalesforceClient],
                             rows: list[ChangeRow]) -> list[ValidationIssue]:
    if not rows:
        return []
    if client is None:
        return [ValidationIssue(
            code="salesforce_check_skipped", severity=IssueSeverity.WARNING,
            message="Salesforce checks are switched off (SALESFORCE_MODE=off), so record existence "
                    "and current values were not checked.",
        )]

    grouped: dict[SalesforceObject, list[ChangeRow]] = defaultdict(list)
    for row in rows:
        grouped[SalesforceObject(row.object_type)].append(row)

    issues = []
    for obj, obj_rows in grouped.items():
        # One lookup per object, asking only for the fields needed.
        fields = sorted({ALLOWED_FIELDS[r.field].salesforce_field for r in obj_rows})
        if obj == SalesforceObject.OPPORTUNITY:
            fields.append("AccountId")
        try:
            found = client.get_records(obj, sorted({r.record_id for r in obj_rows}), fields)
        except Exception as exc:  # network/auth problems shouldn't crash validation
            issues.append(ValidationIssue(
                code="salesforce_unavailable",
                message=f"Could not read {obj.value} records from Salesforce: {exc}. Try submitting again later.",
            ))
            continue
        found_upper = {rid.upper(): values for rid, values in found.items()}

        for row in obj_rows:
            record = found_upper.get(row.record_id.upper())
            if record is None:
                issues.append(ValidationIssue(
                    row=row.row_number, column="record_id", code="record_not_found",
                    message=f"{obj.value} {row.record_id} does not exist in Salesforce.",
                ))
                continue

            if obj == SalesforceObject.OPPORTUNITY:
                owner_account = record.get("AccountId")
                if owner_account and not same_sf_id(owner_account, row.salesforce_account_id):
                    issues.append(ValidationIssue(
                        row=row.row_number, column="record_id", code="wrong_account",
                        message=f"Opportunity {row.record_id} belongs to account {owner_account}, "
                                f"not {row.salesforce_account_id}.",
                    ))
                    continue

            rule = ALLOWED_FIELDS[row.field]
            live = (record.get(rule.salesforce_field) or "").strip()
            if rule.same(live, row.new_value):
                issues.append(ValidationIssue(
                    row=row.row_number, column="new_value", code="already_applied",
                    message=f"Salesforce already has {row.field} = {_show(live)}. This change has already been made.",
                ))
            elif not rule.same(live, row.current_value):
                issues.append(ValidationIssue(
                    row=row.row_number, column="current_value", code="stale_current_value",
                    message=f"current_value doesn't match Salesforce: Salesforce has {_show(live)}, "
                            f"the file says {_show(row.current_value)}.",
                ))
    return issues


# ---------------------------------------------------------------------------
# Everything
# ---------------------------------------------------------------------------


def validate_request(db: Session, change_request_id: UUID, rows: list[ChangeRow]) -> list[ValidationIssue]:
    if not rows:
        return [ValidationIssue(code="no_changes", message="This request has no changes to validate.")]
    try:
        client = get_salesforce_client()
        salesforce_issues = check_against_salesforce(client, rows)
    except SalesforceUnavailable as exc:
        salesforce_issues = [ValidationIssue(code="salesforce_unavailable", message=str(exc))]
    return (
        check_duplicates_in_file(rows)
        + check_open_request_conflicts(db, change_request_id, rows)
        + salesforce_issues
    )
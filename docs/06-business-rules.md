BR-001: Account existence <br>
The account ID must exist.

BR-002: Current-value match <br>
The submitted current_value must equal the value stored in the CRM. <br>
This prevents overwriting a newer change.

BR-003: Editable fields <br>
Only these fields may be changed: <br>
country <br>
industry <br>
account_owner <br>
account_tier <br>
lifecycle_stage <br>

BR-004: Owner changes <br>
Any account_owner change requires business approval.

BR-005: Large batches <br>
A request affecting more than 25 accounts requires approval.

BR-006: Duplicate instructions <br>
Two changes to the same account and field in one request are invalid.

BR-007: AI output must be presented as a recommendation and confirmed by the requester.

BR-008: A requester cannot approve their own request.

BR-009: Low-risk requests are assigned to the CRM administrator

BR-010: Med-risk requests are assigned to the Sales manager

BR-010: High-risk requests require compliance review

BR-011: Approval and rejection require an approver comment.

BR-012: Every action should create an audit event. This includes status changes, commends added, etc.

BR-013: Audit events cannot be updated or deleted in the application API.

BR-014: The AI generated interpretation should be stored completely seperately from any of the human written text.

BR-015: A failed n8n worklfow should not invalidate the entire request. It should be a 'soft' failure.

### Assumptions
- Account IDs are unique.
- A single field is changed per CSV row.
- One request may contain multiple rows.
- The mock CRM is the source of truth.
- One manager role is sufficient for approval.
- Approval applies to the whole batch for the MVP.
- Partial execution is not supported initially.
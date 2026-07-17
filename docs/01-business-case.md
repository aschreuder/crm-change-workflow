The current CRM change process relies on spreadsheets, email, Slack and manual checking. Requests often omit required information, may contain stale current values and lack consistent approval controls.

This increases processing effor, creates a risk of incorrect CRM updates and makes it difficult to demonstrate who requested, approved and executed each change.

Typical requests:
- Reassigning account owners
- Correcting account countries
- Updating account industries
- Changing account tiers
- Changing lifecycle stages

Proposed solution:
Users submit a structured CSV of requested account changes. The system:
1. Validates the file.
2. Checks each request against the current CRM record.
3. Applies business rules.
4. Separates valid, invalid and approval-required changes.
5. Shows a before-and-after preview.
6. Routes sensitive requests for approval.
7. Applies approved changes.
8. Records every action in an audit log.
9. Reports operational and data-quality metrics.

Success criteria:
- User can submit a change request
- See validation results
- Approve or reject it
- Execute approved changes
- View the audit history
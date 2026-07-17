Desired state:

1. Submit a standard template based CSV via the frontend.
2. Platform validates the schema.
3. Validates if the records are unique.
4. Business-rule validation.
5. AI Analyzes request:
    - Summary
    - Category
    - Missing information
    - suggested risk level
6. Requester receives a preview outcome and confirms submission.
7. Request saved as SUBMITTED
8. FastAPI sends event to n8n
9. n8n applies approval-routing rules
10. Approver assigned
11. Low risk change -> CRM admin Approval -> Execution -> Audit log -> Reporting
12. Med risk change -> Sales Manager approval -> Execution -> Audit log -> Reporting
13. High risk change -> Sales Manager approval -> Execution -> Audit log -> Reporting
14. Request COMPLETED


A request can have the following statuses:
DRAFT
SUBMITTED
VALIDATING
VALIDATION_FAILED
READY_FOR_APPROVAL
APPROVED
REJECTED
COMPLETED
PARTIALLY_COMPLETED
FAILED
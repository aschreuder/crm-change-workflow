### US-001: Create a change request

As a CRM user,
I want to create a structured change request
So that the change can be evaluated and implemented consistently.

### Acceptance criteria
Given I am creating a new request
When I provide all mandatory fields
Then the system saves the request with a status of DRAFT
And a Request Created audit event is recorded

Given I omit a mandatory field
When I attempt to create or submit the request
Then the system identifies the missing field
And the request is not submitted

### US-002: Receive AI Assistance
As a requester

I want to receive an AI-generated summary and completeness check so that I can improve the quality of my request before submission.

### Acceptance criteria
Given a draft request contains descriptive text
When I request AI analysis
Then the system returns a summary, suggested category and missing-information list
And the AI response is stored separately from the original request
And the request remains in DRAFT status

### US-003: Route a submitted request
As a process owner

I want submitted requests routed according to risk so that the correct stakeholder reviews each request.

### Acceptance criteria
Submitted requests need to be routed by n8n based on the following criteria:
Low --> CRM administrator
Medium --> Sales operations manager
High --> Compliance representative
The routing should first be reviewed by the CRM administrator in all 3 cases.
And an Approver Assigned audit event is recorded

### US-004: Approve a request
As an assigned approver

I want to approve a valid request so that implementation can begin.

### Acceptance criteria
Given I am the assigned approver
And the request status is IN_REVIEW
When I approve the request with a comment
Then the status changes to APPROVED
And an approval decision is stored
And an Approved audit event is recorded

Given I am the requester
When I attempt to approve my own request
Then the system rejects the action
And the request status does not change
And an audit event is recorded
### users

| Field      | Type            | Notes                                                                   |
------------ | --------------- | ------------------------------------------------------------------------|
| account_id | Integer or UUID | Primary key                                                             |
| name       | string          | Required                                                                |
| email      | string          | Unique                                                                  |
| role       | string          | Requester, CRM_ADMIN, SALES_MANAGER, REVOPS_MANAGER, COMPLIANCE_MANAGER |

### change_requests

| Field                  | Type            | Notes                                               |
------------------------ | --------------- | ----------------------------------------------------|
| account_id             | Integer or UUID | Primary key                                         |
| title                  | string          | Required                                            |
| description            | text            | origninal user input                                |
| business_justification | text            | Required                                            |
| requested_change       | text            | Required                                            |
| category               | string          | Suggested or confirmed category                     |
| priority               | string          | Low, Med, High                                      |
| risk_level             | string          | Low, Med, High                                      |
| status                 | string          | Controlled status                                   |
| requester_id           | foreign key     | Required                                            |
| assigned_approver_id   | foreign key     | Nullable until routed                               |
| created_at             | timestamp       | Required                                            |
| updated_at             | timestamp       | Required                                            |
| requires approval      | text            | Required                                            |
| approval reasons       | text            | Required                                            |

### approval_decisions

| Field                  | Type            | Notes                                               |
------------------------ | --------------- | ----------------------------------------------------|
| account_id             | Integer or UUID | Primary key                                         |
| change_request_id      | foreign key     | Required                                            |
| approver_id            | foreign key     | Required                                            |
| decision               | string          | Approved, Rejected, Needs info                      |
| comment                | text            | Required                                            |
| decided_at             | timestamp       | Required                                            |

### audit_events

| Field                  | Type            | Notes                                               |
------------------------ | --------------- | ----------------------------------------------------|
| account_id             | Integer or UUID | Primary key                                         |
| change_request_id      | foreign key     | Required                                            |
| event_type             | text            | Created, Submitted, Assigned, Approved, etc.        |
| actor_id               | text            | User or system actor                                |
| previous_value         | text            | optional                                            |
| new_value              | string          | optional                                            |
| event_metadata         | string          | Workflow or AI metadata                             |
| created_at             | timestamp       | Required                                            |

### ai_analysis
        Column        |           Type           | Notes       |
----------------------+--------------------------+-------------|
 account_id           | uuid                     | Primary key |
 change_request_id    | uuid                     |             |
 status               | ai_analysis_status       |             |
 summary              | text                     |             |
 category             | change_category          |             |
 category_reason      | text                     |             |
 risk_level           | risk_level               |             |
 risk_reasons         | jsonb                    |             | 
 missing_information  | jsonb                    |             | 
 model                | character varying(100)   |             |
 raw_output           | jsonb                    |             | 
 requested_by         | uuid                     |             | 
 created_at           | timestamp with time zone |             | 
 reviewed_by          | uuid                     |             | 
 reviewed_at          | timestamp with time zone |             | 
 review_comment       | text                     |             |
 confirmed_category   | change_category          |             |
 confirmed_risk_level | risk_level               |             |



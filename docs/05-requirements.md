## Functional & Non-functional requirements

### Functional requirements
| ID     | Requirement                                                                   |
| ------ | ----------------------------------------------------------------------------- |
| FR-001 | The system shall accept a CSV change-request file                             |
| FR-002 | The system shall reject files missing mandatory columns                       |
| FR-003 | The system shall verify that every account ID exists                          |
| FR-004 | The system shall compare the supplied current value with the stored CRM value |
| FR-005 | The system shall validate proposed values against allowed values              |
| FR-006 | The system shall identify duplicate requests within the same batch            |
| FR-007 | The system shall determine whether a change requires approval                 |
| FR-008 | The system shall display current and proposed values before execution         |
| FR-009 | An approver shall be able to approve or reject a request                      |
| FR-010 | The system shall prevent execution before required approval                   |
| FR-011 | The system shall apply approved changes to the mock CRM                       |
| FR-012 | The system shall record each important action in an audit log                 |
| FR-013 | Users shall be able to view request status                                    |
| FR-014 | Users shall be able to view validation failures                               |
| FR-015 | The system shall expose basic process metrics                                 |

### Non-functional requirements
| ID      | Requirement                                                               |
| ------- | ------------------------------------------------------------------------- |
| NFR-001 | Validation of 500 records should complete within an agreed demo threshold |
| NFR-002 | The same input and database state must produce the same validation result |
| NFR-003 | Every executed field change must be traceable                             |
| NFR-004 | API inputs and outputs must use documented schemas                        |
| NFR-005 | The system must not execute a rejected request                            |
| NFR-006 | Tests must cover critical business rules                                  |
| NFR-007 | The solution must run locally using Docker Compose                        |
| NFR-008 | Synthetic data must be used throughout                                    |

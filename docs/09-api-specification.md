| Method        |	Endpoint	                            | Purpose                                     |
|-------------- | ----------------------------------------- | --------------------------------------------|
| POST	        | /change-requests	                        | Create a draft request                      |
| GET	        | /change-requests	                        | List requests                               |
| GET	        | /change-requests/{id}	                    | View one request                            |
| PATCH	        | /change-requests/{id}	                    | Update a draft or needs-information request |
| POST	        | /change-requests/{id}/ai-analyze	        | Generate AI assistance                      |
| POST	        | /change-requests/{id}/submit	            | Validate and submit                         |
| POST	        | /change-requests/{id}/assign	            | Register assigned approver                  |
| POST	        | /change-requests/{id}/request-information | Return request for clarification            |
| POST	        | /change-requests/{id}/approve	            | Approve request                             |
| POST	        | /change-requests/{id}/reject	            | Reject request                              |
| POST	        | /change-requests/{id}/implement	        | Mark approved request implemented           |
| POST	        | /change-requests/{id}/close	            | Close implemented request                   |
| GET	        | /change-requests/{id}/audit-events	    | Retrieve audit history                      |
| POST	        | /webhooks/n8n/request-submitted	        | Optional callback or workflow endpoint      |
| GET	        | /health	                                | Application health check                    |

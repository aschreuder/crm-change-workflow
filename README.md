# How to run the app
`uvicorn main:app --reload`

# Checking app help
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/health

# Docker setup
docker compose up -d # Start the DB
pip install -r requirements.txt
python database.py   # Create the tables

# exec into docker containter
`docker exec -it change-requests-db psql -U cr_user -d change_requests`

# Useful DB commands
\dt                         -- list tables
\d change_requests          -- show a table's columns
\dT                         -- list the enum types (statuses, roles…)
SELECT * FROM users;        -- SQL statements end with ;
\q                          -- quit
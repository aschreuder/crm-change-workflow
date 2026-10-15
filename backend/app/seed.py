"""
Seed the database with one test user per role.
 
Run:  python seed.py
 
Safe to run more than once: users are matched by email, so existing users are
updated rather than duplicated. The IDs are fixed, so you can paste them into
requests (e.g. as requester_id) and n8n workflows without looking them up.
"""
 
from __future__ import annotations
 
from uuid import UUID
 
from sqlalchemy import select
 
from database import SessionLocal, UserORM, check_connection, engine, init_db
from schemas import UserRole
 
SEED_USERS: list[dict] = [
    {
        "account_id": UUID("00000000-0000-0000-0000-000000000001"),
        "name": "Rita Requester",
        "email": "requester@example.com",
        "role": UserRole.REQUESTER,
    },
    {
        "account_id": UUID("00000000-0000-0000-0000-000000000002"),
        "name": "Carl CRM Admin",
        "email": "crm.admin@example.com",
        "role": UserRole.CRM_ADMIN,
    },
    {
        "account_id": UUID("00000000-0000-0000-0000-000000000003"),
        "name": "Sam Sales Manager",
        "email": "sales.manager@example.com",
        "role": UserRole.SALES_MANAGER,
    },
    {
        "account_id": UUID("00000000-0000-0000-0000-000000000004"),
        "name": "Rosa RevOps Manager",
        "email": "revops.manager@example.com",
        "role": UserRole.REVOPS_MANAGER,
    },
    {
        "account_id": UUID("00000000-0000-0000-0000-000000000005"),
        "name": "Chris Compliance Manager",
        "email": "compliance.manager@example.com",
        "role": UserRole.COMPLIANCE_MANAGER,
    },
]
 
 
def seed_users() -> None:
    created, updated = 0, 0
    with SessionLocal() as db:
        for data in SEED_USERS:
            user = db.scalar(select(UserORM).where(UserORM.email == data["email"]))
            if user is None:
                db.add(UserORM(**data))
                created += 1
            else:
                user.name = data["name"]
                user.role = data["role"]
                updated += 1
        db.commit()
 
        print(f"Seed complete: {created} created, {updated} updated.\n")
        print(f"{'ROLE':<20} {'ACCOUNT_ID':<38} EMAIL")
        for data in SEED_USERS:
            user = db.scalar(select(UserORM).where(UserORM.email == data["email"]))
            print(f"{user.role.value:<20} {str(user.account_id):<38} {user.email}")
 
 
if __name__ == "__main__":
    if not check_connection():
        raise SystemExit(f"Cannot reach the database at {engine.url!r}. Is `docker compose up -d` running?")
    init_db()
    seed_users()

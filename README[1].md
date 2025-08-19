# Leave Management System MVP

A minimal Leave Management System (LMS) with FastAPI + SQLite, suitable for a 50-employee startup and scalable to 500 with modest changes.

## Features (MVP)
- Add employees (Name, Email, Department, Joining Date)
- Apply for leave (Mon–Fri working days only; excludes weekends)
- Approve / Reject leave
- Fetch leave balance per employee
- SQLite database with simple schema
- Built-in validations:
  - Leave before joining date
  - Invalid dates (end before start)
  - Overlapping requests (pending/approved)
  - Exceeding available balance
  - Employee not found
  - No working days in the selected range
  - (MVP constraint) Leave cannot span calendar years

## Tech
- Python 3.10+
- FastAPI
- SQLAlchemy
- SQLite

## Quickstart

```bash
# 1) Create & activate venv (recommended)
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 2) Install deps
pip install -r requirements.txt

# 3) Run server
uvicorn app.main:app --reload

# 4) Open interactive docs
# Browse to http://127.0.0.1:8000/docs
```

## Default Assumptions
- Annual allocation: **20 days per calendar year** for all employees (can be changed in code).
- Balance is checked against **approved leaves** in the same calendar year.
- Working days = Monday–Friday; weekends are excluded. No holiday calendar in MVP.
- New hires can be added before their joining date, but cannot apply leave before it.
- Email is unique per employee.
- Approval re-checks overlap & balance at decision time to avoid race conditions.
- Leave cannot span multiple calendar years in MVP (keep logic/jobs simple).

## API Endpoints

### 1) Add Employee
**POST** `/employees`

Request:
```json
{
  "name": "Ava Kumar",
  "email": "ava@example.com",
  "department": "Engineering",
  "joining_date": "2025-08-01"
}
```

Response (201):
```json
{
  "id": 1,
  "name": "Ava Kumar",
  "email": "ava@example.com",
  "department": "Engineering",
  "joining_date": "2025-08-01",
  "annual_balance": 20
}
```

### 2) Apply Leave
**POST** `/leaves/apply`

Request:
```json
{
  "employee_id": 1,
  "start_date": "2025-08-18",
  "end_date": "2025-08-20",
  "reason": "Family event"
}
```

Response (201):
```json
{
  "id": 1,
  "employee_id": 1,
  "start_date": "2025-08-18",
  "end_date": "2025-08-20",
  "days": 3,
  "reason": "Family event",
  "status": "pending"
}
```

### 3) Approve/Reject Leave
**POST** `/leaves/{leave_id}/decision`

Request (approve):
```json
{ "approved": true }
```

Request (reject):
```json
{ "approved": false }
```

Response:
```json
{
  "id": 1,
  "employee_id": 1,
  "start_date": "2025-08-18",
  "end_date": "2025-08-20",
  "days": 3,
  "reason": "Family event",
  "status": "approved"
}
```

### 4) Get Balance
**GET** `/employees/{employee_id}/balance`

Response:
```json
{
  "employee_id": 1,
  "available_days": 17,
  "used_days": 3,
  "annual_allocation": 20,
  "year": 2025
}
```

### 5) List Leaves
**GET** `/leaves?employee_id=1&status=pending`

---

## Sample cURL

```bash
curl -X POST http://127.0.0.1:8000/employees -H "Content-Type: application/json" -d '{
  "name":"Ava Kumar","email":"ava@example.com","department":"Engineering","joining_date":"2025-08-01"
}'

curl -X POST http://127.0.0.1:8000/leaves/apply -H "Content-Type: application/json" -d '{
  "employee_id":1,"start_date":"2025-08-18","end_date":"2025-08-20","reason":"Family event"
}'

curl -X POST http://127.0.0.1:8000/leaves/1/decision -H "Content-Type: application/json" -d '{"approved": true}'

curl http://127.0.0.1:8000/employees/1/balance
```

## Edge Cases Covered
- Applying for leave before joining date → **400**
- Applying for more days than available → **400**
- Overlapping leave (pending/approved) → **409**
- Employee not found → **404**
- Invalid date range (end < start) → **400**
- No working days between start & end (e.g., weekend only) → **400**
- Approval after balance drops or new overlap occurs → **409/400** accordingly
- Duplicate employee email → **409**
- Leave crossing years (MVP restriction) → **400**

### Additional Edge Cases (documented or future work)
- Partial-day (half-day) leaves → future enhancement
- Public holiday calendar → future enhancement
- Different leave types (sick/annual/unpaid) with separate balances → future
- Pro-rated annual balance for mid-year joiners → future
- Carry forward policy → future
- Cancellation or modification of pending/approved leaves → future
- Manager roles & auth → future
- Timezone normalization → future (store dates as UTC days)

## High-Level Design (HLD)

See `diagrams/architecture.png`. In short:
- **Frontend**: SPA or simple admin UI (could be React/Vue) talks to FastAPI
- **Backend (FastAPI)**: CRUD for employees, leave application + decisions, validations
- **DB (SQLite)**: Employees, Leaves
- For 50 employees, SQLite is fine; for 500+ move to Postgres/MySQL, add indexes, background jobs, and caching.

### Scaling from 50 → 500 employees
- Migrate from SQLite to Postgres (set `DATABASE_URL` env).
- Add DB indexes on `employees.email`, `leaves.employee_id`, `leaves.status`, `leaves.start_date`, `leaves.end_date`.
- Move to gunicorn/uvicorn workers behind Nginx; containerize with Docker.
- Add simple auth/roles (HR vs Managers) and rate limiting.
- Use read/write separation if necessary; add a queue for email/Slack notifications.
- Introduce a holiday calendar table and computed balances via materialized views or nightly jobs.
- Add pagination to list endpoints.

## Deploy (Bonus)
- **Render**: create a free web service, set `Start Command` to `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **Railway/Heroku (if available)**: similar Procfile style
- **Vercel**: use serverless FastAPI adapters or deploy a Next.js frontend and host the API on Render/Railway.

### Environment
- Set `DATABASE_URL` (optional). Defaults to local SQLite file `leave_mvp.sqlite3`.

## Diagrams
- `diagrams/architecture.png` (exported) — generated programmatically in this bundle.
- Mermaid (alternative) you can paste into docs:  
  ```mermaid
  flowchart LR
    FE[Frontend (React/Vue)] -->|REST/JSON| API[FastAPI Backend]
    API --> DB[(SQLite/Postgres)]
    subgraph API Layer
      E[Employees]:::svc --> DB
      L[Leaves]:::svc --> DB
    end
    classDef svc fill:#eef,stroke:#99f,stroke-width:1px;
  ```

## Potential Improvements
- AuthN/AuthZ (JWT, roles)
- Leave types, pro-rating, carry-forward, accrual
- Holiday calendar and region-aware business days
- Audit logs & notifications (email/Slack)
- Admin dashboard + CSV import/export
- CI/CD with tests
- Observability (metrics, tracing)

---

**Author:** MVP generated on 2025-08-17

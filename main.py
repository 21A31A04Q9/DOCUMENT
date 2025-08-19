from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, List
from datetime import date, timedelta, datetime
from sqlalchemy import create_engine, Column, Integer, String, Date, Enum, ForeignKey, DateTime, and_, or_
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
import enum
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./leave_mvp.sqlite3")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

DEFAULT_ANNUAL_BALANCE = 20  # days per calendar year for MVP

class LeaveStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"

class Employee(Base):
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    department = Column(String, nullable=False)
    joining_date = Column(Date, nullable=False)
    # For MVP, store a simple annual balance (resets Jan 1). Could be expanded to accruals later.
    annual_balance = Column(Integer, default=DEFAULT_ANNUAL_BALANCE, nullable=False)

    leaves = relationship("Leave", back_populates="employee", cascade="all, delete-orphan")

class Leave(Base):
    __tablename__ = "leaves"
    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    days = Column(Integer, nullable=False)  # computed as working days for MVP (excl Sat/Sun)
    reason = Column(String, nullable=True)
    status = Column(Enum(LeaveStatus), default=LeaveStatus.pending, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    employee = relationship("Employee", back_populates="leaves")

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Leave Management System MVP", version="0.1.0")

# ------------ Schemas ------------

class EmployeeCreate(BaseModel):
    name: str
    email: EmailStr
    department: str
    joining_date: date

class EmployeeOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    department: str
    joining_date: date
    annual_balance: int

    class Config:
        from_attributes = True

class LeaveApply(BaseModel):
    employee_id: int
    start_date: date
    end_date: date
    reason: Optional[str] = None

    @field_validator("end_date")
    @classmethod
    def validate_dates(cls, v, info):
        # pydantic v2 validator: ensure end_date >= start_date (checked later with start_date present)
        return v

class LeaveAction(BaseModel):
    approved: bool
    # Optional override to enforce manager-specified days (rare; for half-days etc.)
    days_override: Optional[int] = None

class LeaveOut(BaseModel):
    id: int
    employee_id: int
    start_date: date
    end_date: date
    days: int
    reason: Optional[str]
    status: LeaveStatus

    class Config:
        from_attributes = True

class BalanceOut(BaseModel):
    employee_id: int
    available_days: int
    used_days: int
    annual_allocation: int
    year: int

# ------------ Helpers ------------

def working_days(start: date, end: date) -> int:
    """Count working days (Mon-Fri) inclusive. Excludes Sat/Sun. No holiday calendar for MVP."""
    if end < start:
        raise ValueError("end_date cannot be before start_date")
    day_count = 0
    cur = start
    while cur <= end:
        if cur.weekday() < 5:  # 0=Mon..4=Fri
            day_count += 1
        cur += timedelta(days=1)
    return day_count

def get_year_bounds(d: date):
    return date(d.year, 1, 1), date(d.year, 12, 31)

def compute_used_days(sess, emp_id: int, year: int) -> int:
    """Sum approved leave days in the given calendar year for the employee."""
    y_start, y_end = date(year, 1, 1), date(year, 12, 31)
    leaves = sess.query(Leave).filter(
        Leave.employee_id == emp_id,
        Leave.status == LeaveStatus.approved,
        or_(
            and_(Leave.start_date >= y_start, Leave.start_date <= y_end),
            and_(Leave.end_date >= y_start, Leave.end_date <= y_end),
            and_(Leave.start_date <= y_start, Leave.end_date >= y_end),
        )
    ).all()

    used = 0
    for lv in leaves:
        # Clip to year
        s = max(lv.start_date, y_start)
        e = min(lv.end_date, y_end)
        used += working_days(s, e)
    return used

def has_overlap(sess, emp_id: int, start: date, end: date) -> bool:
    """Check overlap with existing PENDING or APPROVED leaves."""
    q = sess.query(Leave).filter(
        Leave.employee_id == emp_id,
        Leave.status.in_([LeaveStatus.pending, LeaveStatus.approved]),
        or_(
            and_(Leave.start_date <= end, Leave.end_date >= start),  # ranges intersect
        )
    )
    return sess.query(q.exists()).scalar()

# ------------ Endpoints ------------

@app.post("/employees", response_model=EmployeeOut, status_code=201)
def add_employee(payload: EmployeeCreate):
    sess = SessionLocal()
    try:
        # Simple dedupe by email
        existing = sess.query(Employee).filter_by(email=str(payload.email).lower()).first()
        if existing:
            raise HTTPException(status_code=409, detail="Employee with this email already exists.")
        emp = Employee(
            name=payload.name.strip(),
            email=str(payload.email).lower(),
            department=payload.department.strip(),
            joining_date=payload.joining_date,
            annual_balance=DEFAULT_ANNUAL_BALANCE,
        )
        if emp.joining_date > date.today():
            # Allowed; but they can't apply leave before joining. Just store.
            pass
        sess.add(emp)
        sess.commit()
        sess.refresh(emp)
        return emp
    finally:
        sess.close()

@app.get("/employees/{employee_id}/balance", response_model=BalanceOut)
def get_balance(employee_id: int, year: Optional[int] = None):
    sess = SessionLocal()
    try:
        emp = sess.query(Employee).filter_by(id=employee_id).first()
        if not emp:
            raise HTTPException(status_code=404, detail="Employee not found.")
        year = year or date.today().year
        used = compute_used_days(sess, emp.id, year)
        allocation = emp.annual_balance if emp.joining_date.year <= year else 0
        available = max(allocation - used, 0)
        return BalanceOut(
            employee_id=emp.id,
            available_days=available,
            used_days=used,
            annual_allocation=allocation,
            year=year,
        )
    finally:
        sess.close()

@app.post("/leaves/apply", response_model=LeaveOut, status_code=201)
def apply_leave(payload: LeaveApply):
    sess = SessionLocal()
    try:
        emp = sess.query(Employee).filter_by(id=payload.employee_id).first()
        if not emp:
            raise HTTPException(status_code=404, detail="Employee not found.")

        if payload.end_date < payload.start_date:
            raise HTTPException(status_code=400, detail="Invalid dates: end_date before start_date.")

        if payload.start_date < emp.joining_date:
            raise HTTPException(status_code=400, detail="Cannot apply for leave before joining date.")

        if has_overlap(sess, emp.id, payload.start_date, payload.end_date):
            raise HTTPException(status_code=409, detail="Overlapping with existing pending/approved leave.")

        days = working_days(payload.start_date, payload.end_date)
        if days <= 0:
            raise HTTPException(status_code=400, detail="No working days in the selected range.")

        # Check balance in the year(s). For MVP, restrict to same calendar year.
        if payload.start_date.year != payload.end_date.year:
            raise HTTPException(status_code=400, detail="For MVP, leave cannot span multiple calendar years.")

        used = compute_used_days(sess, emp.id, payload.start_date.year)
        available = max(emp.annual_balance - used, 0)
        if days > available:
            raise HTTPException(status_code=400, detail=f"Requested {days} days exceeds available balance {available}.")

        leave = Leave(
            employee_id=emp.id,
            start_date=payload.start_date,
            end_date=payload.end_date,
            days=days,
            reason=payload.reason,
            status=LeaveStatus.pending
        )
        sess.add(leave)
        sess.commit()
        sess.refresh(leave)
        return leave
    finally:
        sess.close()

@app.post("/leaves/{leave_id}/decision", response_model=LeaveOut)
def decide_leave(leave_id: int, action: LeaveAction):
    sess = SessionLocal()
    try:
        leave = sess.query(Leave).filter_by(id=leave_id).first()
        if not leave:
            raise HTTPException(status_code=404, detail="Leave request not found.")
        if leave.status != LeaveStatus.pending:
            raise HTTPException(status_code=409, detail=f"Leave already {leave.status}.")

        emp = sess.query(Employee).filter_by(id=leave.employee_id).first()
        if not emp:
            raise HTTPException(status_code=404, detail="Employee not found.")

        if action.approved:
            # Re-check overlap and balance at approval time
            if has_overlap(sess, emp.id, leave.start_date, leave.end_date):
                # Allow overlap with itself by temporarily excluding current leave
                # Simple approach: check overlapping OTHER leaves
                other = sess.query(Leave).filter(
                    Leave.employee_id == emp.id,
                    Leave.id != leave.id,
                    Leave.status.in_([LeaveStatus.pending, LeaveStatus.approved]),
                    or_(and_(Leave.start_date <= leave.end_date, Leave.end_date >= leave.start_date))
                ).first()
                if other:
                    raise HTTPException(status_code=409, detail="Overlaps another leave at approval time.")

            days = action.days_override if action.days_override is not None else leave.days
            if days <= 0:
                raise HTTPException(status_code=400, detail="days_override must be positive.")

            used = compute_used_days(sess, emp.id, leave.start_date.year)
            available = max(emp.annual_balance - used, 0)
            if days > available:
                raise HTTPException(status_code=400, detail=f"Approval exceeds available balance ({available}).")

            leave.days = days
            leave.status = LeaveStatus.approved
        else:
            leave.status = LeaveStatus.rejected

        sess.add(leave)
        sess.commit()
        sess.refresh(leave)
        return leave
    finally:
        sess.close()

@app.get("/leaves", response_model=List[LeaveOut])
def list_leaves(employee_id: Optional[int] = None, status: Optional[LeaveStatus] = None):
    sess = SessionLocal()
    try:
        q = sess.query(Leave)
        if employee_id is not None:
            q = q.filter(Leave.employee_id == employee_id)
        if status is not None:
            q = q.filter(Leave.status == status)
        q = q.order_by(Leave.created_at.desc())
        return q.all()
    finally:
        sess.close()

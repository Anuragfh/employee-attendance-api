"""
Employee Attendance & Analytics API

Run with: uvicorn app.main:app --port 8000
Env vars: MONGO_URI, MONGO_DB
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from decimal import Decimal, ROUND_HALF_UP

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

load_dotenv()

# MongoDB setup
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True)
db = client[os.getenv("MONGO_DB", "attendance_db")]

# IST timezone (UTC+5:30)
IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# Create indexes on startup
@app.on_event("startup")
def create_indexes():
    # Employees indexes
    db.employees.create_index([("emp_code", ASCENDING)], unique=True)
    db.employees.create_index([("department", ASCENDING), ("emp_code", ASCENDING)])
    db.employees.create_index([("joined_on", ASCENDING)])
    
    # Attendance logs indexes
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True)
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)])
    db.attendance_logs.create_index([("status", ASCENDING)])
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("punch_in", ASCENDING)])
    db.attendance_logs.create_index([("date", ASCENDING)])


# Utility functions for datetime conversion
def epoch_ms_to_datetime(epoch_ms: int) -> datetime:
    """Convert epoch milliseconds to UTC datetime (truncated to seconds)"""
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    return dt.replace(microsecond=0)  # Rule: truncate to seconds


def datetime_to_epoch_ms(dt: datetime) -> int:
    """Convert datetime to epoch milliseconds"""
    if dt is None:
        return None
    return int(dt.timestamp() * 1000)


def to_ist(dt: datetime) -> datetime:
    """Convert UTC datetime to IST"""
    return dt.astimezone(IST)


# R8: ROUND_HALF_UP implementation (not Python's default banker's rounding)
def half_up_round(value: float, decimals: int) -> float:
    """Round using ROUND_HALF_UP as specified in R8"""
    if value is None:
        return None
    d = Decimal(str(value))
    if decimals == 2:
        return float(d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    elif decimals == 4:
        return float(d.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP))
    else:
        factor = Decimal(10) ** decimals
        return float((d * factor).quantize(Decimal('1'), rounding=ROUND_HALF_UP) / factor)


# R1: Calculate attendance date considering IST timezone and overnight shifts
def get_attendance_date(punch_in_utc: datetime, shift_end: str) -> str:
    """
    For overnight shifts, if punch time is before shift_end, 
    it belongs to previous day's shift
    """
    punch_in_ist = to_ist(punch_in_utc)
    punch_time = punch_in_ist.time()
    
    end_h, end_m = map(int, shift_end.split(":"))
    shift_end_time = datetime.min.replace(hour=end_h, minute=end_m).time()
    
    # Check if this is overnight shift continuation
    if punch_time < shift_end_time:
        # Belongs to previous day
        return (punch_in_ist.date() - timedelta(days=1)).isoformat()
    else:
        return punch_in_ist.date().isoformat()


def is_overnight_shift(shift_start: str, shift_end: str) -> bool:
    """Check if shift is overnight"""
    start_h, start_m = map(int, shift_start.split(":"))
    end_h, end_m = map(int, shift_end.split(":"))
    start_mins = start_h * 60 + start_m
    end_mins = end_h * 60 + end_m
    return end_mins <= start_mins


# R2: Late calculation with 10-minute grace period
def compute_late_minutes(punch_in_utc: datetime, shift_start: str, attendance_date: str) -> int:
    """
    Grace period is 10 minutes. Late only if STRICTLY more than 10:00 after shift_start.
    Returns floor of minutes elapsed since shift_start.
    """
    # Build shift start time in IST for the attendance date
    year, month, day = map(int, attendance_date.split("-"))
    start_h, start_m = map(int, shift_start.split(":"))
    
    shift_start_ist = datetime(year, month, day, start_h, start_m, 0, tzinfo=IST)
    punch_in_ist = to_ist(punch_in_utc)
    
    diff_seconds = (punch_in_ist - shift_start_ist).total_seconds()
    diff_minutes = diff_seconds / 60
    
    # Late only if strictly > 10 minutes
    if diff_minutes > 10:
        return int(diff_minutes)
    return 0


# R4: Work hours calculation
def compute_work_hours(punch_in_utc: datetime, punch_out_utc: datetime) -> float:
    """Calculate work hours with ROUND_HALF_UP to 2 decimals"""
    seconds = (punch_out_utc - punch_in_utc).total_seconds()
    hours = seconds / 3600
    return half_up_round(hours, 2)


# R3: Overtime calculation (minimum 30 minutes)
def compute_overtime_minutes(punch_out_utc: datetime, shift_end: str, attendance_date: str, shift_start: str) -> int:
    """
    Overtime counted only if >= 30 minutes after shift_end.
    For overnight shifts, shift_end is next day.
    """
    year, month, day = map(int, attendance_date.split("-"))
    end_h, end_m = map(int, shift_end.split(":"))
    
    # For overnight shifts, shift end is on next day
    if is_overnight_shift(shift_start, shift_end):
        shift_end_date = datetime(year, month, day) + timedelta(days=1)
    else:
        shift_end_date = datetime(year, month, day)
    
    shift_end_ist = shift_end_date.replace(hour=end_h, minute=end_m, second=0, tzinfo=IST)
    punch_out_ist = to_ist(punch_out_utc)
    
    diff_seconds = (punch_out_ist - shift_end_ist).total_seconds()
    diff_minutes = int(diff_seconds / 60)
    
    # Only count if >= 30 minutes overtime
    if diff_minutes >= 30:
        return diff_minutes
    return 0


# R5: Half day check
def compute_half_day(work_hours: float) -> bool:
    """Half day if work_hours < 4.50"""
    if work_hours is None:
        return False
    return work_hours < 4.50


def is_presence_status(status: str) -> bool:
    """R6: Check if status counts as present"""
    return status in ["PRESENT", "WFH", "ON_DUTY"]


# R7: Calculate working days in month
def get_working_days_in_month(year: int, month: int, joined_on: str) -> int:
    """Count Mon-Fri from joined_on if applicable"""
    from calendar import monthrange
    import datetime as dt
    
    last_day = monthrange(year, month)[1]
    start_date = dt.date(year, month, 1)
    
    # Adjust for joined_on if in this month
    joined_date = dt.date.fromisoformat(joined_on)
    if joined_date.year == year and joined_date.month == month:
        start_date = max(start_date, joined_date)
    
    count = 0
    current = start_date
    end_date = dt.date(year, month, last_day)
    
    while current <= end_date:
        if current.weekday() < 5:  # Mon=0, Fri=4
            count += 1
        current += dt.timedelta(days=1)
    
    return count


# Pydantic models for request/response
class EmployeeIn(BaseModel):
    emp_code: str = Field(..., pattern=r'^EMP\d{4,6}$')
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., max_length=120, pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    department: str = Field(..., min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    shift_end: str = Field(default="18:30", pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    joined_on: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}$')


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(None, ge=100000000000, le=4102444800000)
    status: str = Field(default="PRESENT")
    
    @field_validator('status')
    @classmethod
    def validate_status(cls, v):
        if v not in ["PRESENT", "WFH", "ON_DUTY"]:
            raise ValueError("status must be PRESENT, WFH, or ON_DUTY for punch-in")
        return v


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(None, ge=100000000000, le=4102444800000)


class RegularizeIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = Field(None, ge=100000000000, le=4102444800000)
    punch_out: Optional[int] = Field(None, ge=100000000000, le=4102444800000)
    reason: str = Field(..., min_length=5, max_length=200)
    regularized_by: str = Field(..., min_length=1, max_length=50)
    
    @field_validator('status')
    @classmethod
    def validate_status(cls, v):
        if v and v not in ["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]:
            raise ValueError("Invalid status")
        return v


# Helper to format attendance record for response
def format_attendance_record(doc: dict) -> dict:
    """Convert MongoDB doc to API response"""
    result = {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": datetime_to_epoch_ms(doc.get("punch_in")),
        "punch_out": datetime_to_epoch_ms(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": doc.get("half_day", False),
        "history": []
    }
    
    # Format history - convert datetime fields
    for h in doc.get("history", []):
        entry = {
            "at": datetime_to_epoch_ms(h["at"]),
            "by": h["by"],
            "reason": h["reason"],
            "changes": {}
        }
        for field, change in h["changes"].items():
            if field in ["punch_in", "punch_out"]:
                # These are datetime fields
                entry["changes"][field] = {
                    "from": datetime_to_epoch_ms(change["from"]) if change["from"] else None,
                    "to": datetime_to_epoch_ms(change["to"]) if change["to"] else None
                }
            else:
                entry["changes"][field] = change
        result["history"].append(entry)
    
    return result


def format_employee(doc: dict) -> dict:
    """Convert employee doc to API response"""
    doc = doc.copy()
    doc.pop("_id", None)
    if "created_at" in doc:
        doc["created_at"] = datetime_to_epoch_ms(doc["created_at"])
    return doc


# API Endpoints

@app.get("/health")
def health():
    """Health check with MongoDB ping"""
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "Database unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    """Create employee"""
    if body.shift_start == body.shift_end:
        raise HTTPException(422, "shift_start must differ from shift_end")
    
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc).replace(microsecond=0)
    
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    
    return format_employee(doc)


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """List employees with optional filtering"""
    query = {}
    if department:
        query["department"] = department
    
    total = db.employees.count_documents(query)
    skip = (page - 1) * page_size
    
    items = list(db.employees.find(query, {"_id": 0})
                 .sort("emp_code", ASCENDING)
                 .skip(skip)
                 .limit(page_size))
    
    # Convert created_at to epoch ms
    for item in items:
        if "created_at" in item:
            item["created_at"] = datetime_to_epoch_ms(item["created_at"])
    
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    """Record a punch-in"""
    # Find employee
    emp = db.employees.find_one({"emp_code": body.emp_code}, {"_id": 0})
    if not emp:
        raise HTTPException(404, "Employee not found")
    
    # Determine punch time
    if body.punched_at:
        punch_in_utc = epoch_ms_to_datetime(body.punched_at)
    else:
        punch_in_utc = datetime.now(timezone.utc).replace(microsecond=0)
    
    # Compute attendance date per R1
    attendance_date = get_attendance_date(punch_in_utc, emp["shift_end"])
    
    # Check for existing record
    existing = db.attendance_logs.find_one({"emp_code": body.emp_code, "date": attendance_date})
    if existing:
        raise HTTPException(409, "Already punched in for this date")
    
    # Compute late_minutes per R2
    late_mins = compute_late_minutes(punch_in_utc, emp["shift_start"], attendance_date)
    
    # Create document
    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": punch_in_utc,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_mins,
        "overtime_minutes": 0,
        "half_day": False,
        "history": []
    }
    
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        # Race condition - another request created it
        raise HTTPException(409, "Already punched in for this date")
    
    return format_attendance_record(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    """Record a punch-out"""
    # Find employee
    emp = db.employees.find_one({"emp_code": body.emp_code}, {"_id": 0})
    if not emp:
        raise HTTPException(404, "Employee not found")
    
    # Determine punch-out time
    if body.punched_at:
        punch_out_utc = epoch_ms_to_datetime(body.punched_at)
    else:
        punch_out_utc = datetime.now(timezone.utc).replace(microsecond=0)
    
    # Find most recent open record (punch_in <= punched_at, punch_out is null)
    record = db.attendance_logs.find_one({
        "emp_code": body.emp_code,
        "punch_in": {"$lte": punch_out_utc},
        "punch_out": None
    }, sort=[("punch_in", DESCENDING)])
    
    if not record:
        raise HTTPException(404, "No open punch-in found")
    
    punch_in_utc = record["punch_in"]
    
    # Validate: punch_out > punch_in and within 24 hours
    diff_seconds = (punch_out_utc - punch_in_utc).total_seconds()
    if diff_seconds <= 0:
        raise HTTPException(422, "punch_out must be after punch_in")
    if diff_seconds > 24 * 3600:
        raise HTTPException(422, "punch_out must be within 24 hours of punch_in")
    
    # Compute derived fields
    work_hours = compute_work_hours(punch_in_utc, punch_out_utc)
    overtime_mins = compute_overtime_minutes(punch_out_utc, emp["shift_end"], record["date"], emp["shift_start"])
    half_day = compute_half_day(work_hours)
    
    # Update with optimistic locking (check punch_out still null)
    result = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            "punch_out": None  # Ensure still open
        },
        {
            "$set": {
                "punch_out": punch_out_utc,
                "work_hours": work_hours,
                "overtime_minutes": overtime_mins,
                "half_day": half_day
            }
        },
        return_document=True
    )
    
    if not result:
        raise HTTPException(409, "Record already punched out")
    
    return format_attendance_record(result)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """List attendance records with filters"""
    query = {}
    
    if emp_code:
        query["emp_code"] = emp_code
    
    if date_from or date_to:
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from
        if date_to:
            query["date"]["$lte"] = date_to
        # Validate date range
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "date_from must be <= date_to")
    
    if status:
        query["status"] = status
    
    total = db.attendance_logs.count_documents(query)
    skip = (page - 1) * page_size
    
    items = list(db.attendance_logs.find(query, {"_id": 0})
                 .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
                 .skip(skip)
                 .limit(page_size))
    
    formatted_items = [format_attendance_record(doc) for doc in items]
    
    return {
        "items": formatted_items,
        "total": total,
        "page": page,
        "page_size": page_size
    }


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    """Manually correct (regularize) an attendance record"""
    # Find employee
    emp = db.employees.find_one({"emp_code": emp_code}, {"_id": 0})
    if not emp:
        raise HTTPException(404, "Employee not found")
    
    # Find record
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(404, "Attendance record not found")
    
    # Build updates and track changes
    updates = {}
    changes = {}
    
    # Handle status change
    new_status = body.status if body.status else record["status"]
    if body.status and body.status != record["status"]:
        changes["status"] = {"from": record["status"], "to": body.status}
        updates["status"] = body.status
    
    # Handle punch times
    new_punch_in = record.get("punch_in")
    new_punch_out = record.get("punch_out")
    
    if body.punch_in is not None:
        new_punch_in = epoch_ms_to_datetime(body.punch_in)
        if new_punch_in != record.get("punch_in"):
            changes["punch_in"] = {
                "from": record.get("punch_in"),
                "to": new_punch_in
            }
            updates["punch_in"] = new_punch_in
    
    if body.punch_out is not None:
        new_punch_out = epoch_ms_to_datetime(body.punch_out)
        if new_punch_out != record.get("punch_out"):
            changes["punch_out"] = {
                "from": record.get("punch_out"),
                "to": new_punch_out
            }
            updates["punch_out"] = new_punch_out
    
    # Validation rules
    if new_status in ["ABSENT", "LEAVE"]:
        # Must clear punch times
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "Cannot supply punch times with ABSENT or LEAVE status")
        new_punch_in = None
        new_punch_out = None
        if record.get("punch_in") is not None:
            changes["punch_in"] = {"from": record.get("punch_in"), "to": None}
            updates["punch_in"] = None
        if record.get("punch_out") is not None:
            changes["punch_out"] = {"from": record.get("punch_out"), "to": None}
            updates["punch_out"] = None
    else:
        # Presence status requires punch_in
        if new_punch_in is None:
            raise HTTPException(422, "Presence status requires a punch_in")
    
    # Validate punch times if both present
    if new_punch_in and new_punch_out:
        diff_seconds = (new_punch_out - new_punch_in).total_seconds()
        if diff_seconds <= 0:
            raise HTTPException(422, "punch_out must be after punch_in")
        if diff_seconds > 24 * 3600:
            raise HTTPException(422, "punch_out must be within 24 hours of punch_in")
    
    # Validate punch_in stays on attendance date (R1)
    if new_punch_in:
        computed_date = get_attendance_date(new_punch_in, emp["shift_end"])
        if computed_date != date:
            raise HTTPException(422, "punch_in must stay on the record's attendance date")
    
    # Check if anything changed
    if not changes:
        raise HTTPException(422, "No changes detected")
    
    # Recompute derived fields
    if new_status in ["ABSENT", "LEAVE"]:
        new_work_hours = None
        new_late_minutes = 0
        new_overtime_minutes = 0
        new_half_day = False
    else:
        # Compute late_minutes if punch_in exists
        if new_punch_in:
            new_late_minutes = compute_late_minutes(new_punch_in, emp["shift_start"], date)
        else:
            new_late_minutes = 0
        
        # Compute work hours, overtime, half_day if both punches exist
        if new_punch_in and new_punch_out:
            new_work_hours = compute_work_hours(new_punch_in, new_punch_out)
            new_overtime_minutes = compute_overtime_minutes(new_punch_out, emp["shift_end"], date, emp["shift_start"])
            new_half_day = compute_half_day(new_work_hours)
        else:
            new_work_hours = None
            new_overtime_minutes = 0
            new_half_day = False
    
    # Track changes in derived fields
    if new_late_minutes != record.get("late_minutes", 0):
        changes["late_minutes"] = {"from": record.get("late_minutes", 0), "to": new_late_minutes}
    
    if new_work_hours != record.get("work_hours"):
        changes["work_hours"] = {"from": record.get("work_hours"), "to": new_work_hours}
    
    if new_overtime_minutes != record.get("overtime_minutes", 0):
        changes["overtime_minutes"] = {"from": record.get("overtime_minutes", 0), "to": new_overtime_minutes}
    
    if new_half_day != record.get("half_day", False):
        changes["half_day"] = {"from": record.get("half_day", False), "to": new_half_day}
    
    updates.update({
        "work_hours": new_work_hours,
        "late_minutes": new_late_minutes,
        "overtime_minutes": new_overtime_minutes,
        "half_day": new_half_day
    })
    
    # Create history entry
    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes
    }
    
    # Update with optimistic locking on history length
    result = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"]
        },
        {
            "$set": updates,
            "$push": {"history": history_entry}
        },
        return_document=True
    )
    
    if not result:
        raise HTTPException(409, "Concurrent modification detected")
    
    return format_attendance_record(result)


# --------------------------------------------------------------------------- #
# Analytics Endpoints
# --------------------------------------------------------------------------- #
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$')):
    """Monthly attendance summary for one employee"""
    # Find employee
    emp = db.employees.find_one({"emp_code": emp_code}, {"_id": 0})
    if not emp:
        raise HTTPException(404, "Employee not found")
    
    # Parse month
    year, mon = map(int, month.split("-"))
    
    # Calculate working days per R7
    working_days = get_working_days_in_month(year, mon, emp["joined_on"])
    
    # Get all records for this employee in this month
    from calendar import monthrange
    last_day = monthrange(year, mon)[1]
    date_from = f"{year}-{mon:02d}-01"
    date_to = f"{year}-{mon:02d}-{last_day:02d}"
    
    records = list(db.attendance_logs.find({
        "emp_code": emp_code,
        "date": {"$gte": date_from, "$lte": date_to}
    }))
    
    # Calculate metrics
    present_days = 0.0
    leave_days = 0
    late_count = 0
    total_late_minutes = 0
    total_overtime_minutes = 0
    
    for rec in records:
        # Check if it's a weekday
        rec_date = datetime.fromisoformat(rec["date"]).date()
        is_weekday = rec_date.weekday() < 5
        
        # Count present days (only weekdays per R7)
        if is_presence_status(rec["status"]) and is_weekday:
            if rec.get("half_day", False):
                present_days += 0.5
            else:
                present_days += 1.0
        
        # Count leave days
        if rec["status"] == "LEAVE":
            leave_days += 1
        
        # Late count and total (all days including weekends)
        if rec.get("late_minutes", 0) > 0:
            late_count += 1
            total_late_minutes += rec.get("late_minutes", 0)
        
        # Overtime total (all days)
        total_overtime_minutes += rec.get("overtime_minutes", 0)
    
    # Calculate attendance percentage
    if working_days > 0:
        attendance_pct = half_up_round((present_days / working_days) * 100, 2)
    else:
        attendance_pct = None
    
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": half_up_round(present_days, 2),
        "leave_days": leave_days,
        "late_count": late_count,
        "total_late_minutes": total_late_minutes,
        "total_overtime_minutes": total_overtime_minutes,
        "attendance_pct": attendance_pct
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
    department: Optional[str] = None
):
    """Department summary for a month using aggregation pipeline"""
    year, mon = map(int, month.split("-"))
    from calendar import monthrange
    last_day = monthrange(year, mon)[1]
    date_from = f"{year}-{mon:02d}-01"
    date_to = f"{year}-{mon:02d}-{last_day:02d}"
    end_date_obj = datetime(year, mon, last_day).date()
    
    # Build employee filter
    emp_match = {}
    if department:
        emp_match["department"] = department
    
    # Add joined_on filter (R9)
    emp_match["joined_on"] = {"$lte": date_to}
    
    # Aggregation pipeline
    pipeline = [
        # Start with employees
        {"$match": emp_match},
        # Lookup attendance logs
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"emp_code": "$emp_code"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$emp_code", "$$emp_code"]},
                    "date": {"$gte": date_from, "$lte": date_to}
                }}
            ],
            "as": "logs"
        }},
        # Unwind logs (keep employees with no logs)
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        # Group by department
        {"$group": {
            "_id": "$department",
            "headcount": {"$addToSet": "$emp_code"},
            "present_days": {
                "$sum": {
                    "$cond": [
                        {
                            "$and": [
                                {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                # Check if weekday
                                {"$lte": [
                                    {"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}},
                                    6  # Mon-Fri are 2-6 in MongoDB
                                ]},
                                {"$gte": [
                                    {"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}},
                                    2
                                ]}
                            ]
                        },
                        {"$cond": [{"$eq": ["$logs.half_day", True]}, 0.5, 1.0]},
                        0
                    ]
                }
            },
            "work_hours_sum": {
                "$sum": {
                    "$cond": [
                        {
                            "$and": [
                                {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                {"$ne": ["$logs.work_hours", None]}
                            ]
                        },
                        "$logs.work_hours",
                        0
                    ]
                }
            },
            "work_hours_count": {
                "$sum": {
                    "$cond": [
                        {
                            "$and": [
                                {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                {"$ne": ["$logs.work_hours", None]}
                            ]
                        },
                        1,
                        0
                    ]
                }
            },
            "late_count": {
                "$sum": {
                    "$cond": [{"$gt": ["$logs.late_minutes", 0]}, 1, 0]
                }
            },
            "total_late_minutes": {
                "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
            },
            "leave_count": {
                "$sum": {
                    "$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]
                }
            },
            "on_duty_count": {
                "$sum": {
                    "$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]
                }
            }
        }},
        # Project final shape
        {"$project": {
            "department": "$_id",
            "headcount": {"$size": "$headcount"},
            "present_days": 1,
            "avg_work_hours": {
                "$cond": [
                    {"$gt": ["$work_hours_count", 0]},
                    {"$divide": ["$work_hours_sum", "$work_hours_count"]},
                    None
                ]
            },
            "late_count": 1,
            "total_late_minutes": 1,
            "leave_count": 1,
            "on_duty_count": 1
        }},
        # Filter out departments with 0 headcount
        {"$match": {"headcount": {"$gt": 0}}},
        # Sort by department
        {"$sort": {"department": 1}}
    ]
    
    results = list(db.employees.aggregate(pipeline))
    
    # Format results
    items = []
    for r in results:
        items.append({
            "department": r["department"],
            "headcount": r["headcount"],
            "present_days": half_up_round(r["present_days"], 2),
            "avg_work_hours": half_up_round(r["avg_work_hours"], 2) if r["avg_work_hours"] else None,
            "late_count": r["late_count"],
            "total_late_minutes": r["total_late_minutes"],
            "leave_count": r["leave_count"],
            "on_duty_count": r["on_duty_count"]
        })
    
    return {"month": month, "items": items}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None
):
    """Late-comers leaderboard with standard competition ranking"""
    year, mon = map(int, month.split("-"))
    from calendar import monthrange
    last_day = monthrange(year, mon)[1]
    date_from = f"{year}-{mon:02d}-01"
    date_to = f"{year}-{mon:02d}-{last_day:02d}"
    
    # Build pipeline
    pipeline = [
        # Match records in month
        {"$match": {
            "date": {"$gte": date_from, "$lte": date_to}
        }},
        # Group by employee
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {
                "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
            }
        }},
        # Filter only those with late minutes > 0
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        # Lookup employee details
        {"$lookup": {
            "from": "employees",
            "localField": "_id",
            "foreignField": "emp_code",
            "as": "emp"
        }},
        {"$unwind": "$emp"},
        # Filter by department if specified
        *([{"$match": {"emp.department": department}}] if department else []),
        # Sort for ranking: total_late_minutes desc, emp_code asc
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        # Project fields
        {"$project": {
            "emp_code": "$_id",
            "name": "$emp.name",
            "department": "$emp.department",
            "total_late_minutes": 1,
            "late_count": 1
        }}
    ]
    
    results = list(db.attendance_logs.aggregate(pipeline))
    
    # Apply standard competition ranking
    items = []
    current_rank = 0
    prev_minutes = None
    same_rank_count = 0
    
    for i, r in enumerate(results):
        if r["total_late_minutes"] != prev_minutes:
            current_rank = i + 1
            prev_minutes = r["total_late_minutes"]
        
        # Include all employees with rank <= limit
        if current_rank <= limit:
            items.append({
                "rank": current_rank,
                "emp_code": r["emp_code"],
                "name": r["name"],
                "department": r["department"],
                "total_late_minutes": r["total_late_minutes"],
                "late_count": r["late_count"]
            })
        else:
            break
    
    return {"month": month, "items": items}


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from", pattern=r'^\d{4}-\d{2}-\d{2}$'),
    to_date: str = Query(..., alias="to", pattern=r'^\d{4}-\d{2}-\d{2}$')
):
    """Daily attendance trend for a department with gap-filling"""
    import datetime as dt
    
    # Validate date range
    from_dt = dt.date.fromisoformat(from_date)
    to_dt = dt.date.fromisoformat(to_date)
    
    if to_dt < from_dt:
        raise HTTPException(422, "to must be >= from")
    
    days_diff = (to_dt - from_dt).days + 1
    if days_diff > 92:
        raise HTTPException(422, "Date range must not exceed 92 days")
    
    # Check if department exists
    dept_exists = db.employees.find_one({"department": department})
    if not dept_exists:
        raise HTTPException(404, "Department not found")
    
    # Use aggregation with $densify (MongoDB 5.1+)
    pipeline = [
        # Start with a single doc to anchor the densify
        {"$limit": 1},
        # Create date range
        {"$project": {"_id": 0, "date": from_date}},
        # Densify to create all dates in range
        {"$densify": {
            "field": "date",
            "range": {
                "bounds": [from_date, to_date],
                "step": 1,
                "unit": "day"
            }
        }},
        # For each date, lookup headcount
        {"$lookup": {
            "from": "employees",
            "let": {"current_date": "$date"},
            "pipeline": [
                {"$match": {
                    "department": department,
                    "$expr": {"$lte": ["$joined_on", "$$current_date"]}
                }},
                {"$count": "count"}
            ],
            "as": "headcount_data"
        }},
        # Lookup attendance for this date and department
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"current_date": "$date"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$date", "$$current_date"]}
                }},
                {"$lookup": {
                    "from": "employees",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "emp"
                }},
                {"$unwind": "$emp"},
                {"$match": {"emp.department": department}}
            ],
            "as": "logs"
        }},
        # Calculate metrics
        {"$project": {
            "date": 1,
            "is_working_day": {
                "$and": [
                    {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 2]},
                    {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 6]}
                ]
            },
            "headcount": {
                "$ifNull": [
                    {"$arrayElemAt": ["$headcount_data.count", 0]},
                    0
                ]
            },
            "present_count": {
                "$sum": {
                    "$map": {
                        "input": "$logs",
                        "as": "log",
                        "in": {
                            "$cond": [
                                {"$in": ["$$log.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                {"$cond": [{"$eq": ["$$log.half_day", True]}, 0.5, 1.0]},
                                0
                            ]
                        }
                    }
                }
            },
            "late_count": {
                "$size": {
                    "$filter": {
                        "input": "$logs",
                        "as": "log",
                        "cond": {"$gt": ["$$log.late_minutes", 0]}
                    }
                }
            },
            "logs": 1,
            "headcount_data": 1
        }},
        # Sort by date
        {"$sort": {"date": 1}}
    ]
    
    try:
        results = list(db.employees.aggregate(pipeline, allowDiskUse=True))
    except Exception as e:
        # Fallback if $densify not available
        # Manual gap filling
        results = []
        current_date = from_dt
        while current_date <= to_dt:
            date_str = current_date.isoformat()
            
            # Get headcount
            headcount = db.employees.count_documents({
                "department": department,
                "joined_on": {"$lte": date_str}
            })
            
            # Get logs for this date
            logs = list(db.attendance_logs.aggregate([
                {"$match": {"date": date_str}},
                {"$lookup": {
                    "from": "employees",
                    "localField": "emp_code",
                    "foreignField": "emp_code",
                    "as": "emp"
                }},
                {"$unwind": "$emp"},
                {"$match": {"emp.department": department}}
            ]))
            
            present_count = sum(
                0.5 if log.get("half_day") else 1.0
                for log in logs
                if is_presence_status(log["status"])
            )
            
            late_count = sum(1 for log in logs if log.get("late_minutes", 0) > 0)
            
            is_working_day = current_date.weekday() < 5
            
            results.append({
                "date": date_str,
                "is_working_day": is_working_day,
                "headcount": headcount,
                "present_count": present_count,
                "late_count": late_count,
                "logs": logs
            })
            
            current_date += dt.timedelta(days=1)
    
    # Calculate attendance_rate and moving average
    items = []
    rates = []  # Store non-null rates for moving average
    
    for i, r in enumerate(results):
        # Calculate attendance rate per spec
        if r["is_working_day"] and r["headcount"] > 0:
            rate = half_up_round(r["present_count"] / r["headcount"], 4)
        else:
            rate = None
        
        # Track non-null rates
        if rate is not None:
            rates.append(rate)
        else:
            rates.append(None)
        
        # Calculate 7-day moving average
        # Window includes current and up to 6 preceding rows within range
        window_start = max(0, i - 6)
        window_rates = [rates[j] for j in range(window_start, i + 1) if rates[j] is not None]
        
        if window_rates:
            moving_avg = half_up_round(sum(window_rates) / len(window_rates), 4)
        else:
            moving_avg = None
        
        items.append({
            "date": r["date"],
            "is_working_day": r["is_working_day"],
            "headcount": r["headcount"],
            "present_count": half_up_round(r["present_count"], 2),
            "late_count": r["late_count"],
            "attendance_rate": rate,
            "moving_avg_7d": moving_avg
        })
    
    return {"department": department, "items": items}


# --------------------------------------------------------------------------- #
# Admin Endpoints
# --------------------------------------------------------------------------- #
@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: Optional[int] = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """Return MongoDB explain for an endpoint's main query"""
    
    if endpoint == "attendance_list":
        # Build same query as list_attendance
        query = {}
        if emp_code:
            query["emp_code"] = emp_code
        if date_from or date_to:
            query["date"] = {}
            if date_from:
                query["date"]["$gte"] = date_from
            if date_to:
                query["date"]["$lte"] = date_to
        if status:
            query["status"] = status
        
        skip = (page - 1) * page_size
        cursor = db.attendance_logs.find(query).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip(skip).limit(page_size)
        explain = db.command("explain", {
            "find": "attendance_logs",
            "filter": query,
            "sort": {"date": -1, "emp_code": 1},
            "skip": skip,
            "limit": page_size
        }, verbosity="executionStats")
        
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain
        }
    
    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month required")
        
        year, mon = map(int, month.split("-"))
        from calendar import monthrange
        last_day = monthrange(year, mon)[1]
        date_from_calc = f"{year}-{mon:02d}-01"
        date_to_calc = f"{year}-{mon:02d}-{last_day:02d}"
        
        query = {
            "emp_code": emp_code,
            "date": {"$gte": date_from_calc, "$lte": date_to_calc}
        }
        
        explain = db.command("explain", {
            "find": "attendance_logs",
            "filter": query
        }, verbosity="executionStats")
        
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain
        }
    
    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month required")
        
        year, mon = map(int, month.split("-"))
        from calendar import monthrange
        last_day = monthrange(year, mon)[1]
        date_from_calc = f"{year}-{mon:02d}-01"
        date_to_calc = f"{year}-{mon:02d}-{last_day:02d}"
        
        emp_match = {}
        if department:
            emp_match["department"] = department
        emp_match["joined_on"] = {"$lte": date_to_calc}
        
        # Explain the aggregation on employees collection
        pipeline = [
            {"$match": emp_match},
            {"$lookup": {
                "from": "attendance_logs",
                "let": {"emp_code": "$emp_code"},
                "pipeline": [
                    {"$match": {
                        "$expr": {"$eq": ["$emp_code", "$$emp_code"]},
                        "date": {"$gte": date_from_calc, "$lte": date_to_calc}
                    }}
                ],
                "as": "logs"
            }}
        ]
        
        explain = db.command("explain", {
            "aggregate": "employees",
            "pipeline": pipeline,
            "cursor": {}
        }, verbosity="executionStats")
        
        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain
        }
    
    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422, "month required")
        
        year, mon = map(int, month.split("-"))
        from calendar import monthrange
        last_day = monthrange(year, mon)[1]
        date_from_calc = f"{year}-{mon:02d}-01"
        date_to_calc = f"{year}-{mon:02d}-{last_day:02d}"
        
        pipeline = [
            {"$match": {
                "date": {"$gte": date_from_calc, "$lte": date_to_calc}
            }},
            {"$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"}
            }},
            {"$match": {"total_late_minutes": {"$gt": 0}}}
        ]
        
        explain = db.command("explain", {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {}
        }, verbosity="executionStats")
        
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain
        }
    
    elif endpoint == "department_trend":
        if not department or not from_date or not to_date:
            raise HTTPException(422, "department, from, and to required")
        
        # Explain attendance logs lookup
        query = {"date": {"$gte": from_date, "$lte": to_date}}
        
        explain = db.command("explain", {
            "find": "attendance_logs",
            "filter": query
        }, verbosity="executionStats")
        
        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain
        }
    
    else:
        raise HTTPException(422, "Invalid endpoint")

# Employee Attendance & Analytics API

A FastAPI-based attendance management system with real-time tracking, manual corrections with audit trails, and MongoDB-powered analytics.

## Features

- **Employee Management**: Create and list employees with customizable shifts
- **Attendance Tracking**: 
  - Punch-in/out with automatic late and overtime calculations
  - Support for overnight shifts (22:00-06:00 type schedules)
  - Race-condition-safe concurrent operations
- **Regularization**: Manual corrections with complete audit trail
- **Analytics**:
  - Employee monthly summaries (attendance %, late count, overtime)
  - Department-wise statistics with headcount
  - Late-comers leaderboard with competition ranking
  - Daily attendance trends with 7-day moving average
- **Performance**: Optimized for 100k+ records with strategic indexes

## Tech Stack

- **Backend**: Python 3.11+, FastAPI
- **Database**: MongoDB 6.0+
- **Validation**: Pydantic v2
- **Timezone**: IST (UTC+5:30) with proper UTC handling

## Quick Start

### Prerequisites

- Python 3.11 or higher
- MongoDB 6.0+ (Docker or MongoDB Atlas)
- Git

### Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/employee-attendance-api.git
cd employee-attendance-api

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows:
.venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### MongoDB Setup

**Option A: Docker (Recommended)**
```bash
docker run -d -p 27017:27017 --name attendance-mongo mongo:7
```

**Option B: MongoDB Atlas (Cloud)**
1. Create free cluster at [MongoDB Atlas](https://www.mongodb.com/cloud/atlas)
2. Get connection string from dashboard
3. Use in configuration below

### Configuration

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your settings
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

### Load Sample Data

```bash
python sample_seed.py
```

Expected output:
```
loaded 6 documents into 'attendance_db.employees'
loaded 9 documents into 'attendance_db.attendance_logs'
```

### Run the API

```bash
uvicorn app.main:app --port 8000 --reload
```

The API will be available at: **http://localhost:8000**

API Documentation (Swagger): **http://localhost:8000/docs**

## API Endpoints

### System
- `GET /health` - Health check with MongoDB ping

### Employees
- `POST /employees` - Create new employee
- `GET /employees` - List employees (with filters and pagination)

### Attendance
- `POST /attendance/punch-in` - Record punch-in
- `POST /attendance/punch-out` - Record punch-out
- `GET /attendance` - List attendance records (with filters)
- `PATCH /attendance/{emp_code}/{date}` - Regularize (correct) attendance

### Analytics
- `GET /analytics/employees/{emp_code}/monthly` - Monthly summary for employee
- `GET /analytics/departments/summary` - Department-wise monthly summary
- `GET /analytics/leaderboard/late` - Late-comers ranking
- `GET /analytics/departments/{department}/trend` - Daily attendance trend

### Admin
- `GET /admin/explain/{endpoint}` - MongoDB query performance analysis

## Usage Examples

### Create Employee
```bash
curl -X POST http://localhost:8000/employees \
  -H "Content-Type: application/json" \
  -d '{
    "emp_code": "EMP0001",
    "name": "John Doe",
    "email": "john@example.com",
    "department": "Engineering",
    "shift_start": "09:30",
    "shift_end": "18:30",
    "joined_on": "2026-01-01"
  }'
```

### Punch In
```bash
curl -X POST http://localhost:8000/attendance/punch-in \
  -H "Content-Type: application/json" \
  -d '{
    "emp_code": "EMP0001",
    "status": "PRESENT"
  }'
```

### Get Monthly Analytics
```bash
curl "http://localhost:8000/analytics/employees/EMP0001/monthly?month=2026-07"
```

### Get Department Summary
```bash
curl "http://localhost:8000/analytics/departments/summary?month=2026-07"
```

## Business Rules

The API implements the following key business rules:

- **R1**: IST timezone handling with overnight shift support
- **R2**: Late calculation with 10-minute grace period
- **R3**: Overtime counted only if ≥30 minutes
- **R4**: Work hours with ROUND_HALF_UP (not banker's rounding)
- **R5**: Half-day detection (< 4.5 hours)
- **R6**: Presence status definition (PRESENT, WFH, ON_DUTY)
- **R7**: Working days calculation (Mon-Fri, from join date)
- **R8**: Proper decimal rounding (2 decimals for most values, 4 for rates)
- **R9**: Headcount includes employees with zero attendance logs
- **R10**: Pagination (page starts at 1, max 100 per page)

## Architecture Decisions

### Database Indexes
8 strategic indexes created for optimal performance:
- Unique index on `emp_code` (prevents duplicates)
- Compound unique index on `(emp_code, date)` (prevents duplicate punches)
- Query-optimized indexes for filtering and sorting
- Analytics-supporting indexes for aggregation pipelines

### Concurrency Handling
- Database-level unique constraints prevent race conditions
- No check-then-insert patterns
- DuplicateKeyError handling for atomic operations

### Analytics Design
- MongoDB aggregation pipelines (not Python loops)
- Reads stored derived values (doesn't recompute)
- Aggregations start from `employees` collection to ensure accurate headcount

See [DECISIONS.md](DECISIONS.md) for detailed architectural decisions.

## Code Review

This implementation fixes 22 defects found in the original starter code, including:
- Race conditions in employee creation and punch-in
- Timezone handling bugs (naive datetimes)
- Incorrect pagination calculations
- Memory issues (loading entire collections)
- Missing validation and error handling

See [REVIEW.md](REVIEW.md) for complete analysis.

## Project Structure

```
.
├── app/
│   ├── __init__.py
│   └── main.py              # Complete API implementation
├── sample_data/
│   ├── employees.json       # Sample employee data
│   └── attendance_logs.json # Sample attendance data
├── .env.example             # Environment configuration template
├── .gitignore              # Git exclusions
├── DECISIONS.md            # Architecture decisions
├── README.md               # This file
├── REVIEW.md               # Code review analysis
├── requirements.txt        # Python dependencies
└── sample_seed.py          # Database seeding script
```

## Testing

### Interactive Testing
Visit **http://localhost:8000/docs** for Swagger UI where you can test all endpoints interactively.

### Command Line Testing
```bash
# Health check
curl http://localhost:8000/health

# List employees
curl http://localhost:8000/employees

# Monthly analytics
curl "http://localhost:8000/analytics/employees/EMP0001/monthly?month=2026-07"
```

## Performance Considerations

- **Indexes**: 8 optimized indexes prevent collection scans
- **Aggregation Pipelines**: Server-side processing for analytics
- **Cursor-based Pagination**: Efficient for large datasets
- **Unique Constraints**: Prevent duplicates at database level
- **Optimistic Locking**: Safe concurrent updates

## Development

### Run with Auto-reload
```bash
uvicorn app.main:app --port 8000 --reload
```

### Code Style
- Type hints for better IDE support
- Clear function names and comments
- Separation of concerns (helpers, models, endpoints)
- Consistent error handling

## Troubleshooting

### "ModuleNotFoundError"
Make sure virtual environment is activated:
```bash
# Windows
.venv\Scripts\activate

# Mac/Linux
source .venv/bin/activate
```

### "ServerSelectionTimeoutError"
MongoDB is not running. Start MongoDB:
```bash
# Docker
docker start attendance-mongo

# Or start new container
docker run -d -p 27017:27017 --name attendance-mongo mongo:7
```

### "Port 8000 already in use"
Either stop the other process or use a different port:
```bash
uvicorn app.main:app --port 8001
```

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `MONGO_URI` | MongoDB connection string | `mongodb://localhost:27017` |
| `MONGO_DB` | Database name | `attendance_db` |

## License

This project was created as part of a technical assessment.

## Author

Created for the HROne engineering assignment.

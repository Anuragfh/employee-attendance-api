# Employee Attendance & Analytics API

A FastAPI-based attendance tracking system with punch-in/out, manual corrections with audit trails, and MongoDB-powered analytics.

## Setup

### Prerequisites
- Python 3.11+
- MongoDB 6.0+ (local Docker or MongoDB Atlas)

### Installation

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and set MONGO_URI and MONGO_DB
```

### MongoDB Setup

**Using Docker:**
```bash
docker run -d -p 27017:27017 --name mongo mongo:7
```

**Using MongoDB Atlas:**
- Create free cluster at https://www.mongodb.com/cloud/atlas
- Use connection string in .env

### Seed Sample Data

```bash
python sample_seed.py
```

### Run the API

```bash
uvicorn app.main:app --port 8000
```

API Documentation: http://localhost:8000/docs

## API Endpoints

- `GET /health` - Health check
- `POST /employees` - Create employee
- `GET /employees` - List employees
- `POST /attendance/punch-in` - Record punch-in
- `POST /attendance/punch-out` - Record punch-out
- `GET /attendance` - List attendance records
- `PATCH /attendance/{emp_code}/{date}` - Regularize attendance
- `GET /analytics/employees/{emp_code}/monthly` - Monthly summary
- `GET /analytics/departments/summary` - Department summary
- `GET /analytics/leaderboard/late` - Late-comers leaderboard
- `GET /analytics/departments/{department}/trend` - Daily trend
- `GET /admin/explain/{endpoint}` - Query performance explain

## Key Features

- **Business Rules**: All R1-R10 implemented (IST timezone, late calculation, overtime, work hours, half-day detection, etc.)
- **Concurrency Safe**: Handles race conditions with unique indexes
- **Performance**: Optimized indexes for 100k+ records
- **Analytics**: MongoDB aggregation pipelines (no in-memory processing)
- **Audit Trail**: Full history tracking for manual corrections

## Example Usage

```bash
# Create employee
curl -X POST http://localhost:8000/employees \
  -H "Content-Type: application/json" \
  -d '{"emp_code":"EMP0001","name":"John Doe","email":"john@example.com","department":"Engineering","joined_on":"2026-01-01"}'

# Punch in
curl -X POST http://localhost:8000/attendance/punch-in \
  -H "Content-Type: application/json" \
  -d '{"emp_code":"EMP0001"}'

# View monthly analytics
curl "http://localhost:8000/analytics/employees/EMP0001/monthly?month=2026-07"
```

## Project Structure

```
.
├── app/
│   └── main.py              # Complete API implementation
├── sample_data/             # Sample JSON data
│   ├── employees.json
│   └── attendance_logs.json
├── sample_seed.py           # Database seeding script
├── requirements.txt         # Python dependencies
├── REVIEW.md               # Code review of starter
├── DECISIONS.md            # Architecture decisions
└── README.md               # This file
```

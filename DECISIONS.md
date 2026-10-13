# DECISIONS.md

## 1. Indexes

**Created:**
- `employees.emp_code` (unique) - Enforces uniqueness constraint and serves employee lookups by code
- `employees.(department, emp_code)` - Supports filtered employee listing, sorted results
- `employees.joined_on` - Used in analytics for headcount calculations (R9) filtering by join date
- `attendance_logs.(emp_code, date)` (unique) - Prevents duplicate punch-ins and enables fast record lookups
- `attendance_logs.(date DESC, emp_code ASC)` - Primary index for attendance listing endpoint's sort order
- `attendance_logs.status` - Filters by status in list endpoint
- `attendance_logs.(emp_code, punch_in)` - Finds most recent open punch for punch-out operation
- `attendance_logs.date` - Supports date range queries in all analytics aggregations

**Rejected:**
- Separate indexes on `emp_code` and `date` individually in attendance_logs - the compound index covers both single-field queries and the combined query
- Index on `late_minutes` - Not selective enough; analytics do range scans anyway
- Index on `department` in attendance_logs - Department comes from employees via lookup, not directly queried

## 2. Punch-in Race

Two identical punch-in requests arrive simultaneously for the same employee and attendance date:

1. Both requests pass the employee existence check
2. Both compute the same attendance_date from punch_in time and shift_end (per R1)
3. Both prepare identical document structures
4. First request's `insert_one()` succeeds, creating the document
5. Second request's `insert_one()` hits the unique index on (emp_code, date) and raises `DuplicateKeyError`
6. The except block catches this and raises `HTTPException(409, "Already punched in for this date")`
7. First request returns 201 with the created record; second returns 409

The unique compound index guarantees atomicity at the database level. No check-then-insert race is possible.

## 3. Ties

When multiple employees tie at the cutoff rank (e.g., three employees all have 45 late_minutes and rank 10 is the limit):

My leaderboard uses **standard competition ranking** - tied employees share the same rank, and I return *all* employees whose rank ≤ limit. So if employees at rank 10 are tied, all tied members appear in the response, possibly exceeding the limit count but not the rank cutoff.

This matches the spec: "return every employee whose rank <= limit." Sorting within a rank is by emp_code ascending, providing stable, deterministic ordering.

## 4. Headcount

The department summary aggregation starts with the `employees` collection (not logs), matches employees by department and joined_on ≤ month_end (R9), then uses `$lookup` to join their logs. The `$unwind` uses `preserveNullAndEmptyArrays: true` so employees with zero logs remain in the pipeline.

In the `$group` stage, headcount is `$addToSet` on emp_code, counting unique employees regardless of log presence. This ensures every employee who existed in that month is counted, even if they have no attendance records.

## 5. One Thing to Change at 100x Scale

**Sharding on (emp_code, date)**: With 10M+ attendance records, I'd shard the attendance_logs collection by a hashed or range key combining emp_code and date. This distributes writes (punches) and makes employee-specific and date-range queries routable to specific shards.

I'd also pre-aggregate monthly summaries into a separate materialized collection updated on punch-out and corrections, avoiding repeated scans for the analytics endpoints. The trend endpoint would benefit from a daily rollup table per department, pre-computed overnight.

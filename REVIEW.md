# REVIEW.md

## Defects Found in Starter Code

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_late_minutes` | Uses `punch_in.replace()` which fails for overnight shifts and doesn't account for attendance date correctly per R1 | Punch-in at 02:00 IST (overnight shift) would calculate late_minutes against 09:30 on the wrong day | Rewrote to build shift_start datetime from attendance_date parameter, properly handling timezone |
| 2 | `compute_overtime` | Uses `datetime.fromisoformat(date_str)` creating naive datetime, mixing with IST-aware datetime; doesn't handle overnight shifts where shift_end is next day | Timezone-aware comparison fails or gives wrong results; overnight shift overtime computed incorrectly | Properly construct shift_end_ist with correct date (next day for overnight) and IST timezone |
| 3 | `create_employee` | Doesn't validate that `shift_start != shift_end` as required by spec | API accepts invalid employee with identical shift times, violating contract | Added validation to raise HTTPException(422) when shift_start == shift_end |
| 4 | `create_employee` | Uses `datetime.now()` without timezone, creating naive datetime | Inconsistent datetime handling, potential timezone bugs | Changed to `datetime.now(timezone.utc).replace(microsecond=0)` |
| 5 | `create_employee` | Checks existence with `find_one` then inserts - race condition allowing duplicate emp_codes | Two concurrent requests can both pass the check and insert, violating uniqueness | Used try/except DuplicateKeyError with unique index to handle race atomically |
| 6 | `list_employees` | Calculates skip as `page * page_size` instead of `(page-1) * page_size` | Page 1 would skip first 20 items, returning wrong results | Fixed skip calculation to `(page-1) * page_size` |
| 7 | `list_employees` | Counts total from unfiltered collection instead of filtered query | Returns wrong total when department filter is applied | Changed to `count_documents(query)` with same filter |
| 8 | `list_employees` | Doesn't convert `created_at` datetime to epoch milliseconds | Returns Python datetime object instead of integer, violating API contract | Added conversion to epoch ms in response formatting |
| 9 | `punch_in` | Uses `ts.date().isoformat()` which gives UTC date, not attendance date per R1 | Wrong attendance date for punches near midnight, especially overnight shifts | Replaced with `get_attendance_date()` function implementing R1 correctly |
| 10 | `punch_in` | Missing employee existence check before accessing `emp["shift_start"]` | Crashes with KeyError if employee not found instead of returning 404 | Added check that raises HTTPException(404) when employee doesn't exist |
| 11 | `punch_in` | Check-then-insert pattern doesn't prevent race condition | Two simultaneous punch-ins can both succeed | Wrapped insert in try/except to catch DuplicateKeyError from unique index |
| 12 | `punch_in` | Uses naive datetime from `datetime.now()` | Inconsistent timezone handling | Changed to use UTC-aware datetime |
| 13 | `punch_in` | Returns `doc["id"] = str(res.inserted_id)` but API spec says _id should never appear | API returns internal MongoDB id field, violating contract | Removed id field, only return defined fields via format function |
| 14 | `list_attendance` | Loads all matching docs into memory with `list()` then sorts in Python | Fails on large datasets (spec mentions ~100k records); slow and memory-intensive | Changed to use MongoDB sort with indexes and cursor-based pagination |
| 15 | `list_attendance` | Doesn't validate `date_from <= date_to` | Confusing results when range is backwards | Added validation raising HTTPException(422) for invalid range |
| 16 | `list_attendance` | Returns `doc["id"]` field not in spec | Violates API contract | Use format function that excludes _id and formats datetimes properly |
| 17 | `list_attendance` | Doesn't convert datetime fields to epoch milliseconds | Returns Python datetime objects instead of integers | Created format_attendance_record() to properly convert all fields |
| 18 | Missing | No punch-out endpoint implemented | Can't complete attendance records | Implemented full punch-out with work_hours, overtime, half_day calculations per R3-R5 |
| 19 | Missing | No regularization endpoint | Can't correct attendance records or build audit trail | Implemented PATCH with history tracking and derived field recomputation |
| 20 | Missing | No analytics endpoints | Assignment incomplete | Implemented all 4 analytics endpoints with aggregation pipelines |
| 21 | Missing | No admin/explain endpoint | Can't verify query performance | Implemented explain endpoint for all main queries |
| 22 | Missing | No indexes created at startup | All queries do collection scans, failing performance requirements | Added startup event to create all necessary indexes idempotently |

## Items Examined But Not Defects

- **Import statements**: All necessary imports are present and correct in the starter.
- **FastAPI app initialization**: Properly configured with title and version.
- **MongoDB client connection**: Correctly reads from environment variables with sensible defaults.
- **IST timezone definition**: Correctly defined as UTC+5:30.
- **PydanticBaseModel usage**: EmployeeIn and PunchInIn models are valid structures, though incomplete validation.
- **dotenv loading**: Proper use of `load_dotenv()` to support local development.
- **HTTP status codes for happy paths**: 201 for create, 200 for reads are correct where implemented.

The main issue with the starter code is not syntax errors but logic bugs, incomplete validation, race conditions, performance issues from lack of indexes, and missing 80% of the required functionality.

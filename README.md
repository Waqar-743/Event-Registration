# EventFlow — Event Registration System API

**Innovaxel Backend Summer Intern Assessment — B0626**

A production-quality REST API for event creation, user registration, and seat management — built with race-condition safety, proper validation, and a real-time frontend dashboard.

---

## Architecture Overview

CLIENT (Browser)
  Hero Section ↔ Dashboard ↔ Modals
       |            |            |
       +---- WebSocket (/ws) ----+
       |            |            |
       v            v            v
REST API (FastAPI)
  POST /events    GET /events    POST /reg    DELETE /reg
       |            |            |            |
       +---- Service Layer (Race-condition prevention) ----+
       |            |            |            |
       v            v            v            v
DATA LAYER (SQLite + WAL)
  events table ↔ registrations table
       |            |
       +---- Indexes: (event_id, status), (event_id, email)
       |
       v
data/events.db (Persistent)

---

## Requirements Compliance

| # | Requirement | Status | Implementation |
|---|-------------|--------|----------------|
| 1 | Create Event (name, seats, date) | Done | POST /api/events with Pydantic validation |
|   | Unique event name | Done | DB UNIQUE constraint + 409 Conflict |
|   | Seats > 0 | Done | Field(gt=0) + DB CHECK constraint |
|   | Future date only | Done | Validator rejects past dates |
| 2 | Register User (name, email, contact, event_id) | Done | POST /api/events/{id}/register |
|   | Cannot register if full | Done | Checked inside EXCLUSIVE transaction |
|   | No duplicate registration | Done | Email-based check inside transaction |
|   | Timestamp stored | Done | registered_at (ISO 8601 UTC) |
| 3 | View Events with available seats | Done | GET /api/events |
|   | Total registrations shown | Done | total_active_registrations field |
|   | Sort by date | Done | sort_by_date query param |
|   | Filter upcoming only | Done | upcoming_only query param |
| 4 | Cancel Registration | Done | DELETE /api/events/{id}/registrations/{email} |
|   | Seat becomes available | Done | Dynamic computation — instant |
|   | Cancelled hidden from active | Done | Only status=active returned |
| Hidden | Race condition prevention | Done | BEGIN EXCLUSIVE transaction |
| Hidden | Duplicate request safety | Done | Idempotent checks in transaction |
| Hidden | Correct seat count always | Done | Computed: total - COUNT(active) |
| Hidden | Proper error messages | Done | Consistent {success, message, detail} |

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| API Framework | FastAPI | Async REST API with automatic OpenAPI docs |
| Database | SQLite + aiosqlite | Embedded, persistent, ACID transactions |
| Concurrency | WAL Mode + BEGIN EXCLUSIVE | Race-condition-safe writes |
| Validation | Pydantic v2 | Request/response schema validation |
| Server | Uvicorn | ASGI server for async Python |
| Frontend | Vanilla HTML/CSS/JS | Zero-dependency, real-time dashboard |
| Real-time | WebSocket | Live seat updates across clients |

---

## Setup & Run

cd event-registration-api
pip install -r requirements.txt
uvicorn main:app --reload
# Frontend:     http://localhost:8000
# API Docs:     http://localhost:8000/api/docs
# WebSocket:    ws://localhost:8000/ws

The SQLite database (data/events.db) is created automatically on first run. No configuration needed.

---

## Project Structure

event-registration-api/
main.py                 # App entry point, WebSocket, lifespan
database.py             # DB connection, schema initialization
models.py               # Pydantic request/response schemas
realtime.py             # WebSocket connection manager + broadcast
requirements.txt        # Python dependencies
routes/
  events.py             # Event CRUD + listing
  registrations.py      # Register, cancel, list registrations
static/
  index.html            # Frontend dashboard
data/
  events.db             # SQLite database (auto-created)

---

## Race Condition Prevention

The critical registration endpoint uses SQLites BEGIN EXCLUSIVE:

await db.execute("BEGIN EXCLUSIVE")
# 1. Verify event exists
# 2. Count active registrations
# 3. Check seat availability
# 4. Check duplicate email
# 5. Insert registration
await db.commit()

Why this works:
- BEGIN EXCLUSIVE immediately acquires a write lock on the database file
- No other connection can read OR write until COMMIT/ROLLBACK
- All checks + insert happen atomically — physically impossible for two concurrent requests to both pass the seat check
- Equivalent to PostgreSQLs SELECT ... FOR UPDATE

Verified: 5 concurrent registrations for 1 seat → exactly 1 succeeds, 4 get 409 Conflict.

---

## Edge Cases Handled

| Scenario | Response |
|----------|----------|
| Duplicate event name | 409 Conflict — Event already exists |
| Event date in past | 422 Unprocessable Entity |
| Total seats <= 0 | 422 Unprocessable Entity |
| Register for non-existent event | 404 Not Found |
| Register when event full | 409 Conflict — No seats available |
| Duplicate email registration | 409 Conflict — Already registered |
| Cancel non-existent registration | 404 Not Found |
| Cancel already-cancelled | 404 Not Found |
| Blank/whitespace names | 422 Unprocessable Entity |
| Invalid email format | 422 Unprocessable Entity |
| Invalid contact number | 422 Unprocessable Entity |
| Concurrent last-seat race | One succeeds, others get 409 |

---

*Built for Innovaxel Backend Summer Intern Assessment — B0626*
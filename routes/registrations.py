"""
Registrations router — handles all registration operations.

The most critical part of this file is the POST /register endpoint.
It uses BEGIN EXCLUSIVE to prevent race conditions (overbooking).

Why BEGIN EXCLUSIVE?
SQLite allows multiple concurrent readers but only one writer at a time.
BEGIN EXCLUSIVE immediately acquires a write lock on the database file.
This means:
  - No other connection can read OR write while the lock is held.
  - We check seat availability AND insert the registration in one atomic
    operation — so it is physically impossible for two concurrent requests
    to both pass the seat check and both insert.
  - The lock is released automatically when we COMMIT or ROLLBACK.

This is the correct solution for SQLite. For PostgreSQL you would use
SELECT ... FOR UPDATE instead.
"""

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, HTTPException
import aiosqlite

from database import get_db
from models import RegisterUserRequest, RegistrationResponse, MessageResponse
from realtime import broadcast_event_update, broadcast_registration_change

router = APIRouter(prefix="/api/events", tags=["Registrations"])


# ─────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────


@router.post(
    "/{event_id}/register", status_code=201, response_model=RegistrationResponse
)
async def register_user(event_id: int, payload: RegisterUserRequest):
    """
    Register a user for an event.

    RACE CONDITION PREVENTION — BEGIN EXCLUSIVE TRANSACTION:
    This endpoint uses a single exclusive transaction to atomically:
      1. Verify the event exists
      2. Count currently active registrations
      3. Compare against total_seats
      4. Check the user hasn't already registered
      5. Insert the registration

    Steps 2-5 happen inside one lock. It is impossible for two simultaneous
    requests to both read "1 seat left", both pass the check, and both insert.
    One will get the lock first; the second will wait, then see 0 seats left.

    Constraints checked (in order):
    - Event must exist                        → 404
    - Event must not be full                  → 409
    - User must not already be registered     → 409
    """
    now_utc = datetime.now(timezone.utc).isoformat()
    db = await get_db()

    try:
        # BEGIN EXCLUSIVE acquires a write lock immediately.
        # No other connection can read or write until we COMMIT or ROLLBACK.
        await db.execute("BEGIN EXCLUSIVE")

        try:
            # Step 1: Verify event exists and fetch total_seats
            async with db.execute(
                "SELECT id, total_seats FROM events WHERE id = ?", (event_id,)
            ) as cursor:
                event_row = await cursor.fetchone()

            if event_row is None:
                await db.rollback()
                raise HTTPException(
                    status_code=404,
                    detail={
                        "success": False,
                        "message": f"Event with ID {event_id} was not found.",
                        "detail": None,
                    },
                )

            total_seats = event_row["total_seats"]

            # Step 2: Count active registrations for this event
            async with db.execute(
                "SELECT COUNT(*) AS cnt FROM registrations WHERE event_id = ? AND status = 'active'",
                (event_id,),
            ) as cursor:
                count_row = await cursor.fetchone()

            active_count = count_row["cnt"] if count_row else 0

            # Step 3: Seat availability check
            if active_count >= total_seats:
                await db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail={
                        "success": False,
                        "message": "This event is full. No seats are available.",
                        "detail": f"All {total_seats} seats have been taken.",
                    },
                )

            # Step 4: Duplicate registration check (by email - more reliable than name)
            async with db.execute(
                """
                SELECT id FROM registrations
                WHERE event_id = ? AND email = ? AND status = 'active'
            """,
                (event_id, payload.email),
            ) as cursor:
                existing = await cursor.fetchone()

            if existing is not None:
                await db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail={
                        "success": False,
                        "message": f"'{payload.email}' is already registered for this event.",
                        "detail": "A user can only register once per event. Cancel first to re-register.",
                    },
                )

            # Step 5: Insert the registration — all checks passed
            async with db.execute(
                """
                INSERT INTO registrations (event_id, user_name, email, contact_number, status, registered_at)
                VALUES (?, ?, ?, ?, 'active', ?)
            """,
                (
                    event_id,
                    payload.user_name,
                    payload.email,
                    payload.contact_number,
                    now_utc,
                ),
            ) as cursor:
                new_id = cursor.lastrowid

            # COMMIT releases the exclusive lock
            await db.commit()

            # Broadcast real-time update after successful registration
            await broadcast_registration_change(event_id, payload.user_name, "register")

        except HTTPException:
            # Re-raise HTTP exceptions (we already rolled back above)
            raise
        except Exception as e:
            await db.rollback()
            raise HTTPException(
                status_code=500,
                detail={
                    "success": False,
                    "message": "An unexpected error occurred during registration.",
                    "detail": str(e),
                },
            )

        # Fetch and return the newly created registration
        async with db.execute(
            "SELECT * FROM registrations WHERE id = ?", (new_id,)
        ) as cursor:
            row = await cursor.fetchone()

        return RegistrationResponse(
            id=row["id"],
            event_id=row["event_id"],
            user_name=row["user_name"],
            email=row["email"],
            contact_number=row["contact_number"],
            status=row["status"],
            registered_at=row["registered_at"],
        )

    finally:
        await db.close()


@router.delete("/{event_id}/registrations/{email}", response_model=MessageResponse)
async def cancel_registration(event_id: int, email: str):
    """
    Cancel a user's active registration for an event (identified by email).

    Soft-delete: the row is NOT removed. status is set to 'cancelled'.
    This preserves the full audit trail of who registered and when.
    The seat is freed automatically because available_seats is computed
    dynamically (total_seats - COUNT(active)) — no counter to update.

    Returns 404 if no active registration exists for this email + event combo.
    """
    db = await get_db()
    try:
        # Check that an active registration exists
        async with db.execute(
            """
            SELECT id, user_name FROM registrations
            WHERE event_id = ? AND email = ? AND status = 'active'
        """,
            (event_id, email),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "success": False,
                    "message": f"No active registration found for '{email}' in event {event_id}.",
                    "detail": "The user may not be registered, or the registration was already cancelled.",
                },
            )

        user_name = row["user_name"]

        # Soft-delete: mark as cancelled
        await db.execute(
            """
            UPDATE registrations
            SET status = 'cancelled'
            WHERE event_id = ? AND email = ? AND status = 'active'
        """,
            (event_id, email),
        )
        await db.commit()

        # Broadcast real-time update after successful cancellation
        await broadcast_registration_change(event_id, email, "cancel")

        return MessageResponse(
            success=True,
            message=f"Registration for '{user_name}' has been successfully cancelled. The seat is now available.",
        )
    finally:
        await db.close()


@router.get("/{event_id}/registrations", response_model=List[RegistrationResponse])
async def list_registrations(event_id: int):
    """
    List all ACTIVE registrations for a given event.
    Cancelled registrations are excluded from this view.
    Returns 404 if the event does not exist.
    """
    db = await get_db()
    try:
        # Verify event exists first
        async with db.execute(
            "SELECT id FROM events WHERE id = ?", (event_id,)
        ) as cursor:
            event_row = await cursor.fetchone()

        if event_row is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "success": False,
                    "message": f"Event with ID {event_id} was not found.",
                    "detail": None,
                },
            )

        async with db.execute(
            """
            SELECT * FROM registrations
            WHERE event_id = ? AND status = 'active'
            ORDER BY registered_at ASC
        """,
            (event_id,),
        ) as cursor:
            rows = await cursor.fetchall()

        return [
            RegistrationResponse(
                id=row["id"],
                event_id=row["event_id"],
                user_name=row["user_name"],
                email=row["email"],
                contact_number=row["contact_number"],
                status=row["status"],
                registered_at=row["registered_at"],
            )
            for row in rows
        ]
    finally:
        await db.close()

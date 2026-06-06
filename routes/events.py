"""
Events router — handles all /api/events endpoints.
Responsible for creating events and listing them with optional filtering/sorting.
"""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
import aiosqlite

from database import get_db
from models import CreateEventRequest, EventResponse
from realtime import broadcast_event_update

router = APIRouter(prefix="/api/events", tags=["Events"])


# ─────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────


async def fetch_event_with_stats(
    db: aiosqlite.Connection, event_id: int
) -> Optional[EventResponse]:
    """
    Fetch a single event row and compute available_seats dynamically.
    available_seats = total_seats - COUNT(active registrations for this event)
    This is always computed at query time — never stored — to ensure accuracy.
    """
    async with db.execute(
        """
        SELECT
            e.id,
            e.name,
            e.total_seats,
            e.event_date,
            e.created_at,
            COUNT(CASE WHEN r.status = 'active' THEN 1 END) AS active_count
        FROM events e
        LEFT JOIN registrations r ON r.event_id = e.id
        WHERE e.id = ?
        GROUP BY e.id
    """,
        (event_id,),
    ) as cursor:
        row = await cursor.fetchone()

    if row is None:
        return None

    active = row["active_count"] or 0
    return EventResponse(
        id=row["id"],
        name=row["name"],
        total_seats=row["total_seats"],
        available_seats=row["total_seats"] - active,
        total_active_registrations=active,
        event_date=row["event_date"],
        created_at=row["created_at"],
    )


# ─────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────


@router.post("", status_code=201, response_model=EventResponse)
async def create_event(payload: CreateEventRequest):
    """
    Create a new event.

    Validation (enforced by Pydantic before this function runs):
    - name: non-empty string
    - total_seats: integer > 0
    - event_date: future datetime

    DB-level check:
    - name must be unique (UNIQUE constraint → caught and re-raised as 409)
    """
    now_utc = datetime.now(timezone.utc).isoformat()
    event_date_str = payload.event_date.isoformat()

    db = await get_db()
    try:
        try:
            async with db.execute(
                """
                INSERT INTO events (name, total_seats, event_date, created_at)
                VALUES (?, ?, ?, ?)
            """,
                (payload.name, payload.total_seats, event_date_str, now_utc),
            ) as cursor:
                new_id = cursor.lastrowid
            await db.commit()
        except aiosqlite.IntegrityError:
            # UNIQUE constraint on name violated
            raise HTTPException(
                status_code=409,
                detail={
                    "success": False,
                    "message": f"An event named '{payload.name}' already exists.",
                    "detail": "Event names must be unique. Choose a different name.",
                },
            )

        event = await fetch_event_with_stats(db, new_id)
        # Broadcast real-time update for new event
        await broadcast_event_update(new_id, event.model_dump())
        return event
    finally:
        await db.close()


@router.get("", response_model=List[EventResponse])
async def list_events(
    upcoming_only: bool = Query(
        False, description="If true, only return events with a future date"
    ),
    sort_by_date: bool = Query(
        True, description="If true, sort events by event_date ascending"
    ),
):
    """
    List all events with live seat availability.

    Query params:
    - upcoming_only: filter to events where event_date > now (UTC)
    - sort_by_date: order by event_date ASC (default true)

    available_seats and total_active_registrations are computed per event
    via a LEFT JOIN + COUNT — no stored counters that could drift.
    """
    now_utc = datetime.now(timezone.utc).isoformat()

    # Build query dynamically based on filter/sort params
    where_clause = "WHERE e.event_date > ?" if upcoming_only else ""
    order_clause = (
        "ORDER BY e.event_date ASC" if sort_by_date else "ORDER BY e.created_at DESC"
    )
    params = (now_utc,) if upcoming_only else ()

    query = f"""
        SELECT
            e.id,
            e.name,
            e.total_seats,
            e.event_date,
            e.created_at,
            COUNT(CASE WHEN r.status = 'active' THEN 1 END) AS active_count
        FROM events e
        LEFT JOIN registrations r ON r.event_id = e.id
        {where_clause}
        GROUP BY e.id
        {order_clause}
    """

    db = await get_db()
    try:
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()

        results = []
        for row in rows:
            active = row["active_count"] or 0
            results.append(
                EventResponse(
                    id=row["id"],
                    name=row["name"],
                    total_seats=row["total_seats"],
                    available_seats=row["total_seats"] - active,
                    total_active_registrations=active,
                    event_date=row["event_date"],
                    created_at=row["created_at"],
                )
            )
        return results
    finally:
        await db.close()


@router.get("/{event_id}", response_model=EventResponse)
async def get_event(event_id: int):
    """
    Get a single event by ID with live seat availability.
    Returns 404 if the event does not exist.
    """
    db = await get_db()
    try:
        event = await fetch_event_with_stats(db, event_id)
        if event is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "success": False,
                    "message": f"Event with ID {event_id} was not found.",
                    "detail": None,
                },
            )
        return event
    finally:
        await db.close()

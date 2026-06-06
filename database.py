"""
Database module — handles SQLite connection, initialization, and schema creation.
Uses aiosqlite for async access. WAL mode is enabled for better concurrent read
performance. Foreign keys are enforced at the connection level.
"""

import aiosqlite
import os

# Database path configurable via env var for production deployments
DB_PATH = os.getenv(
    "DATABASE_PATH", os.path.join(os.path.dirname(__file__), "data", "events.db")
)


async def get_db() -> aiosqlite.Connection:
    """
    Open and configure a new aiosqlite connection.
    WAL mode: allows concurrent reads while a write is in progress.
    Foreign keys: enforced at runtime (SQLite disables them by default).
    Row factory: returns rows as dict-like objects (access by column name).
    """
    db = await aiosqlite.connect(DB_PATH)
    await db.execute("PRAGMA journal_mode=WAL;")
    await db.execute("PRAGMA foreign_keys=ON;")
    db.row_factory = aiosqlite.Row
    return db


async def init_db() -> None:
    """
    Create tables if they don't already exist.
    Called once at application startup via the FastAPI lifespan event.

    Schema design decisions:
    - available_seats is NOT stored — it's always computed dynamically as
      total_seats - COUNT(active registrations). This prevents drift/inconsistency.
    - registrations.status is either 'active' or 'cancelled'. Rows are never
      deleted; soft-delete preserves the audit trail.
    - event name has a UNIQUE constraint enforced at the DB level (not just app level).
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA foreign_keys=ON;")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    UNIQUE NOT NULL,
                total_seats INTEGER NOT NULL CHECK(total_seats > 0),
                event_date  TEXT    NOT NULL,
                created_at  TEXT    NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS registrations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id        INTEGER NOT NULL REFERENCES events(id),
                user_name       TEXT    NOT NULL,
                email           TEXT    NOT NULL,
                contact_number  TEXT,
                status          TEXT    NOT NULL DEFAULT 'active'
                                          CHECK(status IN ('active', 'cancelled')),
                registered_at   TEXT    NOT NULL
            )
        """)

        # Index for fast seat-count queries (called on every registration attempt)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_registrations_event_status
            ON registrations(event_id, status)
        """)

        # Index for duplicate-check queries
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_registrations_event_user
            ON registrations(event_id, user_name, status)
        """)

        await db.commit()

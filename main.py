"""
Main application entry point.
Run with: uvicorn main:app --reload

Architecture overview:
- FastAPI handles routing, validation (via Pydantic), and OpenAPI docs
- SQLite (via aiosqlite) stores all data persistently in data/events.db
- WAL mode + BEGIN EXCLUSIVE transactions prevent race conditions
- Static files served from /static → frontend dashboard at /
- WebSocket for real-time updates on registrations/seat changes
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from database import init_db
from routes.events import router as events_router
from routes.registrations import router as registrations_router
from realtime import manager


# ─────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
PORT = int(os.getenv("PORT", "10000"))
HOST = os.getenv("HOST", "0.0.0.0")

# ─────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("main")


# ─────────────────────────────────────────
# LIFESPAN (startup / shutdown)
# ─────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the database on startup."""
    logger.info("Starting up — initializing database...")
    await init_db()
    logger.info("Database ready.")
    yield
    logger.info("Shutting down.")


# ─────────────────────────────────────────
# APP
# ─────────────────────────────────────────

app = FastAPI(
    title="Event Registration System",
    description=(
        "A REST API for creating events, registering users, and managing "
        "registrations with race-condition-safe seat management."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# CORS — configurable via ALLOWED_ORIGINS env var
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────
# GLOBAL EXCEPTION HANDLERS
# ─────────────────────────────────────────


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(
        status_code=404,
        content={
            "success": False,
            "message": "Resource not found.",
            "detail": str(exc.detail),
        },
    )


@app.exception_handler(422)
async def validation_error_handler(request: Request, exc):
    """
    Pydantic validation failures come through here.
    We reformat them into our consistent error shape.
    """
    errors = exc.errors() if hasattr(exc, "errors") else []
    messages = [f"{' → '.join(str(l) for l in e['loc'])}: {e['msg']}" for e in errors]
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "message": "Validation failed. Check your request data.",
            "detail": "; ".join(messages),
        },
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    logger.error("Unhandled server error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "message": "An internal server error occurred.",
            "detail": None,
        },
    )


# ─────────────────────────────────────────
# ROUTERS
# ─────────────────────────────────────────

app.include_router(events_router)
app.include_router(registrations_router)


# ─────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────


@app.get("/api/health", tags=["Health"])
async def health_check():
    """Quick liveness probe — confirms the server is running."""
    return {"status": "ok", "message": "Event Registration System is running"}


# ─────────────────────────────────────────
# WEBSOCKET — Real-time updates
# ─────────────────────────────────────────


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time event/registration updates."""
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive; client can send ping if needed
            data = await websocket.receive_text()
            # Echo back for ping/pong or ignore
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logging.getLogger("websocket").error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


# ─────────────────────────────────────────
# FRONTEND — serve index.html at root
# ─────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
async def serve_frontend():
    """Serve the frontend dashboard."""
    return FileResponse("static/index.html")

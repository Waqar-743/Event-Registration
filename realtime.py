"""
Real-time WebSocket manager for broadcasting event/registration updates.
Separated to avoid circular imports between main.py and routes.
"""

import json
import logging
from typing import Set

from fastapi import WebSocket, WebSocketDisconnect


class ConnectionManager:
    """Manages active WebSocket connections for real-time updates."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        logging.getLogger("websocket").info(
            f"Client connected. Total: {len(self.active_connections)}"
        )

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        logging.getLogger("websocket").info(
            f"Client disconnected. Total: {len(self.active_connections)}"
        )

    async def broadcast(self, message: dict):
        """Broadcast a message to all connected clients."""
        if not self.active_connections:
            return
        data = json.dumps(message)
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_text(data)
            except Exception:
                disconnected.add(connection)
        for conn in disconnected:
            self.disconnect(conn)


manager = ConnectionManager()


async def broadcast_event_update(event_id: int, event_data: dict = None):
    """Broadcast event update to all connected clients."""
    if event_data is None:
        # Fetch fresh event data
        from database import get_db
        from routes.events import fetch_event_with_stats

        db = await get_db()
        try:
            event = await fetch_event_with_stats(db, event_id)
            if event:
                event_data = event.model_dump()
        finally:
            await db.close()

    if event_data:
        await manager.broadcast({"type": "event_update", "event": event_data})


async def broadcast_registration_change(event_id: int, user_name: str, action: str):
    """Broadcast registration change (register/cancel) to all clients."""
    await manager.broadcast(
        {
            "type": "registration_change",
            "event_id": event_id,
            "user_name": user_name,
            "action": action,  # "register" or "cancel"
        }
    )
    # Also broadcast updated event data
    await broadcast_event_update(event_id)

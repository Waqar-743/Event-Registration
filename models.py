"""
Pydantic v2 models for request validation and response serialization.
Validation happens automatically before any route handler runs — if the
request doesn't match the schema, FastAPI returns a 422 with field-level
error details before our code even executes.
"""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, field_validator, EmailStr


# ─────────────────────────────────────────
# REQUEST SCHEMAS
# ─────────────────────────────────────────


class CreateEventRequest(BaseModel):
    """Payload for POST /api/events"""

    name: str = Field(
        ..., min_length=1, max_length=200, description="Unique event name"
    )
    total_seats: int = Field(..., gt=0, description="Must be greater than 0")
    event_date: datetime = Field(
        ..., description="Must be a future datetime (ISO 8601)"
    )

    @field_validator("event_date")
    @classmethod
    def event_date_must_be_future(cls, v: datetime) -> datetime:
        """
        Reject events scheduled in the past.
        Normalize to UTC if timezone-aware, compare against UTC now.
        """
        now = datetime.now(timezone.utc)
        # Make v timezone-aware for comparison if it isn't already
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        if v <= now:
            raise ValueError("event_date must be in the future")
        return v

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Event name cannot be blank or whitespace only")
        return v.strip()


class RegisterUserRequest(BaseModel):
    """Payload for POST /api/events/{event_id}/register"""

    user_name: str = Field(
        ..., min_length=1, max_length=100, description="Registrant's name"
    )
    email: EmailStr = Field(..., description="Registrant's email")
    contact_number: Optional[str] = Field(
        None, max_length=20, description="Optional contact number"
    )

    @field_validator("user_name")
    @classmethod
    def user_name_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("user_name cannot be blank or whitespace only")
        return v.strip()

    @field_validator("contact_number")
    @classmethod
    def contact_number_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not v:
                return None
            # Allow digits, spaces, +, -, (, )
            import re

            if not re.match(r"^[\d\s+\-()]{7,20}$", v):
                raise ValueError("Invalid contact number format")
        return v


# ─────────────────────────────────────────
# RESPONSE SCHEMAS
# ─────────────────────────────────────────


class EventResponse(BaseModel):
    """Returned for every event — available_seats is always computed, never stored."""

    id: int
    name: str
    total_seats: int
    available_seats: int
    total_active_registrations: int
    event_date: str
    created_at: str


class RegistrationResponse(BaseModel):
    """Returned for every registration record."""

    id: int
    event_id: int
    user_name: str
    email: str
    contact_number: Optional[str]
    status: str
    registered_at: str


class MessageResponse(BaseModel):
    """Generic success/info response."""

    success: bool
    message: str


class ErrorResponse(BaseModel):
    """Consistent error shape returned for all 4xx/5xx responses."""

    success: bool = False
    message: str
    detail: Optional[str] = None

from __future__ import annotations

from typing import Optional
from uuid import UUID, uuid4
from datetime import datetime
from pydantic import BaseModel, Field

class ReservationBase(BaseModel):
    reservation_id: UUID = Field(
        default_factory=uuid4,
        description="Persistent Reservation ID (server-generated).",
        json_schema_extra={"example": "550e8400-e29b-41d4-a716-446655440000"},
    )
    item_id: int = Field(
        ...,
        description="Item ID.",
        json_schema_extra={"example": 422},
    )
    buyer_id: int = Field(
        ...,
        description="User ID of the potential buyer that is making this reservation.",
        json_schema_extra={"example": 2},
    )
    status: str = Field(
        ...,
        description="Status of the Reservation: Active, Inactive. (Default to Active) (server-generated)",
        json_schema_extra={"example": "Active"},
    )
    hold_expires_at: datetime = Field(
        ...,
        description="Timestamp when this reservation hold expires (ISO 8601 format) (server-generated).",
        json_schema_extra={"example": "2025-11-13T21:30:00Z"},
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "reservation_id": "550e8400-e29b-41d4-a716-446655440000",
                    "item_id": 422,
                    "buyer_id": 2,
                    "status": "Active",
                    "hold_expires_at": "2025-11-13T21:30:00Z",
                }
            ]
        }
    }

class ReservationCreate(ReservationBase):
    """Payload for creating a new reservation."""
    pass


class ReservationUpdate(BaseModel):
    """Payload for updating an existing reservation. 
    Only the `status` field can be modified (e.g., mark as Inactive)."""

    status: str = Field(
        ...,
        description="Updated reservation status. Allowed value: 'Inactive'.",
        json_schema_extra={"example": "Inactive"},
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "status": "Inactive"
                }
            ]
        }
    }

class ReservationRead(ReservationBase):
    updated_at: datetime = Field(
        ...,
        description="Time when the reservation was last updated (UTC).",
        json_schema_extra={"example": "2025-11-13T09:20:00Z"},
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "reservation_id": "550e8400-e29b-41d4-a716-446655440000",
                    "item_id": 422,
                    "buyer_id": 2,
                    "status": "Active",
                    "hold_expires_at": "2025-11-16T12:00:00Z",
                    "updated_at": "2025-11-13T09:20:00Z",
                }
            ]
        }
    }

from __future__ import annotations

import os
import socket
from datetime import datetime, timezone, timedelta
import httpx

from typing import Dict, List
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi import Query, Path, Header, Depends
from typing import Optional

from sqlalchemy import text
from database import engine

from models.reservation import ReservationCreate, ReservationRead, ReservationUpdate

port = int(os.environ.get("FASTAPIPORT", 8000))

# -----------------------------------------------------------------------------
# Fake in-memory "databases"
# -----------------------------------------------------------------------------
reservations: Dict[UUID, ReservationRead] = {}

app = FastAPI(
    title="Reservation Service API",
    description="Demo FastAPI app using Pydantic v2 models for Reservation",
    version="0.1.0",
)

# ============================================================
# Health check
# ============================================================
@app.get("/health/db")
def health_check():
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        return {"db_ok": result.scalar() == 1}

# -----------------------------------------------------------------------------
# Config / helpers
# -----------------------------------------------------------------------------
CATALOG_URL = "https://catalog-1003140017553.us-east1.run.app/"  # replace with real URL
HOLD_TTL_HOURS = 72

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def current_user_id() -> int:
    # TODO: extract from JWT (e.g., sub). Stubbed for now.
    return 2

# -----------------------------------------------------------------------------
# Catalog client (composite behavior)
# -----------------------------------------------------------------------------
def catalog_get_item(item_id: int) -> dict:
    # GET /items/{id} → expects ETag header + JSON with status
    try:
        resp = httpx.get(f"{CATALOG_URL}/items/{item_id}", timeout=5.0)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Catalog unreachable: {e}") from e
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Item not found in Catalog")
    if resp.is_error:
        raise HTTPException(status_code=502, detail=f"Catalog error: {resp.text}")
    return {"etag": resp.headers.get("ETag"), "body": resp.json()}

def catalog_set_status(item_id: int, etag: Optional[str], from_status: str, to_status: str):
    # PATCH /items/{id}/status with If-Match and from→to
    headers = {}
    if etag:
        headers["If-Match"] = etag
    payload = {"from": from_status, "to": to_status, "reason": "RESERVATION_FLOW"}
    try:
        resp = httpx.patch(f"{CATALOG_URL}/items/{item_id}/status",
                            headers=headers, json=payload, timeout=5.0)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Catalog unreachable: {e}") from e
    if resp.status_code in (409, 412):
        # Precondition failed / conflict → someone else changed it
        raise HTTPException(status_code=409, detail="Item status changed concurrently")
    if resp.is_error:
        raise HTTPException(status_code=502, detail=f"Catalog error: {resp.text}")
    return resp.json() if resp.content else None

# -----------------------------------------------------------------------------
# Reservations endpoints
# -----------------------------------------------------------------------------
@app.post(
    "/items/{item_id}/reservations",
    response_model=ReservationRead,
    status_code=201,
    summary="Create a reservation (composite calls Catalog to reserve the item)",
)
def create_reservation(
    item_id: int,
    user_id: int = Depends(current_user_id),
    x_item_etag: Optional[str] = Header(None, description="Optional ETag from prior GET /items/{id}"),
):
    # 1) Consult Catalog: verify item & flip Available→Reserved using ETag if available
    cat = catalog_get_item(item_id)
    etag = x_item_etag or cat["etag"]
    item_status = cat["body"].get("status")

    if item_status not in ("available", "reserved"):
        # Already sold/withdrawn
        raise HTTPException(status_code=409, detail=f"Item not reservable (status={item_status})")

    if item_status == "available":
        # Attempt to reserve
        try:
            catalog_set_status(item_id, etag, from_status="available", to_status="reserved")
        except HTTPException as e:
            # If conflict, surface a clean message
            if e.status_code == 409:
                raise HTTPException(status_code=409, detail="Item was reserved by someone else") from e
            raise

    # 2) Create local reservation record (server sets expiry = now + 72h)
    rid = uuid4()
    now = now_utc()
    status="ACTIVE"
    expires = now + timedelta(hours=HOLD_TTL_HOURS)
    

    record = ReservationRead(
        reservation_id=rid,
        item_id=item_id,
        buyer_id=user_id,
        status=status,
        hold_expires_at=expires,
        updated_at=now,
    )
    reservations[rid] = record
    return record

@app.get("/reservations", response_model=List[ReservationRead], summary="List reservations")
def list_reservations(
    reservation_id: Optional[UUID] = Query(None),
    item_id: Optional[int] = Query(None),
    buyer_id: Optional[int] = Query(None),
    status_q: Optional[str] = Query(None, alias="status"),
):
    results = list(reservations.values())
    if reservation_id is not None:
        results = [r for r in results if r.reservation_id == reservation_id]
    if item_id is not None:
        results = [r for r in results if r.item_id == item_id]
    if buyer_id is not None:
        results = [r for r in results if r.buyer_id == buyer_id]
    if status_q is not None:
        results = [r for r in results if r.status == status_q]
    return results

@app.get("/reservations/{reservation_id}", response_model=ReservationRead, summary="Get reservation by ID")
def get_reservation(reservation_id: UUID):
    rec = reservations.get(reservation_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return rec

@app.patch("/reservations/{reservation_id}", response_model=ReservationRead, summary="Update reservation status")
def update_reservation(reservation_id: UUID, update: ReservationUpdate, user_id: int = Depends(current_user_id)):
    rec = reservations.get(reservation_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Reservation not found")

    if rec.status != "ACTIVE":
        raise HTTPException(status_code=409, detail=f"Reservation not Active (current={rec.status})")

    # Optionally, attempt to relist the item in Catalog if it's still Reserved
    try:
        cat = catalog_get_item(rec.item_id)
        if cat["body"].get("status") == "available":
            catalog_set_status(rec.item_id, cat["etag"], from_status="available", to_status="reserved")
        elif cat["body"].get("status") == "reserved":
            catalog_set_status(rec.item_id, cat["etag"], from_status="reserved", to_status="available")
    except HTTPException:
        # If Catalog call fails, we still let user mark Inactive (decouple UX);
        # you may enqueue a retry instead of swallowing.
        pass

    rec.status = "INACTIVE"
    rec.updated_at = now_utc()
    reservations[reservation_id] = rec
    return rec

@app.delete("/reservations/{reservation_id}", status_code=200, summary="Delete reservation (cancel & relist)")
def delete_reservation(reservation_id: UUID, user_id: int = Depends(current_user_id)):
    rec = reservations.get(reservation_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Reservation not found")

    # Best-effort: relist in Catalog if still Reserved
    try:
        cat = catalog_get_item(rec.item_id)
        if cat["body"].get("status") == "reserved":
            catalog_set_status(rec.item_id, cat["etag"], from_status="reserved", to_status="available")
    except HTTPException:
        # Consider logging/enqueue retry
        pass

    del reservations[reservation_id]
    return {"message": f"Reservation {reservation_id} deleted"}

# -----------------------------------------------------------------------------
# Root
# -----------------------------------------------------------------------------
@app.get("/")
def root():
    return {"message": "Welcome to the Reservation Composite Microservice API. See /docs for OpenAPI UI."}

# -----------------------------------------------------------------------------
# Entrypoint for `python main.py`
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
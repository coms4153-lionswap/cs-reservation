from __future__ import annotations

import os
import socket
from datetime import datetime, timezone, timedelta
import httpx


from typing import Dict, List
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi import Query, Path, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

from sqlalchemy import text
from database import engine

from models.reservation import ReservationCreate, ReservationRead, ReservationUpdate

port = int(os.environ.get("PORT", 8000))

# -----------------------------------------------------------------------------
# Fake in-memory "databases"
# -----------------------------------------------------------------------------

app = FastAPI(
    title="Reservation Service API",
    description="Demo FastAPI app using Pydantic v2 models for Reservation",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["ETag", "Location", "Content-Type"],  
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
    """
    Change an item's status from `from_status` → `to_status` using:
      - GET /items/{id}  (via catalog_get_item)
      - PUT /items/{id}  (full resource with updated status)

    If `etag` is provided, we enforce it via If-Match to detect concurrent updates.
    """

    # 1) Fetch current item (full resource + latest ETag)
    current = catalog_get_item(item_id)
    current_etag = current["etag"]
    body = current["body"]

    # 2) Check that the current status matches what the caller expects
    current_status = body.get("status")
    if current_status != from_status:
        raise HTTPException(
            status_code=409,
            detail=f"Item status is '{current_status}', expected '{from_status}'",
        )

    # 3) Optional extra concurrency check: if caller passed an ETag, compare it
    if etag is not None and current_etag is not None and etag != current_etag:
        raise HTTPException(
            status_code=409,
            detail="Item ETag mismatch (possible concurrent modification)",
        )

    # 4) Build full payload with only status changed
    #    (Assumes schema: name, description, price, category, status)
    payload = {
        "name": body["name"],
        "description": body["description"],
        "price": body["price"],
        "category": body["category"],
        "status": to_status,
    }

    headers = {}
    # Use the latest ETag we just saw from GET for If-Match
    if current_etag:
        headers["If-Match"] = current_etag

    # 5) PUT updated item
    try:
        resp = httpx.put(
            f"{CATALOG_URL}/items/{item_id}",
            headers=headers,
            json=payload,
            timeout=5.0,
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Catalog unreachable: {e}") from e

    if resp.status_code in (409, 412):
        # Conflict / precondition failed → someone else updated it between GET and PUT
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

    if item_status not in ("available"):
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
    

    with engine.begin() as conn:
        # Insert row
        insert_stmt = text("""
            INSERT INTO reservations (
                reservation_id, item_id, buyer_id, status, hold_expires_at, updated_at
            )
            VALUES (
                :reservation_id, :item_id, :buyer_id, :status, :hold_expires_at, :updated_at
            )
        """)
        conn.execute(
            insert_stmt,
            {
                "reservation_id": str(rid),
                "item_id": item_id,
                "buyer_id": user_id,
                "status": status,
                "hold_expires_at": expires,
                "updated_at": now,
            },
        )

        # Fetch the row we just inserted
        select_stmt = text("""
            SELECT reservation_id, item_id, buyer_id, status, hold_expires_at, updated_at
            FROM reservations
            WHERE reservation_id = :reservation_id
        """)
        row = conn.execute(
            select_stmt, {"reservation_id": str(rid)}
        ).mappings().first()

    if not row:
        raise HTTPException(status_code=500, detail="Failed to create reservation")

    # 3) Let Pydantic do the parsing — passing a dict is enough
    return ReservationRead(**row)

@app.get("/reservations", response_model=List[ReservationRead], summary="List reservations")
def list_reservations(
    reservation_id: Optional[UUID] = Query(None),
    item_id: Optional[int] = Query(None),
    buyer_id: Optional[int] = Query(None),
    status_q: Optional[str] = Query(None, alias="status"),
):
    query = """
        SELECT reservation_id, item_id, buyer_id, status, hold_expires_at, updated_at
        FROM reservations
        WHERE 1=1
    """
    params = {}

    if reservation_id is not None:
        query += " AND reservation_id = :reservation_id"
        params["reservation_id"] = str(reservation_id)
    if item_id is not None:
        query += " AND item_id = :item_id"
        params["item_id"] = item_id
    if buyer_id is not None:
        query += " AND buyer_id = :buyer_id"
        params["buyer_id"] = buyer_id
    if status_q is not None:
        query += " AND status = :status"
        params["status"] = status_q

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).mappings().all()

    return [ReservationRead(**row) for row in rows]

@app.get("/reservations/{reservation_id}", response_model=ReservationRead, summary="Get reservation by ID")
def get_reservation(reservation_id: UUID):
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT reservation_id, item_id, buyer_id, status, hold_expires_at, updated_at
                FROM reservations
                WHERE reservation_id = :reservation_id
            """),
            {"reservation_id": str(reservation_id)},
        ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return ReservationRead(**row)

@app.patch("/reservations/{reservation_id}", response_model=ReservationRead, summary="Update reservation status")
def update_reservation(
    reservation_id: UUID,
    update: ReservationUpdate,
    user_id: int = Depends(current_user_id),
):
    # 1) Load existing reservation from DB
    with engine.begin() as conn:
        select_stmt = text("""
            SELECT reservation_id, item_id, buyer_id, status, hold_expires_at, updated_at
            FROM reservations
            WHERE reservation_id = :reservation_id
        """)
        row = conn.execute(
            select_stmt,
            {"reservation_id": str(reservation_id)},
        ).mappings().first()

        if not row:
            raise HTTPException(status_code=404, detail="Reservation not found")

        if row["status"] != "ACTIVE":
            raise HTTPException(
                status_code=409,
                detail=f"Reservation not Active (current={row['status']})",
            )

        # 2) Optionally, attempt to relist the item in Catalog if it's still Reserved
        try:
            cat = catalog_get_item(row["item_id"])
            cat_status = cat["body"].get("status")
            if cat_status == "available":
                catalog_set_status(row["item_id"], cat["etag"], from_status="available", to_status="reserved")
            elif cat_status == "reserved":
                catalog_set_status(row["item_id"], cat["etag"], from_status="reserved", to_status="available")
        except HTTPException:
            # If Catalog call fails, we still let user mark Inactive (decouple UX);
            # you may enqueue a retry instead of swallowing.
            pass

        # 3) Update reservation status in DB
        new_status = "INACTIVE"  # keep old behavior, ignore update.status value
        now = now_utc()

        update_stmt = text("""
            UPDATE reservations
            SET status = :status,
                updated_at = :updated_at
            WHERE reservation_id = :reservation_id
        """)
        conn.execute(
            update_stmt,
            {
                "status": new_status,
                "updated_at": now,
                "reservation_id": str(reservation_id),
            },
        )

        # 4) Fetch updated row
        updated_row = conn.execute(
            select_stmt,
            {"reservation_id": str(reservation_id)},
        ).mappings().first()

    if not updated_row:
        raise HTTPException(status_code=500, detail="Failed to update reservation")

    return ReservationRead(**updated_row)

@app.delete("/reservations/{reservation_id}", status_code=200, summary="Delete reservation (cancel & relist)")
def delete_reservation(
    reservation_id: UUID,
    user_id: int = Depends(current_user_id),
):
    with engine.begin() as conn:
        # 1) Load existing reservation
        select_stmt = text("""
            SELECT reservation_id, item_id, buyer_id, status, hold_expires_at, updated_at
            FROM reservations
            WHERE reservation_id = :reservation_id
        """)
        row = conn.execute(
            select_stmt,
            {"reservation_id": str(reservation_id)},
        ).mappings().first()

        if not row:
            raise HTTPException(status_code=404, detail="Reservation not found")

        # 2) Best-effort: relist in Catalog if still Reserved
        try:
            cat = catalog_get_item(row["item_id"])
            cat_status = cat["body"].get("status")
            if cat_status == "reserved":
                catalog_set_status(row["item_id"], cat["etag"], from_status="reserved", to_status="available")
        except HTTPException:
            # Consider logging/enqueue retry
            pass

        # 3) Delete from DB
        delete_stmt = text("""
            DELETE FROM reservations
            WHERE reservation_id = :reservation_id
        """)
        conn.execute(
            delete_stmt,
            {"reservation_id": str(reservation_id)},
        )

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
# Reservation Microservice

Part of **LionSwap** - Columbia University's exclusive student marketplace platform.

## Overview

The Reservation Service manages item reservations in the LionSwap marketplace, allowing buyers to place temporary holds on items before completing transactions. This microservice handles the complete reservation lifecycle including creation, expiration, and cleanup operations.

## Architecture

- **Deployment**: Google Cloud Run (Containerized)
- **Database**: Cloud SQL (MySQL)
- **Authentication**: JWT Bearer tokens
- **Container**: Multi-stage Docker build
- **Scaling**: Automatic via Cloud Run

## Live Deployment

- **Service URL**: https://reservation-service-157498364441.us-east1.run.app/
- **Repository**: https://github.com/coms4153-lionswap/cs-reservation

## Database Schema
```sql
CREATE TABLE reservations (
  reservation_id    CHAR(36) PRIMARY KEY,      -- UUID
  item_id           INT NOT NULL,              -- Foreign Key to items
  buyer_id          INT NOT NULL,              -- Foreign Key to users
  status            VARCHAR(20) NOT NULL,      -- "ACTIVE" or "INACTIVE"
  hold_expires_at   TIMESTAMP NOT NULL,
  updated_at        TIMESTAMP NOT NULL DEFAULT NOW()
);
```

## API Endpoints

### 1. Get Reservations
```http
GET /reservations
```

Retrieves all reservations with optional filtering.

**Query Parameters** (all optional):
- `reservation_id`: Filter by specific reservation
- `item_id`: Filter by item
- `buyer_id`: Filter by buyer
- `status`: Filter by status (`ACTIVE` or `INACTIVE`)

**Response**: `200 OK`
```json
{
  "reservations": [...]
}
```

---

### 2. Get Reservation by ID
```http
GET /reservations/{reservation_id}
```

Retrieves a specific reservation by its ID.

**Response**: `200 OK`
```json
{
  "reservation_id": "uuid",
  "item_id": 123,
  "buyer_id": 456,
  "status": "ACTIVE",
  "hold_expires_at": "2024-01-30T12:00:00Z",
  "updated_at": "2024-01-27T10:00:00Z"
}
```

---

### 3. Create Reservation
```http
POST /items/{item_id}/reservations
```

Creates a new reservation for an item.

**Headers**:
```
Authorization: Bearer <JWT_token>
```

**Process**:
1. Validates JWT token to extract `user_id`
2. Verifies user exists in users database
3. Verifies item exists and status is "available" in items database
4. Calls Catalog microservice to update item status
5. Creates reservation record

**Response**: `201 Created`
```json
{
  "reservation_id": "uuid",
  "item_id": 123,
  "buyer_id": 456,
  "status": "ACTIVE",
  "hold_expires_at": "2024-01-30T12:00:00Z"
}
```

**Sequence Diagram**:
```
User -> Reservation Service: POST /items/{item_id}/reservations
Reservation Service -> Auth: Decode JWT token
Reservation Service -> Users DB: Validate user exists
Reservation Service -> Items DB: Validate item available
Reservation Service -> Catalog Service: PUT /items/{item_id} (update status)
Reservation Service -> Reservations DB: Create reservation
Reservation Service -> User: 201 Created
```

---

### 4. Update Reservation
```http
PATCH /reservations/{reservation_id}
```

Updates a reservation status from `ACTIVE` to `INACTIVE`.

**Headers**:
```
Authorization: Bearer <JWT_token>
```

**Authorization**: Only the buyer who created the reservation can update it (verified via JWT).

**Response**: `200 OK`
```json
{
  "message": "Reservation updated successfully",
  "reservation_id": "uuid",
  "status": "INACTIVE"
}
```

---

### 5. Expire Reservations (Batch Processing)
```http
POST /reservations/expire-batch
```

Asynchronous cleanup job that processes expired reservations concurrently.

**Process**:
1. Finds all `ACTIVE` reservations with `hold_expires_at` in the past
2. Processes reservations concurrently using thread pool (max 4 workers)
3. Each worker:
   - Calls Catalog microservice to update item status
   - Updates reservation status to `INACTIVE`

**Response**: `201 Accepted` (returns immediately)
```json
{
  "message": "Batch expiration job started",
  "expired_count": 5
}
```

**Concurrency**: Multiple threads process different reservations in parallel, completing at different times based on external service response times.

---

### 6. Delete Reservation
```http
DELETE /reservations/{reservation_id}
```

Deletes a reservation record.

**Headers**:
```
Authorization: Bearer <JWT_token>
```

**Authorization**: Only the buyer who created the reservation can delete it (verified via JWT).

**Response**: `200 OK`
```json
{
  "message": "Reservation deleted successfully"
}
```

---

## Security Features

- **JWT Authentication**: All modification endpoints require valid JWT tokens
- **Authorization**: Users can only modify their own reservations
- **Logical Foreign Keys**: Validates references to users and items before creating reservations
- **Service Account**: Cloud SQL Client role for secure database access
- **CORS**: Configured for web UI access

## Infrastructure

- **Docker**: Multi-stage builds for optimized container size
- **Cloud Run**: Serverless container platform with automatic scaling
- **Cloud SQL**: Managed MySQL database with connection pooling
- **Environment Variables**: Secure configuration management
- **Service Mesh**: Integrates with Catalog and Users microservices

## Related Microservices

- **Catalog Service**: Manages item listings and status updates
- **Users Service**: Handles user authentication and profiles
- **Chat Service**: Facilitates buyer-seller communication

## Development

### Prerequisites
- Docker
- Google Cloud SDK
- MySQL client

### Local Setup
```bash
# Clone repository
git clone https://github.com/coms4153-lionswap/cs-reservation

# Set environment variables
export DATABASE_URL="your-connection-string"
export JWT_SECRET="your-secret"

# Build and run
docker build -t reservation-service .
docker run -p 8080:8080 reservation-service
```

### Testing
```bash
# Run unit tests
pytest tests/

# Test API endpoints
curl -X GET https://reservation-service-157498364441.us-east1.run.app/reservations
```

## Contributing

This microservice is part of the LionSwap project for COMS 4153 at Columbia University.

---

**Maintained by**: Yuting
**Course**: COMS 4153 - Cloud Computing
**Institution**: Columbia University

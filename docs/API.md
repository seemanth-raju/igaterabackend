# iGatera API Reference

> **Base URL:** `http://<host>/api`
> **Auth:** Bearer token — include `Authorization: Bearer <access_token>` on every request except `/auth/login` and `/auth/token`.
> **Content-Type:** `application/json` for all requests/responses unless noted.

---

## Table of Contents

1. [Authentication](#1-authentication)
2. [Tenants](#2-tenants)
3. [Devices](#3-devices)
4. [Access Control](#4-access-control)
5. [Access Logs](#5-access-logs)
6. [Device Mappings](#6-device-mappings)
7. [Companies](#7-companies)
8. [Sites](#8-sites)
9. [App Users](#9-app-users)
10. [WebSocket — Real-Time Events](#10-websocket--real-time-events)
11. [Error Responses](#11-error-responses)
12. [Roles & Permissions](#12-roles--permissions)
13. [Tenant Enrollment — Full Flow](#13-tenant-enrollment--full-flow)

---

## 1. Authentication

### POST `/auth/register`
Create a new app user account.

**Request**
```json
{
  "company_id": "uuid",
  "full_name": "Jane Smith",
  "password": "min8chars",
  "role": "staff"
}
```
`role` options: `"super_admin"` | `"company_admin"` | `"staff"`

**Response `200`**
```json
{
  "user_id": "uuid",
  "company_id": "uuid",
  "role": "staff",
  "username": "STF7XK3M",
  "full_name": "Jane Smith",
  "is_active": true,
  "created_at": "2026-02-24T10:00:00"
}
```
> `username` is auto-generated. Save it — this is what you use to log in.

---

### POST `/auth/login`
Log in and receive tokens.

**Request**
```json
{
  "username": "STF7XK3M",
  "password": "yourpassword"
}
```

**Response `200`**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "expires_at": "2026-02-24T11:00:00"
}
```

---

### POST `/auth/token`
OAuth2-compatible login. Accepts `application/x-www-form-urlencoded` or `application/json`.

**Form body**
```
username=STF7XK3M&password=yourpassword
```

**Response** — same as `/auth/login`

---

### POST `/auth/refresh`
Exchange a refresh token for a new token pair.

**Request**
```json
{ "refresh_token": "eyJ..." }
```

**Response** — same as `/auth/login`

---

### POST `/auth/logout`
Revoke the current access token.

**Headers:** `Authorization: Bearer <access_token>`
**Response `200`**
```json
{ "message": "Logged out" }
```

---

### GET `/auth/me`
Get the currently authenticated user's profile.

**Response `200`**
```json
{
  "user_id": "uuid",
  "company_id": "uuid",
  "role": "staff",
  "username": "STF7XK3M",
  "full_name": "Jane Smith",
  "is_active": true,
  "created_at": "2026-02-24T10:00:00"
}
```

---

## 2. Tenants

Tenants are the physical people (employees, visitors, etc.) who access doors via biometric devices.

### POST `/tenants`
Create a new tenant. If `registration_device_id` is provided, the API will:
1. Create the user on that device.
2. Trigger fingerprint enrollment mode on the device.
3. Poll up to `capture_wait_seconds` for the user to scan their finger.
4. Auto-extract and store the fingerprint template.

> **Note on timing:** This request will block for up to `capture_wait_seconds` while waiting for the finger scan. Default is 30 seconds. Plan your UI accordingly (show a "waiting for fingerprint scan…" state).

**Request**
```json
{
  "full_name": "John Doe",
  "email": "john@example.com",
  "phone": "+1-555-0100",
  "tenant_type": "employee",
  "is_active": true,
  "global_access_from": "2026-03-01T00:00:00",
  "global_access_till": "2027-03-01T00:00:00",
  "registration_device_id": 3,
  "finger_index": 1,
  "capture_wait_seconds": 30
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `full_name` | string (max 15 chars for device limit) | Yes | Tenant's full name |
| `email` | string | No | |
| `phone` | string | No | |
| `tenant_type` | string | No | Default `"employee"` |
| `is_active` | bool | No | Default `true` |
| `global_access_from` | ISO datetime | No | Access start date |
| `global_access_till` | ISO datetime | No | Access expiry date |
| `registration_device_id` | int | No | Device to enroll fingerprint on at creation time |
| `finger_index` | int (1–10) | No | Default `1` |
| `capture_wait_seconds` | int (5–120) | No | Default `30`. Only used when `registration_device_id` is set |

**Response `201`**
```json
{
  "tenant": {
    "tenant_id": 42,
    "company_id": "uuid",
    "external_id": null,
    "full_name": "John Doe",
    "email": "john@example.com",
    "phone": "+1-555-0100",
    "tenant_type": "employee",
    "is_active": true,
    "is_access_enabled": true,
    "global_access_from": "2026-03-01T00:00:00",
    "global_access_till": "2027-03-01T00:00:00",
    "access_timezone": "UTC",
    "created_at": "2026-02-24T10:00:00"
  },
  "enrollment": {
    "tenant_id": 42,
    "device_id": 3,
    "user_created_on_device": true,
    "fingerprint_triggered": true,
    "fingerprint_stored": true,
    "credential_id": 7,
    "file_path": "/storage/fingerprints/tenant_42_finger_1.dat",
    "message": "Fingerprint captured and stored. Push to other devices via POST /{tenant_id}/enroll."
  }
}
```

**`enrollment` field:**
- `null` — `registration_device_id` was not provided.
- `fingerprint_stored: true` — fingerprint captured; use `/enroll` to push to more devices.
- `fingerprint_stored: false` — user didn't scan in time; call `/extract-fingerprint` after they scan, then use `/enroll`.

---

### GET `/tenants`
List all tenants for the authenticated user's company.

**Query Parameters**

| Param | Type | Description |
|---|---|---|
| `skip` | int | Pagination offset (default `0`) |
| `limit` | int | Page size, max `200` (default `50`) |
| `search` | string | Search by name, email, or phone |
| `company_id` | UUID | Super-admin only: filter by company |

**Response `200`** — array of tenant objects (same shape as `tenant` above)

---

### GET `/tenants/{tenant_id}`
Get a single tenant.

**Response `200`** — tenant object

---

### PATCH `/tenants/{tenant_id}`
Update tenant details.

**Request** — all fields optional
```json
{
  "full_name": "John Doe Jr.",
  "email": "john2@example.com",
  "phone": "+1-555-0200",
  "is_active": true,
  "is_access_enabled": true,
  "global_access_from": "2026-04-01T00:00:00",
  "global_access_till": "2027-04-01T00:00:00"
}
```

**Response `200`** — updated tenant object

---

### DELETE `/tenants/{tenant_id}`
Delete a tenant and automatically unenroll them from all devices.

**Response `200`**
```json
{ "message": "Tenant deleted" }
```

---

### POST `/tenants/{tenant_id}/extract-fingerprint`
Pull a fingerprint template that the user has already enrolled on the device and store it in the DB.

> Use this only when automatic capture during `POST /tenants` timed out, or when manually re-capturing.

**Request**
```json
{
  "device_id": 3,
  "finger_index": 1
}
```

**Response `200`**
```json
{
  "tenant_id": 42,
  "device_id": 3,
  "finger_index": 1,
  "fingerprint_stored": true,
  "credential_id": 7,
  "file_path": "/storage/fingerprints/tenant_42_finger_1.dat",
  "message": "Fingerprint stored. Push to other devices via POST /{tenant_id}/enroll."
}
```

---

### POST `/tenants/{tenant_id}/enroll`
Enroll a tenant on a device. If a fingerprint template is already stored, it is pushed automatically — no physical presence required.

**Request**
```json
{
  "device_id": 5,
  "finger_index": 1
}
```

**Response `200`**
```json
{
  "tenant_id": 42,
  "device_id": 5,
  "user_created_on_device": true,
  "fingerprint_pushed": true,
  "device_response": "...",
  "message": "Tenant enrolled on device successfully"
}
```

---

### POST `/tenants/{tenant_id}/enroll-bulk`
Enroll a tenant on multiple devices at once.

**Request**
```json
{ "device_ids": [5, 6, 7] }
```

**Response `200`**
```json
{
  "tenant_id": 42,
  "total": 3,
  "succeeded": 2,
  "failed": 1,
  "results": [
    { "device_id": 5, "success": true, "fingerprint_pushed": true },
    { "device_id": 6, "success": true, "fingerprint_pushed": true },
    { "device_id": 7, "success": false, "error": "Device 7 has no IP address configured" }
  ]
}
```

---

### PUT `/tenants/{tenant_id}/sync-device`
Re-sync tenant details and fingerprint on a single device. Use this after updating a tenant's name or access dates.

**Request**
```json
{ "device_id": 5 }
```

**Response `200`**
```json
{
  "tenant_id": 42,
  "device_id": 5,
  "user_updated_on_device": true,
  "fingerprint_pushed": true,
  "message": "Tenant details synced to device successfully"
}
```

---

### PUT `/tenants/{tenant_id}/sync-devices`
Re-sync on multiple devices.

**Request**
```json
{ "device_ids": [5, 6, 7] }
```

**Response `200`** — same shape as `enroll-bulk` response

---

### DELETE `/tenants/{tenant_id}/unenroll`
Remove tenant from a single device (deletes user + fingerprint from device).

**Request**
```json
{ "device_id": 5 }
```

**Response `200`**
```json
{
  "tenant_id": 42,
  "device_id": 5,
  "removed_from_device": true,
  "message": "Tenant removed from device successfully"
}
```

---

### DELETE `/tenants/{tenant_id}/unenroll-bulk`
Remove tenant from multiple devices.

**Request**
```json
{ "device_ids": [5, 6] }
```

**Response `200`** — same shape as `enroll-bulk` response

---

### DELETE `/tenants/devices/{device_id}/users`
**Admin only.** Wipe ALL users from a device and clear all DB mappings for that device.

**Response `200`**
```json
{
  "device_id": 3,
  "deleted_from_device": ["42", "43"],
  "errors": [],
  "db_mappings_cleared": 2,
  "message": "Wiped 2 user(s) from device."
}
```

---

### POST `/tenants/devices/{device_id}/cleanup-orphans`
**Admin only.** Remove users that exist on the device but have no matching DB record.

**Query Parameters**

| Param | Type | Description |
|---|---|---|
| `dry_run` | bool | `true` to preview without deleting (default `false`) |

**Response `200`**
```json
{
  "device_id": 3,
  "total_on_device": 10,
  "known_in_db": 8,
  "orphans_found": 2,
  "orphans": ["999", "1000"],
  "deleted": ["999", "1000"],
  "errors": [],
  "dry_run": false
}
```

---

## 3. Devices

Devices are Matrix COSEC biometric readers (fingerprint scanners at doors/gates).

### POST `/devices`
Register a new device.

**Request**
```json
{
  "site_id": 1,
  "vendor": "Matrix",
  "model_name": "COSEC DOOR FOQ",
  "device_serial_number": "SN-00123",
  "ip_address": "192.168.1.50",
  "mac_address": "AA:BB:CC:DD:EE:FF",
  "api_username": "admin",
  "api_password": "secret123",
  "api_port": 80,
  "use_https": false
}
```

| Field | Required | Description |
|---|---|---|
| `vendor` | Yes | e.g. `"Matrix"` |
| `ip_address` | No | Must be set before enrolling tenants |
| `api_username` | No | Default `"admin"` |
| `api_password` | No | Stored encrypted |
| `api_port` | No | Default `80` |

**Response `200`** — device object (password is never returned)

---

### GET `/devices`
List devices for the authenticated company.

**Query Parameters**

| Param | Type | Description |
|---|---|---|
| `site_id` | int | Filter by site |
| `skip` | int | Pagination offset |
| `limit` | int | Max `1000`, default `50` |
| `search` | string | Search by name/IP/serial |

**Response `200`** — array of device objects
```json
[
  {
    "device_id": 3,
    "company_id": "uuid",
    "site_id": 1,
    "device_serial_number": "SN-00123",
    "vendor": "Matrix",
    "model_name": "COSEC DOOR FOQ",
    "ip_address": "192.168.1.50",
    "mac_address": "AA:BB:CC:DD:EE:FF",
    "api_username": "admin",
    "api_port": 80,
    "use_https": false,
    "status": "online",
    "config": {},
    "created_at": "2026-02-24T10:00:00"
  }
]
```

---

### GET `/devices/{device_id}`
Get a single device.

---

### PATCH `/devices/{device_id}`
Update device details. All fields optional — same fields as `DeviceCreate`.

---

### DELETE `/devices/{device_id}`
Delete a device record.

**Response `200`**
```json
{ "message": "Device deleted" }
```

---

### POST `/devices/{device_id}/ping`
Check if a device is online. Updates `status` and `last_heartbeat` in DB.

**Response `200`**
```json
{
  "device_id": 3,
  "ip_address": "192.168.1.50",
  "online": true,
  "status": "online",
  "last_heartbeat": "2026-02-24T10:05:00"
}
```

---

## 4. Access Control

Control which sites and devices a tenant can access.

### Site-Level Access

#### POST `/access/site`
Grant a tenant access to a site (applies to all devices in that site).

**Request**
```json
{
  "tenant_id": 42,
  "site_id": 1,
  "access_from": "2026-03-01T00:00:00",
  "access_till": "2027-03-01T00:00:00"
}
```

#### GET `/access/site`
List site access rules.

**Query Parameters:** `tenant_id`, `site_id`, `skip`, `limit`

#### PATCH `/access/site/{access_id}`
Update a site access rule.

#### DELETE `/access/site/{access_id}`
Remove a site access rule.

---

### Device-Level Access

#### POST `/access/device`
Grant a tenant access to a specific device.

**Request**
```json
{
  "tenant_id": 42,
  "device_id": 3,
  "access_from": "2026-03-01T00:00:00",
  "access_till": "2027-03-01T00:00:00"
}
```

#### GET `/access/device`
List device access rules.

**Query Parameters:** `tenant_id`, `device_id`, `skip`, `limit`

#### PATCH `/access/device/{access_id}`
Update a device access rule.

#### DELETE `/access/device/{access_id}`
Remove a device access rule.

---

### Bulk Access

#### POST `/access/bulk`
Assign access to multiple sites or devices in one call.

**Request**
```json
{
  "tenant_id": 42,
  "site_ids": [1, 2],
  "device_ids": [3, 5]
}
```

---

## 5. Access Logs

Access events are synced from devices automatically by a background worker every ~30 seconds, or manually via the sync endpoint.

### GET `/logs`
List access events with filters.

**Query Parameters**

| Param | Type | Description |
|---|---|---|
| `device_id` | int | Filter by device |
| `tenant_id` | int | Filter by tenant |
| `event_type` | string | e.g. `"access_granted"`, `"access_denied"` |
| `access_granted` | bool | `true` or `false` |
| `from_time` | ISO datetime | Start of time range |
| `to_time` | ISO datetime | End of time range |
| `skip` | int | Pagination offset |
| `limit` | int | Max `500`, default `50` |

**Response `200`**
```json
[
  {
    "event_id": 1001,
    "company_id": "uuid",
    "device_id": 3,
    "tenant_id": 42,
    "event_type": "access_granted",
    "event_time": "2026-02-24T09:32:11",
    "access_granted": true,
    "cosec_event_id": 101,
    "detail_1": "...",
    "credential_type": "fingerprint",
    "direction": "entry",
    "notes": null,
    "created_at": "2026-02-24T09:32:15"
  }
]
```

---

### GET `/logs/export`
Export filtered logs as an Excel `.xlsx` file download.

**Query Parameters** — same as `GET /logs` (no pagination)
**Response** — `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` file download with filename `access_logs.xlsx`

---

### GET `/logs/{event_id}`
Get a single log entry.

---

### PATCH `/logs/{event_id}`
Update editable fields on a log entry.

**Request**
```json
{
  "notes": "Maintenance visit",
  "direction": "entry",
  "auth_used": "fingerprint"
}
```

---

### DELETE `/logs/{event_id}`
Delete a log entry.

---

### POST `/logs/sync/{device_id}`
Manually trigger a log sync from a device.

**Query Parameters**

| Param | Type | Description |
|---|---|---|
| `batch_size` | int | Events per batch, max `1000`, default `100` |

**Response `200`**
```json
{
  "device_id": 3,
  "synced": 15,
  "skipped": 0,
  "errors": 0,
  "last_seq": 2048
}
```
> New events are also broadcast to connected WebSocket clients automatically.

---

### GET `/logs/diagnostic/{device_id}`
**Admin only.** Debug: probe a device directly and return raw event XML + parsed output. Useful to verify device connectivity before syncing.

**Query Parameters:** `seq` (default `1`), `rollover` (default `0`), `count` (default `10`)

---

### POST `/logs/reset-cursor/{device_id}`
**Admin only.** Reset the sync cursor so the next sync re-fetches all events from the beginning. Use when events are missing or cursor is stale.

**Response `200`**
```json
{ "device_id": 3, "message": "Cursor reset — next sync will start from seq=1" }
```

---

## 6. Device Mappings

Tracks which tenants are enrolled on which devices.

### GET `/device-mappings`
List all mappings.

**Query Parameters:** `tenant_id`, `device_id`, `skip`, `limit`

**Response `200`**
```json
[
  {
    "mapping_id": 11,
    "tenant_id": 42,
    "device_id": 3,
    "matrix_user_id": "42",
    "is_synced": true,
    "last_sync_at": "2026-02-24T10:00:00",
    "last_sync_attempt_at": "2026-02-24T10:00:00",
    "sync_attempt_count": 1,
    "device_response": { "fingerprint_pushed": true },
    "created_at": "2026-02-24T10:00:00"
  }
]
```

---

### GET `/device-mappings/unsynced`
List mappings that have not been successfully synced (useful for retry logic).

---

### GET `/device-mappings/{mapping_id}`
Get a single mapping.

---

### PATCH `/device-mappings/{mapping_id}`
Update a mapping's sync status manually.

---

### DELETE `/device-mappings/{mapping_id}`
Remove a mapping record (does NOT remove the user from the device — use `unenroll` for that).

---

## 7. Companies

> Super-admin only.

### POST `/companies`
Create a company.

**Request**
```json
{
  "name": "Acme Corp",
  "email": "admin@acme.com"
}
```

### GET `/companies`
List all companies.

### GET `/companies/{company_id}`
Get a single company.

### PATCH `/companies/{company_id}`
Update a company.

### DELETE `/companies/{company_id}`
Delete a company.

---

## 8. Sites

Sites are physical locations (buildings, floors, gates) that group devices.

### POST `/sites`
Create a site.

**Request**
```json
{
  "name": "Main Office",
  "location": "Floor 3",
  "company_id": "uuid"
}
```

### GET `/sites`
List sites (scoped to the authenticated company).

### GET `/sites/{site_id}`
Get a single site.

### PATCH `/sites/{site_id}`
Update a site.

### DELETE `/sites/{site_id}`
Delete a site.

---

## 9. App Users

App users are staff members who manage the iGatera dashboard (not physical tenants).

### GET `/users`
List app users (scoped to the authenticated company).

### GET `/users/{user_id}`
Get a single app user.

### PATCH `/users/{user_id}`
Update an app user.

### DELETE `/users/{user_id}`
Delete an app user.

---

## 10. WebSocket — Real-Time Events

Connect to receive live access events as they are synced from devices.

**URL**
```
ws://<host>/api/logs/ws?token=<access_token>
```

**Authentication:** Pass your Bearer `access_token` as a query parameter.

**Message format** (JSON pushed from server):
```json
{
  "type": "access_event",
  "event_id": 1001,
  "company_id": "uuid",
  "device_id": 3,
  "tenant_id": 42,
  "event_type": "access_granted",
  "event_time": "2026-02-24T09:32:11",
  "access_granted": true,
  "cosec_event_id": 101
}
```

**Keepalive:** Send any text message to keep the connection alive. The server echoes nothing back — it only pushes events.

---

## 11. Error Responses

All errors follow this shape:
```json
{
  "detail": "Human-readable error message"
}
```

| Status | Meaning |
|---|---|
| `400` | Bad request — invalid input or missing required field |
| `401` | Unauthorized — missing or invalid token |
| `403` | Forbidden — authenticated but not allowed |
| `404` | Not found |
| `409` | Conflict — e.g. duplicate `external_id` |
| `502` | Bad gateway — device rejected the request (check `detail` for device error) |
| `422` | Validation error — request body failed schema validation |

**Validation error example (`422`):**
```json
{
  "detail": [
    {
      "loc": ["body", "full_name"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

---

## 12. Roles & Permissions

| Endpoint category | `staff` | `company_admin` | `super_admin` |
|---|---|---|---|
| Auth | ✅ | ✅ | ✅ |
| Tenants (own company) | ✅ | ✅ | ✅ |
| Devices (own company) | ✅ | ✅ | ✅ |
| Wipe device / cleanup orphans | ❌ | ✅ | ✅ |
| Logs (own company) | ✅ | ✅ | ✅ |
| Logs diagnostic / cursor reset | ❌ | ✅ | ✅ |
| Companies | ❌ | ❌ | ✅ |
| Cross-company access | ❌ | ❌ | ✅ |

---

## 13. Tenant Enrollment — Full Flow

This section shows the recommended sequence for enrolling a new tenant end-to-end.

### Scenario A — Fingerprint captured at creation (recommended)

```
1. POST /tenants
   Body: { full_name, registration_device_id: 3, capture_wait_seconds: 30, ... }

   → API creates tenant in DB
   → API creates user on device 3
   → API triggers enrollment mode on device 3
   → User places their finger on device 3's sensor
   → API auto-extracts fingerprint and stores in DB
   → Returns: enrollment.fingerprint_stored = true

2. POST /tenants/{tenant_id}/enroll
   Body: { device_id: 5 }
   → Pushes stored fingerprint to device 5 (no physical visit required)

3. POST /tenants/{tenant_id}/enroll-bulk
   Body: { device_ids: [6, 7, 8] }
   → Pushes to multiple devices at once
```

### Scenario B — Fingerprint capture timed out

```
1. POST /tenants
   Body: { full_name, registration_device_id: 3, ... }
   → Returns: enrollment.fingerprint_stored = false, enrollment.fingerprint_triggered = true
   → (User didn't scan in time)

2. User walks to device 3 and scans their finger via the device UI
   (Device is already in their name from step 1)

3. POST /tenants/{tenant_id}/extract-fingerprint
   Body: { device_id: 3, finger_index: 1 }
   → Pulls template from device → stores in DB

4. POST /tenants/{tenant_id}/enroll-bulk
   Body: { device_ids: [5, 6, 7] }
   → Pushes to all target devices
```

### Scenario C — No registration device at creation time

```
1. POST /tenants
   Body: { full_name, email, ... }  (no registration_device_id)
   → Creates tenant in DB only

2. POST /tenants/{tenant_id}/enroll
   Body: { device_id: 3 }
   → Creates user on device 3 (no fingerprint yet)

3. User scans at device 3 via device UI

4. POST /tenants/{tenant_id}/extract-fingerprint
   Body: { device_id: 3, finger_index: 1 }

5. POST /tenants/{tenant_id}/enroll-bulk
   Body: { device_ids: [5, 6, 7] }
```

---

---

## 14. Matrix Device API Endpoints (Reference)

These are the actual Matrix COSEC device endpoints used internally. Documented here for debugging.

| Operation | Device endpoint | Method | Key params |
|---|---|---|---|
| Create user | `/device.cgi/users` | GET | `action=set`, `user-id`, `name`, `user-active` |
| Get user | `/device.cgi/users` | GET | `action=get`, `user-id` |
| Trigger enrollment | `/device.cgi/enrolluser` | GET | `action=enroll`, `type=2` (finger), `user-id` |
| Extract fingerprint | `/device.cgi/credential` | POST | `action=get`, `type=1`, `user-id`, `finger-index` |
| Import fingerprint | `/device.cgi/credential` | POST | `action=set`, `type=1`, `user-id`, `finger-index` + binary body |
| Delete fingerprint | `/device.cgi/credential` | GET | `action=delete`, `type=1`, `user-id` |
| Delete user | `/device.cgi/users` | GET | `action=delete`, `user-id` |
| Fetch events | `/device.cgi/events` | GET | `action=getevent`, `roll-over-count`, `seq-number` |

---

*Last updated: 2026-02-24*

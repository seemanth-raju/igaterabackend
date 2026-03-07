from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.services.tenants.enrollment import (
    _build_client,
    _get_device_or_404,
    enroll_new_tenant,
    enroll_to_device,
    enroll_to_devices_bulk,
    extract_fingerprint_from_device,
    register_and_capture_fingerprint,
    unenroll_from_device,
    unenroll_from_devices_bulk,
    update_tenant_on_device,
    update_tenant_on_devices_bulk,
)
from app.api.services.tenants.schema import (
    TenantCreate,
    TenantCreateResponse,
    TenantEnrollRequest,
    TenantEnrollResponse,
    TenantRead,
    TenantUpdate,
)
from app.api.services.tenants.service import create_tenant, delete_tenant, get_tenant, list_tenants, update_tenant
from database.models import AppUser, Device, DeviceUserMapping, UserRole

router = APIRouter(prefix="/tenants", tags=["tenants"])


def _to_tenant_read(tenant) -> TenantRead:
    return TenantRead(
        tenant_id=tenant.tenant_id,
        company_id=str(tenant.company_id) if tenant.company_id else None,
        external_id=tenant.external_id,
        full_name=tenant.full_name,
        email=tenant.email,
        phone=tenant.phone,
        tenant_type=tenant.tenant_type,
        is_active=tenant.is_active,
        is_access_enabled=tenant.is_access_enabled,
        global_access_from=tenant.global_access_from,
        global_access_till=tenant.global_access_till,
        access_timezone=tenant.access_timezone,
        created_at=tenant.created_at,
    )


def _check_tenant_access(tenant, current_user: AppUser) -> None:
    if current_user.role != UserRole.super_admin.value and tenant.company_id != current_user.company_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this tenant")


def _resolve_company_id(requested: UUID | None, current_user: AppUser) -> UUID:
    """
    Determine the effective company_id for tenant creation.

    - super_admin + company_id provided  → use the provided company_id
    - super_admin + no company_id        → use their own company_id
    - any other role                     → always their own company_id (input ignored)
    """
    if current_user.role == UserRole.super_admin.value and requested is not None:
        return requested
    return current_user.company_id


# ---------------------------------------------------------------------------
# Atomic enrollment (create + fingerprint in one step)
# ---------------------------------------------------------------------------


@router.post(
    "/enroll",
    response_model=TenantEnrollResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Tenant created and fingerprint enrolled successfully."},
        400: {
            "description": "Validation error — e.g. duplicate `external_id` for this company.",
            "content": {"application/json": {"example": {"detail": "A tenant with the same external_id already exists for this company."}}},
        },
        408: {
            "description": "Fingerprint not captured in time. The user did not scan their finger within 30 seconds. No tenant was created.",
            "content": {"application/json": {"example": {"detail": "Fingerprint not captured within 30s. Tenant was NOT created — please try again and scan promptly."}}},
        },
        422: {
            "description": "Request body failed schema validation (e.g. `full_name` exceeds 15 characters, missing required field).",
        },
        502: {
            "description": "The Matrix device rejected the request — device unreachable, wrong credentials, or enrollment mode failed. No tenant was created.",
            "content": {"application/json": {"example": {"detail": "Device rejected user creation: <raw device response>"}}},
        },
    },
)
def enroll_new_tenant_route(
    payload: TenantEnrollRequest,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> TenantEnrollResponse:
    """
    ## Enroll a new tenant (atomic — create + fingerprint in one step)

    This is the **primary endpoint for registering a new tenant**. It combines
    tenant creation and biometric enrollment into a single atomic operation.

    ### What happens when you call this endpoint

    1. **Validate** — device ID is resolved; if the device is unknown or has no IP
       the request is rejected immediately (no DB writes).
    2. **Create user on device** — the tenant record is pushed to the Matrix
       biometric device identified by `device_id`.
    3. **Trigger enrollment mode** — the device's fingerprint sensor is activated
       and waits for the user to place their finger.
    4. **Capture fingerprint** — the backend polls the device every 3 seconds for
       up to **30 seconds**. The user must place their finger on the sensor during
       this window.
    5. **Persist** — once the fingerprint is captured the template is saved to
       server storage and the following records are committed to the database
       in a single transaction:
         - `tenant` row
         - `credential` row (fingerprint file path + hash)
         - `tenant_site_access` row (links tenant to the selected site)
         - `device_user_mapping` row (marks enrollment as synced)
         - `device_assignment_log` audit entry

    ### Atomicity guarantee

    The tenant is **never** saved to the database unless the fingerprint is
    successfully captured. If any step fails:
    - The database transaction is rolled back.
    - The user is deleted from the device (best-effort cleanup).
    - An appropriate HTTP error is returned (see error codes below).

    ### Company scoping (`company_id`)

    | Role | `company_id` in body | Tenant created under |
    |------|---------------------|----------------------|
    | `super_admin` | provided | the supplied company |
    | `super_admin` | omitted | super_admin's own company |
    | any other role | provided or omitted | always their own company |

    Non-super-admin users cannot create tenants for another company — the field
    is accepted in the request but silently ignored.

    ### Important UX notes for the frontend

    - The request will **block for up to ~30 seconds** while the backend waits for
      the fingerprint. Do **not** set a short HTTP timeout on the client side —
      use at least 60 seconds.
    - Show the user a "Please place your finger on the device" prompt immediately
      after submitting the form.
    - `full_name` is limited to **15 characters** by the device hardware. Enforce
      this in the form — the API will also reject it with 422 if violated.
    - `global_access_till` is required and is programmed into the device so it
      automatically expires access at the hardware level.

    ### Error codes

    | Code | Meaning |
    |------|---------|
    | `400` | `external_id` already exists for this company |
    | `404` | `device_id`, `site_id`, or supplied `company_id` not found |
    | `408` | User did not scan finger within 30 s — tenant NOT created, retry |
    | `422` | Request body invalid (e.g. name too long, missing required field) |
    | `502` | Matrix device rejected the call (offline, bad credentials, etc.) |
    """
    result = enroll_new_tenant(
        payload=payload,
        company_id=_resolve_company_id(payload.company_id, current_user),
        db=db,
        performed_by=current_user.user_id,
    )
    return TenantEnrollResponse(**result)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=TenantCreateResponse, status_code=status.HTTP_201_CREATED)
def create_tenant_route(
    payload: TenantCreate,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> TenantCreateResponse:
    """
    Create a new tenant.

    If `registration_device_id` is supplied the API will immediately:
      1. Create the user on that device.
      2. Trigger fingerprint enrollment mode on the device.
      3. Poll up to `capture_wait_seconds` (default 30 s) for the user to scan.
      4. Extract and store the fingerprint template in the DB.

    Check `enrollment.fingerprint_stored` in the response:
    - `true`  → fingerprint captured; push to other devices via POST /{tenant_id}/enroll.
    - `false` → timeout or trigger failed; have the user scan and call
                POST /{tenant_id}/extract-fingerprint manually, then enroll.
    """
    tenant = create_tenant(payload, _resolve_company_id(payload.company_id, current_user), db)
    tenant_read = _to_tenant_read(tenant)

    enrollment_result: dict | None = None
    if payload.registration_device_id is not None:
        enrollment_result = register_and_capture_fingerprint(
            tenant_id=tenant.tenant_id,
            device_id=payload.registration_device_id,
            db=db,
            finger_index=payload.finger_index,
            capture_wait_seconds=payload.capture_wait_seconds,
            performed_by=current_user.user_id,
        )

    return TenantCreateResponse(tenant=tenant_read, enrollment=enrollment_result)


@router.get("", response_model=list[TenantRead])
def list_tenants_route(
    company_id: UUID | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    search: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> list[TenantRead]:
    if current_user.role != UserRole.super_admin.value:
        company_id = current_user.company_id
    tenants = list_tenants(db, company_id=company_id, skip=skip, limit=limit, search=search)
    return [_to_tenant_read(t) for t in tenants]


@router.get("/{tenant_id}", response_model=TenantRead)
def get_tenant_route(
    tenant_id: int,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> TenantRead:
    tenant = get_tenant(tenant_id, db)
    _check_tenant_access(tenant, current_user)
    return _to_tenant_read(tenant)


@router.patch("/{tenant_id}", response_model=TenantRead)
def update_tenant_route(
    tenant_id: int,
    payload: TenantUpdate,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> TenantRead:
    tenant = get_tenant(tenant_id, db)
    _check_tenant_access(tenant, current_user)
    tenant = update_tenant(tenant_id, payload, db)
    return _to_tenant_read(tenant)


@router.delete("/{tenant_id}")
def delete_tenant_route(
    tenant_id: int,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> dict[str, str]:
    """Delete tenant and unenroll from all enrolled devices."""
    tenant = get_tenant(tenant_id, db)
    _check_tenant_access(tenant, current_user)

    mappings = db.query(DeviceUserMapping).filter(DeviceUserMapping.tenant_id == tenant_id).all()
    if mappings:
        unenroll_from_devices_bulk(
            tenant_id=tenant_id,
            device_ids=[m.device_id for m in mappings],
            db=db,
            performed_by=current_user.user_id,
        )

    delete_tenant(tenant_id, db)
    return {"message": "Tenant deleted"}


# ---------------------------------------------------------------------------
# Fingerprint extraction (step 2 of cross-device enrollment)
# ---------------------------------------------------------------------------


@router.post("/{tenant_id}/extract-fingerprint")
def extract_fingerprint_route(
    tenant_id: int,
    device_id: int = Body(..., embed=True, description="Device the user has already enrolled their finger on"),
    finger_index: int = Body(default=1, ge=1, le=10, embed=True),
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> dict:
    """
    Pull the fingerprint template the user has already enrolled at the device
    and store it in the DB.

    Call this AFTER the user has physically scanned their finger at the device.
    Once stored, use POST /{tenant_id}/enroll to push the fingerprint to
    any other device without the user needing to visit each one.
    """
    tenant = get_tenant(tenant_id, db)
    _check_tenant_access(tenant, current_user)
    return extract_fingerprint_from_device(
        tenant_id=tenant_id,
        device_id=device_id,
        db=db,
        finger_index=finger_index,
        performed_by=current_user.user_id,
    )


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------


@router.post("/{tenant_id}/enroll")
def enroll_route(
    tenant_id: int,
    device_id: int = Body(..., embed=True),
    finger_index: int = Body(default=1, ge=1, le=10, embed=True),
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> dict:
    """
    Enroll tenant on a Matrix device.

    If a fingerprint was already captured via /capture-fingerprint,
    it will be pushed automatically — no physical presence required.
    """
    tenant = get_tenant(tenant_id, db)
    _check_tenant_access(tenant, current_user)
    return enroll_to_device(
        tenant_id=tenant_id, device_id=device_id, db=db,
        finger_index=finger_index, performed_by=current_user.user_id,
    )


@router.post("/{tenant_id}/enroll-bulk")
def enroll_bulk_route(
    tenant_id: int,
    device_ids: list[int] = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> dict:
    """Enroll tenant on multiple Matrix devices."""
    tenant = get_tenant(tenant_id, db)
    _check_tenant_access(tenant, current_user)
    return enroll_to_devices_bulk(tenant_id=tenant_id, device_ids=device_ids, db=db, performed_by=current_user.user_id)


@router.put("/{tenant_id}/sync-device")
def sync_device_route(
    tenant_id: int,
    device_id: int = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> dict:
    """Re-sync tenant details on a single device."""
    tenant = get_tenant(tenant_id, db)
    _check_tenant_access(tenant, current_user)
    return update_tenant_on_device(tenant_id=tenant_id, device_id=device_id, db=db, performed_by=current_user.user_id)


@router.put("/{tenant_id}/sync-devices")
def sync_devices_bulk_route(
    tenant_id: int,
    device_ids: list[int] = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> dict:
    """Re-sync tenant details on multiple devices."""
    tenant = get_tenant(tenant_id, db)
    _check_tenant_access(tenant, current_user)
    return update_tenant_on_devices_bulk(tenant_id=tenant_id, device_ids=device_ids, db=db, performed_by=current_user.user_id)


@router.delete("/{tenant_id}/unenroll")
def unenroll_route(
    tenant_id: int,
    device_id: int = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> dict:
    """Remove tenant from a single device."""
    tenant = get_tenant(tenant_id, db)
    _check_tenant_access(tenant, current_user)
    return unenroll_from_device(tenant_id=tenant_id, device_id=device_id, db=db, performed_by=current_user.user_id)


@router.delete("/{tenant_id}/unenroll-bulk")
def unenroll_bulk_route(
    tenant_id: int,
    device_ids: list[int] = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> dict:
    """Remove tenant from multiple devices."""
    tenant = get_tenant(tenant_id, db)
    _check_tenant_access(tenant, current_user)
    return unenroll_from_devices_bulk(tenant_id=tenant_id, device_ids=device_ids, db=db, performed_by=current_user.user_id)


# ---------------------------------------------------------------------------
# Device-level operations
# ---------------------------------------------------------------------------


@router.delete("/devices/{device_id}/users")
def wipe_device_users(
    device_id: int,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> dict:
    """Wipe ALL users from a device and clear DB mappings. Admin only."""
    if current_user.role not in (UserRole.super_admin.value, UserRole.company_admin.value):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    if not device.ip_address:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Device has no IP configured")

    client = _build_client(device)
    wipe_result = client.wipe_all_users()

    db_count = (
        db.query(DeviceUserMapping)
        .filter(DeviceUserMapping.device_id == device_id)
        .delete()
    )
    db.commit()

    return {
        "device_id": device_id,
        "deleted_from_device": wipe_result["deleted"],
        "errors": wipe_result["errors"],
        "db_mappings_cleared": db_count,
        "message": f"Wiped {len(wipe_result['deleted'])} user(s) from device.",
    }


@router.post("/devices/{device_id}/cleanup-orphans")
def cleanup_device_orphans(
    device_id: int,
    dry_run: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> dict:
    """Remove users on the device that have no DB mapping. Pass ?dry_run=true to preview."""
    if current_user.role not in (UserRole.super_admin.value, UserRole.company_admin.value):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")

    device = _get_device_or_404(device_id, db)
    client = _build_client(device)

    known_ids: set[str] = {
        row.matrix_user_id
        for row in db.query(DeviceUserMapping.matrix_user_id)
        .filter(DeviceUserMapping.device_id == device_id)
        .all()
    }

    device_ids = client.list_users()

    if not device_ids and known_ids:
        return {
            "device_id": device_id,
            "warning": "Device returned no users. Bulk user listing may not be supported on this firmware.",
            "orphans": [],
            "deleted": [],
            "dry_run": dry_run,
        }

    orphans = [uid for uid in device_ids if uid not in known_ids]

    deleted: list[str] = []
    errors: list[dict] = []
    if not dry_run:
        for uid in orphans:
            client.delete_fingerprint(uid)
            result = client.delete_user(uid)
            if result["success"]:
                deleted.append(uid)
            else:
                errors.append({"user_id": uid, "error": result["response"]})

    return {
        "device_id": device_id,
        "total_on_device": len(device_ids),
        "known_in_db": len(known_ids),
        "orphans_found": len(orphans),
        "orphans": orphans,
        "deleted": deleted,
        "errors": errors,
        "dry_run": dry_run,
    }

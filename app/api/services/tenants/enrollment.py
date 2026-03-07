"""Tenant device enrollment — push tenant to Matrix devices.

Fingerprint cross-device workflow:
  1. POST /tenants (registration_device_id=X, capture_wait_seconds=30)
                              → create tenant in DB
                              → create user on device X
                              → trigger fingerprint enrollment mode on device X
                              → poll up to capture_wait_seconds for the user to scan
                              → auto-extract template and store in DB
  2. POST /tenants/{id}/enroll (device_id=Y)
                              → push stored fingerprint to device Y
                                no physical presence required
  3. POST /tenants/{id}/enroll-bulk → push to many devices at once

  If the fingerprint was not captured during step 1 (timeout or trigger failed):
  - User scans at the device manually
  - Call POST /tenants/{id}/extract-fingerprint to store the template
  - Then proceed with step 2/3
"""

import time
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.matrix import MatrixDeviceClient, calculate_file_hash
from database.models import Credential, Device, DeviceAssignmentLog, DeviceUserMapping, Tenant, TenantSiteAccess


def is_access_active(tenant: Tenant) -> bool:
    """Return True if the tenant should currently have active device access."""
    if not tenant.is_active or not tenant.is_access_enabled:
        return False
    now = datetime.now(timezone.utc)
    if tenant.global_access_from:
        from_dt = tenant.global_access_from
        if from_dt.tzinfo is None:
            from_dt = from_dt.replace(tzinfo=timezone.utc)
        if now < from_dt:
            return False
    if tenant.global_access_till:
        till_dt = tenant.global_access_till
        if till_dt.tzinfo is None:
            till_dt = till_dt.replace(tzinfo=timezone.utc)
        if now > till_dt:
            return False
    return True


def _get_tenant_or_404(tenant_id: int, db: Session) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


def _get_device_or_404(device_id: int, db: Session) -> Device:
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Device {device_id} not found")
    if not device.ip_address:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Device {device_id} has no IP address configured",
        )
    return device


def _build_client(device: Device) -> MatrixDeviceClient:
    return MatrixDeviceClient(
        device_ip=device.ip_address,
        username=device.api_username or "admin",
        encrypted_password=device.api_password_encrypted or "",
        use_https=device.use_https,
    )


def _create_user_on_device(client: MatrixDeviceClient, tenant: Tenant) -> dict:
    """Push user record to device. Raises HTTPException on device rejection."""
    if len(tenant.full_name) > 15:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Name '{tenant.full_name}' is too long — device limit is 15 characters.",
        )
    validity_end = tenant.global_access_till.date() if tenant.global_access_till else None
    result = client.create_user(
        user_id=str(tenant.tenant_id),
        name=tenant.full_name,
        active=is_access_active(tenant),
        validity_end_date=validity_end,
    )
    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Device rejected user creation: {result['response']}",
        )
    return result


def _find_fingerprint_credential(tenant_id: int, db: Session, finger_index: int = 1) -> Credential | None:
    """Return the stored fingerprint credential for this tenant, or None."""
    return (
        db.query(Credential)
        .filter(
            Credential.tenant_id == tenant_id,
            Credential.type == "finger",
            Credential.slot_index == finger_index,
        )
        .order_by(Credential.created_at.desc())
        .first()
    )


def _upsert_mapping(
    tenant_id: int,
    device_id: int,
    db: Session,
    *,
    synced: bool = False,
    fingerprint_pushed: bool = False,
) -> DeviceUserMapping:
    """Create or update a DeviceUserMapping row."""
    mapping = (
        db.query(DeviceUserMapping)
        .filter(DeviceUserMapping.tenant_id == tenant_id, DeviceUserMapping.device_id == device_id)
        .first()
    )
    now = db.query(func.current_timestamp()).scalar()
    if mapping:
        mapping.is_synced = synced
        mapping.last_sync_at = now if synced else mapping.last_sync_at
        mapping.last_sync_attempt_at = now
        mapping.sync_attempt_count = (mapping.sync_attempt_count or 0) + 1
        if fingerprint_pushed:
            existing = mapping.device_response or {}
            mapping.device_response = {**existing, "fingerprint_pushed": True}
        mapping.updated_at = now
    else:
        mapping = DeviceUserMapping(
            tenant_id=tenant_id,
            device_id=device_id,
            matrix_user_id=str(tenant_id),
            is_synced=synced,
            last_sync_at=now if synced else None,
            last_sync_attempt_at=now,
            sync_attempt_count=1,
            device_response={"fingerprint_pushed": fingerprint_pushed},
        )
        db.add(mapping)
    db.flush()
    return mapping


def _log_assignment(
    tenant_id: int,
    device_id: int,
    action: str,
    db: Session,
    performed_by=None,
    reason: str | None = None,
    synced: bool = False,
) -> None:
    """Write an audit entry to device_assignment_log."""
    log = DeviceAssignmentLog(
        tenant_id=tenant_id,
        device_id=device_id,
        action=action,
        performed_by=performed_by,
        reason=reason,
        synced_to_device=synced,
    )
    db.add(log)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_fingerprint_from_device(
    tenant_id: int,
    device_id: int,
    db: Session,
    finger_index: int = 1,
    performed_by=None,
) -> dict:
    """
    Pull a fingerprint template that the user has already enrolled at the device
    and store it in the DB / local file storage.

    Call this AFTER the user has physically enrolled their finger at the device.
    Once stored, the template can be pushed to any other device via enroll_to_device()
    without the user needing to visit each device in person.
    """
    tenant = _get_tenant_or_404(tenant_id, db)
    device = _get_device_or_404(device_id, db)
    client = _build_client(device)
    user_id = str(tenant.tenant_id)

    template_data, file_path = client.extract_fingerprint(user_id, finger_index)
    if not template_data or not file_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No fingerprint template found on device for this user. "
                "Make sure the user has enrolled their finger at the device first."
            ),
        )

    # Overwrite any existing credential for this slot
    existing = _find_fingerprint_credential(tenant_id, db, finger_index)
    if existing:
        db.delete(existing)
        db.flush()

    file_hash = calculate_file_hash(file_path)
    credential = Credential(
        tenant_id=tenant.tenant_id,
        type="finger",
        slot_index=finger_index,
        file_path=file_path,
        file_hash=file_hash,
        algorithm_version="matrix_v1",
    )
    db.add(credential)

    _upsert_mapping(tenant_id, device_id, db, synced=True, fingerprint_pushed=True)
    _log_assignment(tenant_id, device_id, "extract_fingerprint", db, performed_by=performed_by, synced=True)
    db.commit()

    return {
        "tenant_id": tenant_id,
        "device_id": device_id,
        "finger_index": finger_index,
        "fingerprint_stored": True,
        "credential_id": credential.credential_id,
        "file_path": file_path,
        "message": "Fingerprint stored. Push to other devices via POST /{tenant_id}/enroll.",
    }


def register_and_capture_fingerprint(
    tenant_id: int,
    device_id: int,
    db: Session,
    finger_index: int = 1,
    capture_wait_seconds: int = 30,
    performed_by=None,
) -> dict:
    """
    Full first-enrollment flow for a new tenant on their registration device:
      1. Create user record on the device.
      2. Trigger the device's fingerprint enrollment mode.
      3. Poll every 3 s (up to capture_wait_seconds) for the user to scan.
      4. Extract template and store Credential in DB.

    Returns a result dict indicating whether the fingerprint was captured.
    If capture times out, the user can still scan later and call
    extract_fingerprint_from_device() manually.
    """
    tenant = _get_tenant_or_404(tenant_id, db)
    device = _get_device_or_404(device_id, db)
    client = _build_client(device)
    user_id = str(tenant.tenant_id)

    # Step 1: Create user on device
    _create_user_on_device(client, tenant)

    # Step 2: Trigger enrollment mode
    trigger_result = client.trigger_fingerprint_enrollment(user_id, finger_index)
    if not trigger_result["success"]:
        _upsert_mapping(tenant_id, device_id, db, synced=True, fingerprint_pushed=False)
        _log_assignment(tenant_id, device_id, "enroll", db, performed_by=performed_by, synced=True)
        db.commit()
        return {
            "tenant_id": tenant_id,
            "device_id": device_id,
            "user_created_on_device": True,
            "fingerprint_triggered": False,
            "fingerprint_stored": False,
            "message": (
                "User created on device but enrollment mode could not be triggered. "
                "Have the user scan their finger at the device then call POST /{tenant_id}/extract-fingerprint."
            ),
        }

    # Step 3: Poll for fingerprint capture
    poll_interval = 3
    elapsed = 0
    template_data: bytes | None = None
    file_path: str | None = None

    while elapsed < capture_wait_seconds:
        time.sleep(poll_interval)
        elapsed += poll_interval
        template_data, file_path = client.extract_fingerprint(user_id, finger_index)
        if template_data and file_path:
            break

    # Step 4: Store credential if captured
    fingerprint_stored = False
    credential_id: int | None = None

    if template_data and file_path:
        existing = _find_fingerprint_credential(tenant_id, db, finger_index)
        if existing:
            db.delete(existing)
            db.flush()

        file_hash = calculate_file_hash(file_path)
        credential = Credential(
            tenant_id=tenant.tenant_id,
            type="finger",
            slot_index=finger_index,
            file_path=file_path,
            file_hash=file_hash,
            algorithm_version="matrix_v1",
        )
        db.add(credential)
        db.flush()
        credential_id = credential.credential_id
        fingerprint_stored = True

        _upsert_mapping(tenant_id, device_id, db, synced=True, fingerprint_pushed=True)
        _log_assignment(tenant_id, device_id, "extract_fingerprint", db, performed_by=performed_by, synced=True)
    else:
        _upsert_mapping(tenant_id, device_id, db, synced=True, fingerprint_pushed=False)
        _log_assignment(tenant_id, device_id, "enroll", db, performed_by=performed_by, synced=True)

    db.commit()

    return {
        "tenant_id": tenant_id,
        "device_id": device_id,
        "user_created_on_device": True,
        "fingerprint_triggered": True,
        "fingerprint_stored": fingerprint_stored,
        "credential_id": credential_id,
        "file_path": file_path,
        "message": (
            "Fingerprint captured and stored. Push to other devices via POST /{tenant_id}/enroll."
            if fingerprint_stored
            else (
                f"Enrollment mode triggered but no fingerprint captured within {capture_wait_seconds}s. "
                "Have the user scan their finger at the device then call POST /{tenant_id}/extract-fingerprint."
            )
        ),
    }


def enroll_to_device(
    tenant_id: int,
    device_id: int,
    db: Session,
    finger_index: int = 1,
    performed_by=None,
) -> dict:
    """
    Enroll a tenant on a Matrix device.

    Creates the user record on the device and, if a fingerprint template
    is already stored in the DB, pushes it automatically — no need for the
    user to visit the device in person.
    """
    tenant = _get_tenant_or_404(tenant_id, db)
    device = _get_device_or_404(device_id, db)
    client = _build_client(device)

    create_resp = _create_user_on_device(client, tenant)

    fingerprint_pushed = False
    credential = _find_fingerprint_credential(tenant_id, db, finger_index)
    if credential and credential.file_path:
        fp_result = client.import_fingerprint(
            user_id=str(tenant_id),
            file_path=credential.file_path,
            finger_index=finger_index,
        )
        if fp_result["success"]:
            fingerprint_pushed = True

    _upsert_mapping(tenant_id, device_id, db, synced=True, fingerprint_pushed=fingerprint_pushed)
    _log_assignment(tenant_id, device_id, "enroll", db, performed_by=performed_by, synced=True)
    db.commit()

    return {
        "tenant_id": tenant_id,
        "device_id": device_id,
        "user_created_on_device": True,
        "fingerprint_pushed": fingerprint_pushed,
        "device_response": create_resp["response"],
        "message": "Tenant enrolled on device successfully",
    }


def enroll_to_devices_bulk(
    tenant_id: int,
    device_ids: list[int],
    db: Session,
    finger_index: int = 1,
    performed_by=None,
) -> dict:
    """Enroll a tenant on multiple devices, pushing stored fingerprint to each."""
    _get_tenant_or_404(tenant_id, db)

    results: list[dict] = []
    succeeded = 0
    failed = 0

    for did in device_ids:
        try:
            result = enroll_to_device(tenant_id, did, db, finger_index=finger_index, performed_by=performed_by)
            results.append({"device_id": did, "success": True, "fingerprint_pushed": result.get("fingerprint_pushed")})
            succeeded += 1
        except HTTPException as exc:
            results.append({"device_id": did, "success": False, "error": exc.detail})
            _log_assignment(tenant_id, did, "enroll", db, performed_by=performed_by, reason=exc.detail, synced=False)
            db.flush()
            failed += 1

    db.commit()
    return {
        "tenant_id": tenant_id,
        "total": len(device_ids),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }


def update_tenant_on_device(
    tenant_id: int,
    device_id: int,
    db: Session,
    performed_by=None,
) -> dict:
    """Re-sync tenant details and fingerprint on a device."""
    tenant = _get_tenant_or_404(tenant_id, db)
    device = _get_device_or_404(device_id, db)
    client = _build_client(device)

    create_resp = _create_user_on_device(client, tenant)

    fingerprint_pushed = False
    credential = _find_fingerprint_credential(tenant_id, db)
    if credential and credential.file_path:
        fp_result = client.import_fingerprint(user_id=str(tenant_id), file_path=credential.file_path)
        if fp_result["success"]:
            fingerprint_pushed = True

    _upsert_mapping(tenant_id, device_id, db, synced=True, fingerprint_pushed=fingerprint_pushed)
    _log_assignment(tenant_id, device_id, "update", db, performed_by=performed_by, synced=True)
    db.commit()

    return {
        "tenant_id": tenant_id,
        "device_id": device_id,
        "user_updated_on_device": True,
        "fingerprint_pushed": fingerprint_pushed,
        "device_response": create_resp["response"],
        "message": "Tenant details synced to device successfully",
    }


def update_tenant_on_devices_bulk(
    tenant_id: int,
    device_ids: list[int],
    db: Session,
    performed_by=None,
) -> dict:
    """Re-sync tenant details on multiple devices."""
    _get_tenant_or_404(tenant_id, db)

    results: list[dict] = []
    succeeded = 0
    failed = 0

    for did in device_ids:
        try:
            update_tenant_on_device(tenant_id, did, db, performed_by=performed_by)
            results.append({"device_id": did, "success": True})
            succeeded += 1
        except HTTPException as exc:
            results.append({"device_id": did, "success": False, "error": exc.detail})
            _log_assignment(tenant_id, did, "update", db, performed_by=performed_by, reason=exc.detail, synced=False)
            db.flush()
            failed += 1

    db.commit()
    return {
        "tenant_id": tenant_id,
        "total": len(device_ids),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }


def unenroll_from_device(
    tenant_id: int,
    device_id: int,
    db: Session,
    performed_by=None,
) -> dict:
    """Remove a tenant from a single device — deletes fingerprint + user on device."""
    tenant = _get_tenant_or_404(tenant_id, db)
    device = _get_device_or_404(device_id, db)
    client = _build_client(device)

    client.delete_fingerprint(str(tenant.tenant_id))
    delete_resp = client.delete_user(str(tenant.tenant_id))

    if not delete_resp["success"]:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Device rejected user deletion: {delete_resp['response']}",
        )

    mapping = (
        db.query(DeviceUserMapping)
        .filter(DeviceUserMapping.tenant_id == tenant_id, DeviceUserMapping.device_id == device_id)
        .first()
    )
    if mapping:
        db.delete(mapping)

    _log_assignment(tenant_id, device_id, "unenroll", db, performed_by=performed_by, synced=True)
    db.commit()

    return {
        "tenant_id": tenant_id,
        "device_id": device_id,
        "removed_from_device": True,
        "message": "Tenant removed from device successfully",
    }


def enroll_new_tenant(
    payload,  # TenantEnrollRequest — imported at call site to avoid circular import
    company_id: UUID,
    db: Session,
    performed_by=None,
) -> dict:
    """
    Atomic single-step tenant creation + fingerprint enrollment.

    The tenant is written to the database ONLY after the fingerprint has been
    successfully captured.  If any step fails the DB transaction is rolled back
    and — if the user was already pushed to the device — the device record is
    cleaned up so no orphan exists.

    Steps:
      1. Validate device reachability.
      2. Insert Tenant row (flushed but NOT committed).
      3. Create user on device.
      4. Trigger fingerprint enrollment mode on device.
      5. Poll until fingerprint is captured (up to capture_wait_seconds).
      6. Persist fingerprint file → Credential → TenantSiteAccess →
         DeviceUserMapping → DeviceAssignmentLog → commit.
      7. Return 200 with tenant + enrollment details.

    On any failure:
      - DB transaction is rolled back (tenant never persisted).
      - User is deleted from the device (best-effort).
      - HTTP error is raised with a clear message.
    """
    # ------------------------------------------------------------------ #
    # Step 1 — Validate device                                            #
    # ------------------------------------------------------------------ #
    device = _get_device_or_404(payload.device_id, db)
    client = _build_client(device)

    # ------------------------------------------------------------------ #
    # Step 2 — Create Tenant row (flush only, no commit)                  #
    # ------------------------------------------------------------------ #
    tenant = Tenant(
        company_id=company_id,
        external_id=payload.external_id,
        full_name=payload.full_name,
        email=payload.email,
        phone=getattr(payload, "phone", None),
        tenant_type=payload.tenant_type,
        is_active=True,
        global_access_from=payload.global_access_from,
        global_access_till=payload.global_access_till,
    )
    db.add(tenant)
    try:
        db.flush()  # assigns tenant_id without committing
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A tenant with the same external_id already exists for this company.",
        )

    user_id = str(tenant.tenant_id)
    user_created_on_device = False

    try:
        # -------------------------------------------------------------- #
        # Step 3 — Create user on device                                  #
        # -------------------------------------------------------------- #
        validity_end = payload.global_access_till.date() if payload.global_access_till else None
        result = client.create_user(
            user_id=user_id,
            name=payload.full_name,
            active=True,
            validity_end_date=validity_end,
        )
        if not result["success"]:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Device rejected user creation: {result['response']}",
            )
        user_created_on_device = True

        # -------------------------------------------------------------- #
        # Step 4 — Trigger fingerprint enrollment mode                    #
        # -------------------------------------------------------------- #
        trigger_result = client.trigger_fingerprint_enrollment(user_id, payload.finger_index)
        if not trigger_result["success"]:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Device could not enter fingerprint enrollment mode. "
                    "Tenant was NOT created — please try again."
                ),
            )

        # -------------------------------------------------------------- #
        # Step 5 — Poll for fingerprint capture                           #
        # -------------------------------------------------------------- #
        _CAPTURE_TIMEOUT_SECONDS = 30
        poll_interval = 3
        elapsed = 0
        template_data: bytes | None = None
        file_path: str | None = None

        while elapsed < _CAPTURE_TIMEOUT_SECONDS:
            time.sleep(poll_interval)
            elapsed += poll_interval
            template_data, file_path = client.extract_fingerprint(user_id, payload.finger_index)
            if template_data and file_path:
                break

        if not template_data or not file_path:
            raise HTTPException(
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
                detail=(
                    f"Fingerprint not captured within {_CAPTURE_TIMEOUT_SECONDS}s. "
                    "Tenant was NOT created — please try again and scan promptly."
                ),
            )

        # -------------------------------------------------------------- #
        # Step 6 — All device steps succeeded: persist to DB              #
        # -------------------------------------------------------------- #

        # Credential (fingerprint template path)
        file_hash = calculate_file_hash(file_path)
        credential = Credential(
            tenant_id=tenant.tenant_id,
            type="finger",
            slot_index=payload.finger_index,
            file_path=file_path,
            file_hash=file_hash,
            algorithm_version="matrix_v1",
        )
        db.add(credential)

        # Site access
        site_access = TenantSiteAccess(
            tenant_id=tenant.tenant_id,
            site_id=payload.site_id,
            valid_from=payload.global_access_from,
            valid_till=payload.global_access_till,
        )
        db.add(site_access)

        # Device mapping
        now = db.query(func.current_timestamp()).scalar()
        mapping = DeviceUserMapping(
            tenant_id=tenant.tenant_id,
            device_id=payload.device_id,
            matrix_user_id=user_id,
            is_synced=True,
            last_sync_at=now,
            last_sync_attempt_at=now,
            sync_attempt_count=1,
            device_response={"fingerprint_pushed": True},
        )
        db.add(mapping)

        # Audit log
        _log_assignment(
            tenant.tenant_id,
            payload.device_id,
            "enroll",
            db,
            performed_by=performed_by,
            synced=True,
        )

        db.flush()
        credential_id = credential.credential_id

        db.commit()
        db.refresh(tenant)

        return {
            "tenant_id": tenant.tenant_id,
            "full_name": tenant.full_name,
            "device_id": payload.device_id,
            "site_id": payload.site_id,
            "fingerprint_stored": True,
            "credential_id": credential_id,
            "message": "Tenant created and fingerprint enrolled successfully.",
        }

    except HTTPException:
        # Roll back every DB change (tenant never committed)
        db.rollback()
        # Best-effort device cleanup so no orphan user is left on the device
        if user_created_on_device:
            try:
                client.delete_fingerprint(user_id)
                client.delete_user(user_id)
            except Exception:
                pass
        raise

    except Exception as exc:
        db.rollback()
        if user_created_on_device:
            try:
                client.delete_fingerprint(user_id)
                client.delete_user(user_id)
            except Exception:
                pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Enrollment failed unexpectedly: {exc}",
        ) from exc


def unenroll_from_devices_bulk(
    tenant_id: int,
    device_ids: list[int],
    db: Session,
    performed_by=None,
) -> dict:
    """Remove a tenant from multiple devices."""
    _get_tenant_or_404(tenant_id, db)

    results: list[dict] = []
    succeeded = 0
    failed = 0

    for did in device_ids:
        try:
            unenroll_from_device(tenant_id, did, db, performed_by=performed_by)
            results.append({"device_id": did, "success": True})
            succeeded += 1
        except HTTPException as exc:
            results.append({"device_id": did, "success": False, "error": exc.detail})
            _log_assignment(tenant_id, did, "unenroll", db, performed_by=performed_by, reason=exc.detail, synced=False)
            db.flush()
            failed += 1

    db.commit()
    return {
        "tenant_id": tenant_id,
        "total": len(device_ids),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }

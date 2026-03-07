from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.services.access.schema import (
    BulkAccessRequest,
    TenantDeviceAccessCreate,
    TenantDeviceAccessUpdate,
    TenantSiteAccessCreate,
    TenantSiteAccessUpdate,
)
from database.models import Device, Site, Tenant, TenantDeviceAccess, TenantSiteAccess


# ==================== SITE ACCESS ====================


def grant_site_access(payload: TenantSiteAccessCreate, db: Session) -> TenantSiteAccess:
    tenant = db.query(Tenant).filter(Tenant.tenant_id == payload.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    site = db.query(Site).filter(Site.site_id == payload.site_id).first()
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found")

    site_access = TenantSiteAccess(
        tenant_id=payload.tenant_id,
        site_id=payload.site_id,
        valid_from=payload.valid_from,
        valid_till=payload.valid_till,
        auto_assign_all_devices=payload.auto_assign_all_devices,
    )
    db.add(site_access)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Site access already exists for this tenant",
        ) from exc
    db.refresh(site_access)

    if payload.auto_assign_all_devices:
        devices = db.query(Device).filter(Device.site_id == payload.site_id).all()
        for device in devices:
            device_access = TenantDeviceAccess(
                tenant_id=payload.tenant_id,
                device_id=device.device_id,
                site_access_id=site_access.site_access_id,
                valid_from=payload.valid_from,
                valid_till=payload.valid_till,
            )
            db.add(device_access)
        db.commit()

    return site_access


def list_site_accesses(
    db: Session,
    tenant_id: int | None = None,
    site_id: int | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[TenantSiteAccess]:
    query = db.query(TenantSiteAccess)
    if tenant_id is not None:
        query = query.filter(TenantSiteAccess.tenant_id == tenant_id)
    if site_id is not None:
        query = query.filter(TenantSiteAccess.site_id == site_id)
    return query.offset(skip).limit(limit).all()


def get_site_access(site_access_id: int, db: Session) -> TenantSiteAccess:
    site_access = db.query(TenantSiteAccess).filter(TenantSiteAccess.site_access_id == site_access_id).first()
    if not site_access:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site access not found")
    return site_access


def update_site_access(site_access_id: int, payload: TenantSiteAccessUpdate, db: Session) -> TenantSiteAccess:
    site_access = get_site_access(site_access_id, db)
    if payload.valid_from is not None:
        site_access.valid_from = payload.valid_from
    if payload.valid_till is not None:
        site_access.valid_till = payload.valid_till
    if payload.auto_assign_all_devices is not None:
        site_access.auto_assign_all_devices = payload.auto_assign_all_devices
    db.commit()
    db.refresh(site_access)
    return site_access


def revoke_site_access(site_access_id: int, db: Session) -> None:
    site_access = get_site_access(site_access_id, db)
    db.query(TenantDeviceAccess).filter(TenantDeviceAccess.site_access_id == site_access_id).delete()
    db.delete(site_access)
    db.commit()


# ==================== DEVICE ACCESS ====================


def grant_device_access(payload: TenantDeviceAccessCreate, db: Session) -> TenantDeviceAccess:
    tenant = db.query(Tenant).filter(Tenant.tenant_id == payload.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    device = db.query(Device).filter(Device.device_id == payload.device_id).first()
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")

    device_access = TenantDeviceAccess(
        tenant_id=payload.tenant_id,
        device_id=payload.device_id,
        site_access_id=payload.site_access_id,
        valid_from=payload.valid_from,
        valid_till=payload.valid_till,
    )
    db.add(device_access)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device access already exists for this tenant",
        ) from exc
    db.refresh(device_access)
    return device_access


def list_device_accesses(
    db: Session,
    tenant_id: int | None = None,
    device_id: int | None = None,
    site_access_id: int | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[TenantDeviceAccess]:
    query = db.query(TenantDeviceAccess)
    if tenant_id is not None:
        query = query.filter(TenantDeviceAccess.tenant_id == tenant_id)
    if device_id is not None:
        query = query.filter(TenantDeviceAccess.device_id == device_id)
    if site_access_id is not None:
        query = query.filter(TenantDeviceAccess.site_access_id == site_access_id)
    return query.offset(skip).limit(limit).all()


def get_device_access(device_access_id: int, db: Session) -> TenantDeviceAccess:
    device_access = (
        db.query(TenantDeviceAccess)
        .filter(TenantDeviceAccess.device_access_id == device_access_id)
        .first()
    )
    if not device_access:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device access not found")
    return device_access


def update_device_access(device_access_id: int, payload: TenantDeviceAccessUpdate, db: Session) -> TenantDeviceAccess:
    device_access = get_device_access(device_access_id, db)
    if payload.valid_from is not None:
        device_access.valid_from = payload.valid_from
    if payload.valid_till is not None:
        device_access.valid_till = payload.valid_till
    db.commit()
    db.refresh(device_access)
    return device_access


def revoke_device_access(device_access_id: int, db: Session) -> None:
    device_access = get_device_access(device_access_id, db)
    db.delete(device_access)
    db.commit()


# ==================== BULK ====================


def grant_bulk_access(payload: BulkAccessRequest, db: Session) -> dict:
    tenant = db.query(Tenant).filter(Tenant.tenant_id == payload.tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    site_accesses_created = 0
    device_accesses_created = 0

    for site_id in payload.site_ids:
        try:
            grant_site_access(TenantSiteAccessCreate(
                tenant_id=payload.tenant_id,
                site_id=site_id,
                valid_from=payload.valid_from,
                valid_till=payload.valid_till,
                auto_assign_all_devices=payload.auto_assign_devices,
            ), db)
            site_accesses_created += 1
        except HTTPException:
            pass

    for device_id in payload.device_ids:
        try:
            grant_device_access(TenantDeviceAccessCreate(
                tenant_id=payload.tenant_id,
                device_id=device_id,
                valid_from=payload.valid_from,
                valid_till=payload.valid_till,
            ), db)
            device_accesses_created += 1
        except HTTPException:
            pass

    return {
        "tenant_id": payload.tenant_id,
        "site_accesses_created": site_accesses_created,
        "device_accesses_created": device_accesses_created,
        "message": "Bulk access granted successfully",
    }

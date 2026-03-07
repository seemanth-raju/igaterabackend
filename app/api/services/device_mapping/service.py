from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.services.device_mapping.schema import SyncStatusUpdate
from database.models import DeviceUserMapping


def list_mappings(
    db: Session,
    tenant_id: int | None = None,
    device_id: int | None = None,
    is_synced: bool | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[DeviceUserMapping]:
    """List device user mappings with filters."""
    query = db.query(DeviceUserMapping)

    if tenant_id is not None:
        query = query.filter(DeviceUserMapping.tenant_id == tenant_id)

    if device_id is not None:
        query = query.filter(DeviceUserMapping.device_id == device_id)

    if is_synced is not None:
        query = query.filter(DeviceUserMapping.is_synced == is_synced)

    return query.order_by(DeviceUserMapping.created_at.desc()).offset(skip).limit(limit).all()


def get_mapping(mapping_id: int, db: Session) -> DeviceUserMapping:
    """Get a specific device user mapping."""
    mapping = db.query(DeviceUserMapping).filter(DeviceUserMapping.mapping_id == mapping_id).first()
    if not mapping:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device user mapping not found")
    return mapping


def get_mapping_by_tenant_device(tenant_id: int, device_id: int, db: Session) -> DeviceUserMapping | None:
    """Get mapping by tenant and device."""
    return (
        db.query(DeviceUserMapping)
        .filter(DeviceUserMapping.tenant_id == tenant_id, DeviceUserMapping.device_id == device_id)
        .first()
    )


def update_sync_status(mapping_id: int, payload: SyncStatusUpdate, db: Session) -> DeviceUserMapping:
    """Update sync status of a mapping."""
    mapping = get_mapping(mapping_id, db)

    mapping.is_synced = payload.is_synced
    mapping.last_sync_attempt_at = func.current_timestamp()

    if payload.is_synced:
        mapping.last_sync_at = func.current_timestamp()
        mapping.sync_error = None
    else:
        mapping.sync_error = payload.sync_error

    if payload.device_response is not None:
        mapping.device_response = payload.device_response

    mapping.sync_attempt_count += 1

    db.commit()
    db.refresh(mapping)
    return mapping


def delete_mapping(mapping_id: int, db: Session) -> None:
    """Delete a device user mapping."""
    mapping = get_mapping(mapping_id, db)
    db.delete(mapping)
    db.commit()


def get_unsynced_mappings(db: Session, limit: int = 50) -> list[DeviceUserMapping]:
    """Get all unsynced mappings for background sync jobs."""
    return db.query(DeviceUserMapping).filter(DeviceUserMapping.is_synced == False).limit(limit).all()

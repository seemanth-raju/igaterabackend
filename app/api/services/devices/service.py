import uuid
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.services.devices.schema import DeviceCreate, DeviceUpdate
from app.core.security import encrypt_password
from database.models import Device, Site


def _resolve_site_id(site_id: int | None, db: Session) -> int | None:
    if site_id is None:
        return None
    site = db.query(Site).filter(Site.site_id == site_id).first()
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found")
    return site.site_id


def create_device(payload: DeviceCreate, company_id: UUID, db: Session) -> Device:
    device_serial_number = payload.device_serial_number
    if not device_serial_number:
        device_serial_number = str(uuid.uuid4())

    # Encrypt the API password before storing
    encrypted_password = encrypt_password(payload.api_password) if payload.api_password else None

    device = Device(
        company_id=company_id,
        site_id=_resolve_site_id(payload.site_id, db),
        device_serial_number=device_serial_number,
        vendor=payload.vendor,
        model_name=payload.model_name,
        ip_address=payload.ip_address,
        mac_address=payload.mac_address,
        api_username=payload.api_username,
        api_password_encrypted=encrypted_password,
        api_port=payload.api_port,
        use_https=payload.use_https,
        status=payload.status,
        config=payload.config,
    )
    db.add(device)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device with same MAC or serial number already exists",
        ) from exc
    db.refresh(device)
    return device


def list_devices(
    db: Session,
    company_id: UUID | None = None,
    site_id: int | None = None,
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
) -> list[Device]:
    query = db.query(Device)

    if company_id is not None:
        query = query.filter(Device.company_id == company_id)

    if site_id is not None:
        query = query.filter(Device.site_id == site_id)

    if search:
        like_value = f"%{search}%"
        query = query.filter(
            Device.device_serial_number.ilike(like_value)
            | Device.vendor.ilike(like_value)
            | Device.model_name.ilike(like_value)
            | Device.ip_address.ilike(like_value)
        )

    return query.order_by(Device.created_at.desc()).offset(skip).limit(limit).all()


def get_device(device_id: int, db: Session) -> Device:
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


def update_device(device_id: int, payload: DeviceUpdate, db: Session) -> Device:
    device = get_device(device_id, db)

    if payload.site_id is not None:
        device.site_id = _resolve_site_id(payload.site_id, db)
    if payload.device_serial_number is not None:
        device.device_serial_number = payload.device_serial_number
    if payload.vendor is not None:
        device.vendor = payload.vendor
    if payload.model_name is not None:
        device.model_name = payload.model_name
    if payload.ip_address is not None:
        device.ip_address = payload.ip_address
    if payload.mac_address is not None:
        device.mac_address = payload.mac_address
    if payload.api_username is not None:
        device.api_username = payload.api_username
    if payload.api_password is not None:
        # Encrypt the new password before storing
        device.api_password_encrypted = encrypt_password(payload.api_password)
    if payload.api_port is not None:
        device.api_port = payload.api_port
    if payload.use_https is not None:
        device.use_https = payload.use_https
    if payload.status is not None:
        device.status = payload.status
    if payload.config is not None:
        device.config = payload.config

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Device with same MAC or serial number already exists",
        ) from exc
    db.refresh(device)
    return device


def delete_device(device_id: int, db: Session) -> None:
    device = get_device(device_id, db)
    db.delete(device)
    db.commit()

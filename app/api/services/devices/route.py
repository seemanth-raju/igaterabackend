from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.services.devices.schema import DeviceCreate, DeviceRead, DeviceUpdate
from app.api.services.devices.service import create_device, delete_device, get_device, list_devices, update_device
from app.services.matrix import MatrixDeviceClient
from app.core.security import decrypt_password
from database.models import AppUser, UserRole
from sqlalchemy import func

router = APIRouter(prefix="/devices", tags=["devices"])


def _resolve_company_id(requested: UUID | None, current_user: AppUser) -> UUID:
    """Super-admins may supply an explicit company_id; everyone else is scoped to their own."""
    if current_user.role == UserRole.super_admin.value and requested is not None:
        return requested
    return current_user.company_id


def _to_device_read(device) -> DeviceRead:
    return DeviceRead(
        device_id=device.device_id,
        company_id=str(device.company_id) if device.company_id else None,
        site_id=device.site_id,
        device_serial_number=device.device_serial_number,
        vendor=device.vendor,
        model_name=device.model_name,
        ip_address=device.ip_address,
        mac_address=device.mac_address,
        api_username=device.api_username,
        api_port=device.api_port,
        use_https=device.use_https,
        status=device.status,
        config=device.config,
        created_at=device.created_at,
    )


def _check_device_access(device, current_user: AppUser) -> None:
    """Raise 403 if the user does not own this device."""
    if current_user.role != UserRole.super_admin.value and device.company_id != current_user.company_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this device")


@router.post("", response_model=DeviceRead)
def create_device_route(
    payload: DeviceCreate,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> DeviceRead:
    device = create_device(payload, _resolve_company_id(payload.company_id, current_user), db)
    return _to_device_read(device)


@router.get("", response_model=list[DeviceRead])
def list_devices_route(
    company_id: UUID | None = Query(default=None),
    site_id: int | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=1000),
    search: str | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> list[DeviceRead]:
    if current_user.role != UserRole.super_admin.value:
        company_id = current_user.company_id

    devices = list_devices(db, company_id=company_id, site_id=site_id, skip=skip, limit=limit, search=search)
    return [_to_device_read(device) for device in devices]


@router.get("/{device_id}", response_model=DeviceRead)
def get_device_route(
    device_id: int,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> DeviceRead:
    device = get_device(device_id, db)
    _check_device_access(device, current_user)
    return _to_device_read(device)


@router.patch("/{device_id}", response_model=DeviceRead)
def update_device_route(
    device_id: int,
    payload: DeviceUpdate,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> DeviceRead:
    device = get_device(device_id, db)
    _check_device_access(device, current_user)
    device = update_device(device_id, payload, db)
    return _to_device_read(device)


@router.post("/{device_id}/ping")
def ping_device_route(
    device_id: int,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> dict:
    """Ping a device to check if it is online. Updates device status and last_heartbeat in DB."""
    device = get_device(device_id, db)
    _check_device_access(device, current_user)

    if not device.ip_address:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Device has no IP address configured")

    client = MatrixDeviceClient(
        device_ip=device.ip_address,
        username=device.api_username or "admin",
        encrypted_password=device.api_password_encrypted or "",
        use_https=device.use_https,
    )
    is_online = client.ping()

    device.status = "online" if is_online else "offline"
    device.last_heartbeat = db.query(func.current_timestamp()).scalar()
    db.commit()

    return {
        "device_id": device_id,
        "ip_address": device.ip_address,
        "online": is_online,
        "status": device.status,
        "last_heartbeat": device.last_heartbeat,
    }


@router.delete("/{device_id}")
def delete_device_route(
    device_id: int,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> dict[str, str]:
    device = get_device(device_id, db)
    _check_device_access(device, current_user)
    delete_device(device_id, db)
    return {"message": "Device deleted"}

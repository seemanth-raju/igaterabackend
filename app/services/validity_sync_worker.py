"""
Background worker: activate tenants on devices when their access start time arrives.

The Matrix device natively enforces the end date (validity-date set at enrollment),
so we only need to handle the start-date side: when global_access_from passes, flip
user-active from 0 → 1 on all enrolled devices.

Runs every N seconds (same interval as log sync).
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.services.matrix import MatrixDeviceClient
from database.models import Device, DeviceUserMapping, Tenant
from database.session import SessionLocal

log = logging.getLogger(__name__)


def _activate_newly_valid_tenants(interval_seconds: int) -> tuple[int, int]:
    """
    Find tenants whose global_access_from just passed in the last poll window
    and activate them (user-active=1) on all their enrolled devices.

    Returns (activated_count, failed_count).
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(seconds=interval_seconds)

        # Tenants whose start window just opened in the last poll interval
        newly_valid = (
            db.query(Tenant)
            .filter(
                Tenant.global_access_from >= window_start,
                Tenant.global_access_from <= now,
                Tenant.is_active.is_(True),
                Tenant.is_access_enabled.is_(True),
            )
            .all()
        )

        if not newly_valid:
            return 0, 0

        activated = 0
        failed = 0

        for tenant in newly_valid:
            mappings = (
                db.query(DeviceUserMapping)
                .filter(DeviceUserMapping.tenant_id == tenant.tenant_id)
                .all()
            )
            device_ids = [m.device_id for m in mappings]
            if not device_ids:
                continue

            devices = (
                db.query(Device)
                .filter(
                    Device.device_id.in_(device_ids),
                    Device.ip_address.isnot(None),
                )
                .all()
            )

            validity_end = tenant.global_access_till.date() if tenant.global_access_till else None

            for device in devices:
                try:
                    client = MatrixDeviceClient(
                        device_ip=device.ip_address,
                        username=device.api_username or "admin",
                        encrypted_password=device.api_password_encrypted or "",
                        use_https=device.use_https,
                    )
                    result = client.create_user(
                        user_id=str(tenant.tenant_id),
                        name=tenant.full_name,
                        active=True,
                        validity_end_date=validity_end,
                    )
                    if result["success"]:
                        log.info(
                            "Activated tenant %d on device %d (access_from=%s)",
                            tenant.tenant_id, device.device_id, tenant.global_access_from,
                        )
                        activated += 1
                    else:
                        log.warning(
                            "Device %d rejected activation for tenant %d: %s",
                            device.device_id, tenant.tenant_id, result["response"],
                        )
                        failed += 1
                except Exception:
                    log.exception(
                        "Error activating tenant %d on device %d",
                        tenant.tenant_id, device.device_id,
                    )
                    failed += 1

        return activated, failed
    finally:
        db.close()


async def run_validity_sync_loop(interval_seconds: int = 60) -> None:
    """
    Infinite loop: check for start-date arrivals every `interval_seconds` seconds.
    End dates are enforced natively by the device via validity-date set at enrollment.
    Cancelled cleanly when the FastAPI app shuts down.
    """
    log.info("Validity sync worker started (interval: %ds)", interval_seconds)
    while True:
        try:
            loop = asyncio.get_event_loop()
            activated, failed = await loop.run_in_executor(
                None, _activate_newly_valid_tenants, interval_seconds
            )
            if activated or failed:
                log.info("Validity sync — activated: %d  failed: %d", activated, failed)
        except Exception:
            log.exception("Validity sync worker error")

        await asyncio.sleep(interval_seconds)

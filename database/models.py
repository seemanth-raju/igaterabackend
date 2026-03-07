import enum
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.base import Base


class AuthMethod(enum.Enum):
    face = "face"
    finger = "finger"
    card = "card"
    pin = "pin"
    palm = "palm"


class SyncStatus(enum.Enum):
    pending = "pending"
    synced = "synced"
    failed = "failed"
    partial = "partial"


class UserRole(enum.Enum):
    super_admin = "super_admin"
    company_admin = "company_admin"
    staff = "staff"
    viewer = "viewer"


class Company(Base):
    __tablename__ = "company"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(100), unique=True)
    primary_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    secondary_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())

    users: Mapped[list["AppUser"]] = relationship(back_populates="company")
    sites: Mapped[list["Site"]] = relationship(back_populates="company")
    devices: Mapped[list["Device"]] = relationship(back_populates="company")


class AppUser(Base):
    __tablename__ = "app_user"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.company_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=UserRole.staff.value,
        server_default=text("'staff'"),
    )
    username: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    last_login: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())

    company: Mapped["Company"] = relationship(back_populates="users")
    tokens: Mapped[list["AuthToken"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AuthToken(Base):
    __tablename__ = "auth_token"
    __table_args__ = (Index("idx_refresh_token", "refresh_token"),)

    token_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.user_id", ondelete="CASCADE"), nullable=False)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())

    user: Mapped["AppUser"] = relationship(back_populates="tokens")


class Site(Base):
    __tablename__ = "site"

    site_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.company_id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), server_default=text("'UTC'"))
    address: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())

    company: Mapped["Company"] = relationship(back_populates="sites")
    devices: Mapped[list["Device"]] = relationship(back_populates="site")


class Device(Base):
    __tablename__ = "device"

    device_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.company_id", ondelete="CASCADE"),
        nullable=False,
    )
    site_id: Mapped[int | None] = mapped_column(ForeignKey("site.site_id", ondelete="CASCADE"))
    device_serial_number: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    vendor: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(100))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    mac_address: Mapped[str | None] = mapped_column(String(17), unique=True)
    api_username: Mapped[str | None] = mapped_column(String(100))
    api_password_encrypted: Mapped[str | None] = mapped_column(Text)
    api_port: Mapped[int] = mapped_column(Integer, server_default=text("80"))
    use_https: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'offline'"))
    last_heartbeat: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    config: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())

    company: Mapped["Company"] = relationship(back_populates="devices")
    site: Mapped["Site"] = relationship(back_populates="devices")


class Tenant(Base):
    __tablename__ = "tenant"
    __table_args__ = (
        UniqueConstraint("company_id", "external_id", name="tenant_company_id_external_id_key"),
        Index("idx_tenant_lookup", "company_id", "external_id"),
        Index("idx_tenant_global_validity", "global_access_from", "global_access_till", "is_access_enabled"),
        Index("idx_tenant_type", "tenant_type"),
    )

    tenant_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company.company_id", ondelete="CASCADE"),
    )
    external_id: Mapped[str | None] = mapped_column(String(50))
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))

    # Global access control
    global_access_from: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    global_access_till: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    is_access_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    access_timezone: Mapped[str] = mapped_column(String(50), server_default=text("'UTC'"))
    tenant_type: Mapped[str] = mapped_column(String(50), server_default=text("'employee'"))
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())


class Credential(Base):
    """Stores biometric credential templates extracted from a device (for cross-device push)."""

    __tablename__ = "credential"

    credential_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenant.tenant_id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # finger, face, card, pin
    slot_index: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    file_path: Mapped[str | None] = mapped_column(Text)
    file_hash: Mapped[str | None] = mapped_column(String(64))
    raw_value: Mapped[str | None] = mapped_column(Text)
    algorithm_version: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())


class AccessEvent(Base):
    __tablename__ = "access_event"
    __table_args__ = (
        Index("idx_event_time", "event_time"),
        Index("idx_event_device", "device_id"),
        Index("idx_event_tenant", "tenant_id"),
        Index("idx_event_company", "company_id"),
        Index("idx_event_granted", "access_granted"),
        UniqueConstraint("device_id", "device_seq_number", "device_rollover_count", name="uq_event_device_seq"),
    )

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("company.company_id", ondelete="SET NULL")
    )
    device_id: Mapped[int | None] = mapped_column(ForeignKey("device.device_id", ondelete="SET NULL"))
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenant.tenant_id", ondelete="SET NULL"))
    device_seq_number: Mapped[int | None] = mapped_column(Integer)
    device_rollover_count: Mapped[int | None] = mapped_column(Integer)
    cosec_event_id: Mapped[int | None] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(30), server_default=text("'unknown'"))
    event_time: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), server_default=text("'IN'"))
    auth_used: Mapped[str | None] = mapped_column(String(20))
    access_granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    temperature: Mapped[float | None] = mapped_column(Numeric(4, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())


class TenantSiteAccess(Base):
    """Controls tenant access to specific sites."""

    __tablename__ = "tenant_site_access"
    __table_args__ = (
        UniqueConstraint("tenant_id", "site_id", name="uq_tenant_site_access"),
        Index("idx_tsa_tenant", "tenant_id"),
        Index("idx_tsa_site", "site_id"),
    )

    site_access_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False)
    site_id: Mapped[int] = mapped_column(ForeignKey("site.site_id", ondelete="CASCADE"), nullable=False)
    valid_from: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    valid_till: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    auto_assign_all_devices: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class TenantDeviceAccess(Base):
    """Controls tenant access to specific devices."""

    __tablename__ = "tenant_device_access"
    __table_args__ = (
        UniqueConstraint("tenant_id", "device_id", name="uq_tenant_device_access"),
        Index("idx_tda_tenant", "tenant_id"),
        Index("idx_tda_device", "device_id"),
        Index("idx_tda_site_access", "site_access_id"),
    )

    device_access_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False)
    device_id: Mapped[int] = mapped_column(ForeignKey("device.device_id", ondelete="CASCADE"), nullable=False)
    site_access_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenant_site_access.site_access_id", ondelete="CASCADE")
    )
    valid_from: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    valid_till: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))


class DeviceUserMapping(Base):
    """Maps tenants to Matrix device user IDs — tracks enrollment state."""

    __tablename__ = "device_user_mapping"
    __table_args__ = (
        UniqueConstraint("tenant_id", "device_id", name="unique_tenant_device_mapping"),
        UniqueConstraint("device_id", "matrix_user_id", name="unique_matrix_user_per_device"),
        Index("idx_mdm_tenant", "tenant_id"),
        Index("idx_mdm_device", "device_id"),
        Index("idx_mdm_matrix_id", "matrix_user_id"),
        Index("idx_mdm_not_synced", "is_synced"),
    )

    mapping_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False)
    device_id: Mapped[int] = mapped_column(ForeignKey("device.device_id", ondelete="CASCADE"), nullable=False)
    matrix_user_id: Mapped[str] = mapped_column(String(50), nullable=False)
    valid_from: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    valid_till: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    is_synced: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    last_sync_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    last_sync_attempt_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    sync_attempt_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    sync_error: Mapped[str | None] = mapped_column(Text)
    device_response: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())


class DeviceAssignmentLog(Base):
    """Audit trail of device enrollment/unenrollment actions."""

    __tablename__ = "device_assignment_log"
    __table_args__ = (
        Index("idx_dal_tenant", "tenant_id"),
        Index("idx_dal_device", "device_id"),
        Index("idx_dal_time", "performed_at"),
        Index("idx_dal_action", "action"),
    )

    assignment_log_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False)
    device_id: Mapped[int] = mapped_column(ForeignKey("device.device_id", ondelete="CASCADE"), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    performed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.user_id", ondelete="SET NULL")
    )
    performed_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.current_timestamp())
    reason: Mapped[str | None] = mapped_column(Text)
    synced_to_device: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    sync_error: Mapped[str | None] = mapped_column(Text)

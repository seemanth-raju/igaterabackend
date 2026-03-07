from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field




class DeviceCreate(BaseModel):
    company_id: UUID | None = Field(
        default=None,
        description="Target company UUID. **Super-admin only** — ignored for all other roles.",
        examples=["a1b2c3d4-e5f6-7890-abcd-ef1234567890"],
    )
    site_id: int | None = None
    device_serial_number: str | None = Field(default=None, max_length=100)
    vendor: str = Field(..., min_length=2, max_length=50)
    model_name: str | None = Field(default=None, max_length=100)
    ip_address: str | None = Field(default=None, max_length=45)
    mac_address: str | None = Field(default=None, max_length=17)
    api_username: str | None = Field(default=None, max_length=100)
    api_password: str | None = None
    api_port: int = Field(default=80, ge=1, le=65535)
    use_https: bool = False
    status: str = Field(default="offline", max_length=20)
    config: dict = Field(default_factory=dict)


class DeviceUpdate(BaseModel):
    site_id: int | None = None
    device_serial_number: str | None = Field(default=None, min_length=1, max_length=100)
    vendor: str | None = Field(default=None, min_length=2, max_length=50)
    model_name: str | None = Field(default=None, max_length=100)
    ip_address: str | None = Field(default=None, max_length=45)
    mac_address: str | None = Field(default=None, max_length=17)
    api_username: str | None = Field(default=None, max_length=100)
    api_password: str | None = None
    api_port: int | None = Field(default=None, ge=1, le=65535)
    use_https: bool | None = None
    status: str | None = Field(default=None, max_length=20)
    config: dict | None = None


class DeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: int
    company_id: str
    site_id: int | None
    device_serial_number: str
    vendor: str
    model_name: str | None
    ip_address: str | None
    mac_address: str | None
    api_username: str | None
    api_port: int
    use_https: bool
    status: str
    config: dict
    created_at: datetime

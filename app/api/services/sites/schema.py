from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field



class SiteCreate(BaseModel):
    company_id: UUID | None = Field(
        default=None,
        description="Target company UUID. **Super-admin only** — ignored for all other roles.",
        examples=["a1b2c3d4-e5f6-7890-abcd-ef1234567890"],
    )
    name: str = Field(..., min_length=2, max_length=255)
    timezone: str = Field(default="UTC", max_length=50)
    address: str | None = None


class SiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    timezone: str | None = Field(default=None, max_length=50)
    address: str | None = None


class SiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    site_id: int
    company_id: str
    name: str
    timezone: str
    address: str | None
    created_at: datetime

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TenantCreate(BaseModel):
    company_id: UUID | None = Field(
        default=None,
        description="Super-admin only. Target company for this tenant. Ignored for non-super-admin users.",
    )
    external_id: str | None = Field(default=None, max_length=50)
    full_name: str = Field(..., min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    tenant_type: str = Field(default="employee", max_length=50)
    is_active: bool = True
    global_access_from: datetime | None = None
    global_access_till: datetime | None = None

    # Optional: register user on their enrollment device at creation time.
    # The API will create the user on the device, trigger fingerprint enrollment
    # mode, wait up to capture_wait_seconds, then auto-extract and store the template.
    registration_device_id: int | None = Field(
        default=None,
        description="Device ID to enroll the user on immediately after creation. Triggers fingerprint capture automatically.",
    )
    finger_index: int = Field(default=1, ge=1, le=10)
    capture_wait_seconds: int = Field(
        default=30,
        ge=5,
        le=120,
        description="Seconds to wait for the user to scan their finger after enrollment mode is triggered. Only used when registration_device_id is set.",
    )


class TenantUpdate(BaseModel):
    external_id: str | None = Field(default=None, max_length=50)
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    tenant_type: str | None = None
    is_active: bool | None = None
    is_access_enabled: bool | None = Field(default=None, description="Master access switch")
    global_access_from: datetime | None = None
    global_access_till: datetime | None = None


class TenantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: int
    company_id: str | None
    external_id: str | None
    full_name: str
    email: str | None
    phone: str | None
    tenant_type: str
    is_active: bool
    is_access_enabled: bool
    global_access_from: datetime | None
    global_access_till: datetime | None
    access_timezone: str
    created_at: datetime


class TenantCreateResponse(BaseModel):
    """Returned by POST /tenants — includes tenant data plus optional enrollment result."""
    tenant: TenantRead
    enrollment: dict | None = Field(
        default=None,
        description="Present when registration_device_id was supplied; contains fingerprint capture result",
    )


class TenantEnrollRequest(BaseModel):
    """
    Request body for **POST /tenants/enroll**.

    Submit this to atomically create a tenant and capture their fingerprint
    in one step. The tenant is saved to the database **only** after the
    fingerprint is successfully captured from the device. If any step fails
    the request is aborted, the device user is cleaned up, and no record is
    left in the database.

    ### Required fields
    - `full_name`, `global_access_till`, `site_id`, `device_id`

    ### Flow triggered on the backend
    1. User record is created on the selected device.
    2. Device enters fingerprint enrollment mode.
    3. Backend polls the device for up to 30 seconds waiting for the user to scan.
    4. On success — fingerprint template is saved and the tenant is committed to DB.
    5. On any failure — everything is rolled back, no tenant is created.
    """

    company_id: UUID | None = Field(
        default=None,
        description=(
            "**Super-admin only.** The company to create this tenant under. "
            "If omitted the tenant is created under the authenticated user's own company. "
            "Ignored for non-super-admin users."
        ),
        examples=["3fa85f64-5717-4562-b3fc-2c963f66afa6"],
    )

    full_name: str = Field(
        ...,
        min_length=1,
        max_length=15,
        description=(
            "Tenant display name. **Max 15 characters** — this is a hard limit "
            "enforced by the Matrix device hardware. Submitting more than 15 characters "
            "will return a 422 validation error before any device call is made."
        ),
        examples=["John Doe"],
    )
    email: str | None = Field(
        default=None,
        max_length=255,
        description="Optional email address. Stored in the database only; not sent to the device.",
        examples=["john.doe@example.com"],
    )
    phone: str | None = Field(
        default=None,
        max_length=50,
        description="Optional phone number. Stored in the database only; not sent to the device.",
        examples=["9876543210"],
    )
    external_id: str | None = Field(
        default=None,
        max_length=50,
        description=(
            "Your internal identifier for this person (e.g. employee ID, badge number). "
            "Must be unique per company. If a duplicate is submitted the request returns 400."
        ),
        examples=["EMP-001"],
    )
    tenant_type: str = Field(
        default="employee",
        max_length=50,
        description='Role/category of the tenant. Common values: `"employee"`, `"visitor"`, `"contractor"`.',
        examples=["employee"],
    )
    global_access_from: datetime | None = Field(
        default=None,
        description=(
            "Optional ISO-8601 datetime from which the tenant's access is valid. "
            "If omitted, access starts immediately. Send with timezone offset or as UTC (`Z`)."
        ),
        examples=["2026-03-01T00:00:00Z"],
    )
    global_access_till: datetime = Field(
        ...,
        description=(
            "**Required.** ISO-8601 datetime when the tenant's access expires. "
            "This date is also programmed into the device so the hardware "
            "automatically denies access after this point."
        ),
        examples=["2027-03-01T00:00:00Z"],
    )
    site_id: int = Field(
        ...,
        description=(
            "ID of the site to grant the tenant access to. "
            "Obtain available site IDs from **GET /sites**."
        ),
        examples=[3],
    )
    device_id: int = Field(
        ...,
        description=(
            "ID of the Matrix biometric device where the fingerprint will be captured. "
            "The user must be physically present at this device when the request is made. "
            "Obtain available device IDs from **GET /devices**."
        ),
        examples=[7],
    )
    finger_index: int = Field(
        default=1,
        ge=1,
        le=10,
        description=(
            "Which finger to enroll (1 = right thumb, 2 = right index, … 6 = left thumb, etc.). "
            "Defaults to `1`. Most deployments only use `1`."
        ),
        examples=[1],
    )


class TenantEnrollResponse(BaseModel):
    """
    Success response for **POST /tenants/enroll**.

    Returned only when the entire enrollment succeeded — tenant is in the
    database and the fingerprint template is stored.
    """

    tenant_id: int = Field(description="Auto-generated database ID for the newly created tenant.")
    full_name: str = Field(description="Name as saved in the database.")
    device_id: int = Field(description="ID of the device where the fingerprint was captured.")
    site_id: int = Field(description="ID of the site the tenant has been granted access to.")
    fingerprint_stored: bool = Field(
        description="Always `true` on a 200 response — fingerprint template is saved to storage."
    )
    credential_id: int = Field(
        description="Database ID of the stored fingerprint credential. Use this to push the fingerprint to additional devices later."
    )
    message: str = Field(description="Human-readable confirmation message.")

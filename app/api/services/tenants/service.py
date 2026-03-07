from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.services.tenants.schema import TenantCreate, TenantUpdate
from database.models import Tenant


def create_tenant(payload: TenantCreate, company_id: UUID, db: Session) -> Tenant:
    tenant = Tenant(
        company_id=company_id,
        external_id=payload.external_id,
        full_name=payload.full_name,
        email=payload.email,
        phone=payload.phone,
        tenant_type=payload.tenant_type,
        is_active=payload.is_active,
        global_access_from=payload.global_access_from,
        global_access_till=payload.global_access_till,
    )

    db.add(tenant)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant with same external_id already exists for this company",
        ) from exc
    db.refresh(tenant)
    return tenant


def list_tenants(
    db: Session,
    company_id: UUID | None = None,
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
) -> list[Tenant]:
    query = db.query(Tenant)

    if company_id is not None:
        query = query.filter(Tenant.company_id == company_id)

    if search:
        like_value = f"%{search}%"
        query = query.filter(Tenant.full_name.ilike(like_value))

    return query.order_by(Tenant.created_at.desc()).offset(skip).limit(limit).all()


def get_tenant(tenant_id: int, db: Session) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


def update_tenant(tenant_id: int, payload: TenantUpdate, db: Session) -> Tenant:
    tenant = get_tenant(tenant_id, db)

    if payload.external_id is not None:
        tenant.external_id = payload.external_id
    if payload.full_name is not None:
        tenant.full_name = payload.full_name
    if payload.email is not None:
        tenant.email = payload.email
    if payload.phone is not None:
        tenant.phone = payload.phone
    if payload.tenant_type is not None:
        tenant.tenant_type = payload.tenant_type
    if payload.is_active is not None:
        tenant.is_active = payload.is_active
    if payload.is_access_enabled is not None:
        tenant.is_access_enabled = payload.is_access_enabled
    if payload.global_access_from is not None:
        tenant.global_access_from = payload.global_access_from
    if payload.global_access_till is not None:
        tenant.global_access_till = payload.global_access_till

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant with same external_id already exists for this company",
        ) from exc
    db.refresh(tenant)
    return tenant


def delete_tenant(tenant_id: int, db: Session) -> None:
    tenant = get_tenant(tenant_id, db)
    db.delete(tenant)
    db.commit()

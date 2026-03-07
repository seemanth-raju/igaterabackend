from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.services.companies.schema import CompanyCreate, CompanyUpdate
from database.models import Company


def create_company(payload: CompanyCreate, db: Session) -> Company:
    if payload.domain:
        existing_domain = db.query(Company).filter(Company.domain == payload.domain).first()
        if existing_domain:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Domain already exists")

    company = Company(
        name=payload.name,
        domain=payload.domain,
        primary_email=str(payload.primary_email) if payload.primary_email else None,
        secondary_email=str(payload.secondary_email) if payload.secondary_email else None,
        is_active=payload.is_active,
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def list_companies(db: Session, skip: int = 0, limit: int = 50, search: str | None = None) -> list[Company]:
    query = db.query(Company)
    if search:
        like_value = f"%{search}%"
        query = query.filter(or_(Company.name.ilike(like_value), Company.domain.ilike(like_value)))

    return query.order_by(Company.created_at.desc()).offset(skip).limit(limit).all()


def get_company(company_id: UUID, db: Session) -> Company:
    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return company


def update_company(company_id: UUID, payload: CompanyUpdate, db: Session) -> Company:
    company = get_company(company_id, db)

    if payload.domain is not None:
        existing_domain = (
            db.query(Company)
            .filter(Company.domain == payload.domain, Company.company_id != company_id)
            .first()
        )
        if existing_domain:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Domain already exists")

    if payload.name is not None:
        company.name = payload.name
    if payload.domain is not None:
        company.domain = payload.domain
    if payload.primary_email is not None:
        company.primary_email = str(payload.primary_email)
    if payload.secondary_email is not None:
        company.secondary_email = str(payload.secondary_email)
    if payload.is_active is not None:
        company.is_active = payload.is_active

    company.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(company)
    return company


def deactivate_company(company_id: UUID, db: Session) -> None:
    company = get_company(company_id, db)
    company.is_active = False
    company.updated_at = datetime.now(UTC)
    db.commit()

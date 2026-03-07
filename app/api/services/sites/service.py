
import uuid
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.api.services.sites.schema import SiteCreate, SiteUpdate
from database.models import Site


def create_site(payload: SiteCreate, company_id: UUID, db: Session) -> Site:
    site = Site(
        company_id=company_id,
        name=payload.name,
        timezone=payload.timezone,
        address=payload.address,
    )
    db.add(site)
    db.commit()
    db.refresh(site)
    return site


def list_sites(db: Session, company_id: UUID | None = None, skip: int = 0, limit: int = 50) -> list[Site]:
    query = db.query(Site)
    if company_id is not None:
        query = query.filter(Site.company_id == company_id)
    return query.order_by(Site.created_at.desc()).offset(skip).limit(limit).all()


def get_site(site_id: int, db: Session) -> Site:
    site = db.query(Site).filter(Site.site_id == site_id).first()
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Site not found")
    return site


def update_site(site_id: int, payload: SiteUpdate, db: Session) -> Site:
    site = get_site(site_id, db)

    if payload.name is not None:
        site.name = payload.name
    if payload.timezone is not None:
        site.timezone = payload.timezone
    if payload.address is not None:
        site.address = payload.address

    db.commit()
    db.refresh(site)
    return site


def delete_site(site_id: int, db: Session) -> None:
    site = get_site(site_id, db)
    db.delete(site)
    db.commit()

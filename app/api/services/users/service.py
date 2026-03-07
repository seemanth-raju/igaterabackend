from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.services.users.schema import UserCreate, UserUpdate
from app.core.security import hash_password
from database.models import AppUser, Company, UserRole

# Which roles each role is allowed to create
_ALLOWED_TO_CREATE: dict[str, set[str]] = {
    UserRole.super_admin.value: {r.value for r in UserRole},
    UserRole.company_admin.value: {UserRole.staff.value, UserRole.viewer.value},
    UserRole.staff.value: set(),
    UserRole.viewer.value: set(),
}


def create_user(payload: UserCreate, current_user: AppUser, db: Session) -> AppUser:
    # Role-based creation guard
    target_role = payload.role.value
    allowed = _ALLOWED_TO_CREATE.get(current_user.role, set())
    if target_role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Your role ({current_user.role}) is not allowed to create '{target_role}' users",
        )

    # Company scoping: company_admin is always locked to their own company
    if current_user.role == UserRole.company_admin.value:
        company_id = current_user.company_id
    else:
        if not payload.company_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="company_id is required for super_admin")
        company_id = payload.company_id

    company = db.query(Company).filter(Company.company_id == company_id).first()
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    # Username uniqueness (case-insensitive)
    existing = db.query(AppUser).filter(func.lower(AppUser.username) == payload.username.lower()).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Username '{payload.username}' is already taken")

    user = AppUser(
        company_id=company_id,
        role=target_role,
        username=payload.username,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def list_users(db: Session, current_user: AppUser, skip: int = 0, limit: int = 50) -> list[AppUser]:
    q = db.query(AppUser)
    if current_user.role != UserRole.super_admin.value:
        q = q.filter(AppUser.company_id == current_user.company_id)
    return q.order_by(AppUser.created_at.desc()).offset(skip).limit(limit).all()


def get_user(user_id: str, db: Session) -> AppUser:
    user = db.query(AppUser).filter(AppUser.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


def update_user(user_id: str, payload: UserUpdate, db: Session) -> AppUser:
    user = get_user(user_id, db)

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.role is not None:
        user.role = payload.role.value
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.password:
        user.password_hash = hash_password(payload.password)

    db.commit()
    db.refresh(user)
    return user


def deactivate_user(user_id: str, db: Session) -> None:
    user = get_user(user_id, db)
    user.is_active = False
    db.commit()

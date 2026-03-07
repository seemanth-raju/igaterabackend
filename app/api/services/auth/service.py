from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.services.auth.schema import LoginRequest, RefreshRequest, TokenResponse, UserRegister
from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token, hash_password, verify_password
from database.models import AppUser, AuthToken, Company


def register_user(payload: UserRegister, db: Session) -> AppUser:
    company = db.query(Company).filter(Company.company_id == payload.company_id).first()
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    user = AppUser(
        company_id=company.company_id,
        role=payload.role.value,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _issue_tokens(user: AppUser, db: Session) -> TokenResponse:
    access_token = create_access_token(str(user.user_id))
    refresh_token = create_refresh_token(str(user.user_id))
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)

    token_row = AuthToken(
        user_id=user.user_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        revoked=False,
    )
    db.add(token_row)
    db.commit()

    return TokenResponse(access_token=access_token, refresh_token=refresh_token, expires_at=expires_at)


def login(payload: LoginRequest, db: Session) -> TokenResponse:
    identifier = payload.username.strip().lower()
    user = db.query(AppUser).filter(func.lower(AppUser.username) == identifier).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")

    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user.password_hash.startswith("$2"):
        user.password_hash = hash_password(payload.password)

    user.last_login = datetime.now(UTC)
    db.commit()

    return _issue_tokens(user, db)


def refresh_tokens(payload: RefreshRequest, db: Session) -> TokenResponse:
    token_row = db.query(AuthToken).filter(AuthToken.refresh_token == payload.refresh_token, AuthToken.revoked.is_(False)).first()
    if not token_row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    if token_row.expires_at <= datetime.now(UTC):
        token_row.revoked = True
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")

    user = db.query(AppUser).filter(AppUser.user_id == token_row.user_id, AppUser.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    token_row.revoked = True
    db.commit()

    return _issue_tokens(user, db)


def logout(access_token: str, db: Session) -> None:
    token_row = db.query(AuthToken).filter(AuthToken.access_token == access_token).first()
    if token_row:
        token_row.revoked = True
        db.commit()

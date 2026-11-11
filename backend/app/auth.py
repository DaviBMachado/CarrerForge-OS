import os
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session, select

from backend.app.db import get_db
from backend.app.models import User

_DEFAULT_EMAIL = "default@careerforge.local"


def is_auth_required() -> bool:
    return os.getenv("CAREERFORGE_REQUIRE_AUTH", "false").strip().lower() in (
        "true",
        "1",
        "yes",
    )


def ensure_default_user(session: Session) -> User:
    user = session.exec(select(User).where(User.email == _DEFAULT_EMAIL)).first()
    if not user:
        user = User(email=_DEFAULT_EMAIL)
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


async def get_current_user(
    session: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> User:
    # 1. Check explicit X-User-Id header
    if x_user_id:
        try:
            target_id = int(x_user_id)
            user = session.get(User, target_id)
            if not user:
                email = _DEFAULT_EMAIL if target_id == 1 else f"user_{target_id}@careerforge.local"
                user = User(id=target_id, email=email)
                session.add(user)
                session.commit()
                session.refresh(user)
            return user
        except ValueError:
            pass
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user identifier in X-User-Id",
        )

    # 2. Check Authorization Bearer header
    if authorization:
        raw_token = authorization.strip()
        if raw_token.lower().startswith("bearer "):
            token = raw_token[7:].strip()
        else:
            token = raw_token

        target_id: int | None = None
        if token.isdigit():
            target_id = int(token)
        elif token.startswith("user_") and token[5:].isdigit():
            target_id = int(token[5:])

        if target_id is not None:
            user = session.get(User, target_id)
            if not user:
                email = _DEFAULT_EMAIL if target_id == 1 else f"user_{target_id}@careerforge.local"
                user = User(id=target_id, email=email)
                session.add(user)
                session.commit()
                session.refresh(user)
            return user

        if "@" in token:
            user = session.exec(select(User).where(User.email == token)).first()
            if user:
                return user

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
        )

    # 3. No credentials provided: check whether auth is strictly required
    if is_auth_required():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    # 4. Transparent local desktop auto-provisioning
    return ensure_default_user(session)


CurrentUser = Annotated[User, Depends(get_current_user)]

__all__ = [
    "CurrentUser",
    "ensure_default_user",
    "get_current_user",
    "is_auth_required",
]

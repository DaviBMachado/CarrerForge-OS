from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session

from backend.app.auth import CurrentUser
from backend.app.db import get_db
from backend.app.schemas.settings import SettingsRead, SettingsUpdate
from backend.app.services.settings import get_settings, update_settings

router = APIRouter(prefix="/settings", tags=["settings"])

DBSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=SettingsRead)
def read_settings(session: DBSession, current_user: CurrentUser) -> SettingsRead:
    assert current_user.id is not None
    return get_settings(session, user_id=current_user.id)


@router.post("", response_model=SettingsRead)
def save_settings(
    payload: SettingsUpdate, session: DBSession, current_user: CurrentUser
) -> SettingsRead:
    assert current_user.id is not None
    return update_settings(session, payload, user_id=current_user.id)

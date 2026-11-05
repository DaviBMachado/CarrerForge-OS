from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session

from backend.app.db import get_db
from backend.app.schemas.settings import SettingsRead, SettingsUpdate
from backend.app.services.settings import get_settings, update_settings

router = APIRouter(prefix="/settings", tags=["settings"])

DBSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=SettingsRead)
def read_settings(session: DBSession) -> SettingsRead:
    return get_settings(session)


@router.post("", response_model=SettingsRead)
def save_settings(payload: SettingsUpdate, session: DBSession) -> SettingsRead:
    return update_settings(session, payload)

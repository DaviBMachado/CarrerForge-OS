from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends
from sqlmodel import Session

from backend.app.auth import CurrentUser
from backend.app.db import get_db
from backend.app.schemas.settings import SettingsRead, SettingsUpdate
from backend.app.services.llm_orchestrator import (
    LLMOrchestrator,
    LLMProvider,
    fetch_provider_models,
)
from backend.app.services.settings import (
    SETTING_KEYS,
    get_raw_setting,
    get_settings,
    is_masked_indicator,
    update_settings,
)

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


@router.get("/models")
def list_models(
    provider: str,
    session: DBSession,
    current_user: CurrentUser,
    api_key: str | None = None,
) -> dict[str, Any]:
    assert current_user.id is not None
    effective_key = api_key
    if not effective_key or is_masked_indicator(effective_key):
        key_attr = f"{provider}_api_key" if provider != "claude" else "anthropic_api_key"
        db_key_name = SETTING_KEYS.get(key_attr)
        if db_key_name:
            effective_key = get_raw_setting(session, db_key_name, user_id=current_user.id)
    base_url = get_raw_setting(session, SETTING_KEYS["ollama_base_url"], user_id=current_user.id)
    models = fetch_provider_models(provider, api_key=effective_key, base_url=base_url)
    default_model = LLMOrchestrator.DEFAULT_MODELS.get(
        cast(LLMProvider, provider), models[0] if models else ""
    )
    return {"models": models, "default_model": default_model}

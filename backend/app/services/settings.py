from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from backend.app.models import SystemSetting, User
from backend.app.schemas.settings import SettingsRead, SettingsUpdate

# Keys tracked by settings service
SETTING_KEYS = {
    "active_provider": "llm_active_provider",
    "selected_model": "llm_selected_model",
    "gemini_api_key": "gemini_api_key",
    "openai_api_key": "openai_api_key",
    "anthropic_api_key": "anthropic_api_key",
    "openrouter_api_key": "openrouter_api_key",
    "ollama_base_url": "ollama_base_url",
    "scraper_headless": "scraper_headless",
    "scraper_timeout": "scraper_timeout",
}

SECRET_KEYS = {
    "gemini_api_key",
    "openai_api_key",
    "anthropic_api_key",
    "openrouter_api_key",
}


def mask_secret(value: str | None) -> str | None:
    """Return a masked representation of a sensitive key to protect secrets in UI/logs."""
    if not value:
        return None
    if len(value) <= 8:
        return "***"
    if value.startswith("sk-"):
        return f"sk-...{value[-4:]}"
    return f"{value[:3]}...{value[-4:]}"


def is_masked_indicator(value: str | None) -> bool:
    """Check if value is an already masked string to prevent overwriting stored secrets."""
    if not value:
        return False
    return "..." in value or value == "***"


def get_raw_setting(
    session: Session, db_key: str, default: str | None = None, user_id: int = 1
) -> str | None:
    # shortcut: values stored plain in sqlite; upgrade to keyring/fernet if multi-tenant
    setting = session.exec(
        select(SystemSetting).where(
            SystemSetting.user_id == user_id,
            SystemSetting.key == db_key,
        )
    ).first()
    return setting.value_encrypted_or_plain if setting else default


def set_raw_setting(session: Session, db_key: str, value: str | None, user_id: int = 1) -> None:
    if session.get(User, user_id) is None:
        email = "default@careerforge.local" if user_id == 1 else f"user_{user_id}@careerforge.local"
        existing = session.exec(select(User).where(User.email == email)).first()
        if not existing:
            session.add(User(id=user_id, email=email))
            session.flush()

    setting = session.exec(
        select(SystemSetting).where(
            SystemSetting.user_id == user_id,
            SystemSetting.key == db_key,
        )
    ).first()
    if value is None or value == "":
        if setting:
            session.delete(setting)
    else:
        if setting:
            setting.value_encrypted_or_plain = value
            setting.updated_at = datetime.now(UTC)
            session.add(setting)
        else:
            session.add(
                SystemSetting(
                    user_id=user_id,
                    key=db_key,
                    value_encrypted_or_plain=value,
                )
            )


def get_settings(session: Session, user_id: int = 1) -> SettingsRead:
    settings_map: dict[str, Any] = {}
    for attr, db_key in SETTING_KEYS.items():
        raw_val = get_raw_setting(session, db_key, user_id=user_id)
        if attr in SECRET_KEYS:
            settings_map[attr] = mask_secret(raw_val)
        elif attr == "scraper_headless":
            settings_map[attr] = True if raw_val is None else (raw_val.lower() == "true")
        elif attr == "scraper_timeout":
            settings_map[attr] = 30 if raw_val is None else int(raw_val)
        elif raw_val is not None:
            settings_map[attr] = raw_val

    return SettingsRead(**settings_map)


def update_settings(session: Session, update: SettingsUpdate, user_id: int = 1) -> SettingsRead:
    payload = update.model_dump(exclude_unset=True)

    for attr, val in payload.items():
        if attr not in SETTING_KEYS:
            continue
        db_key = SETTING_KEYS[attr]

        if attr in SECRET_KEYS:
            if val == "":
                set_raw_setting(session, db_key, None, user_id=user_id)
            elif val is not None and not is_masked_indicator(val):
                set_raw_setting(session, db_key, val, user_id=user_id)
        elif attr == "scraper_headless":
            set_raw_setting(session, db_key, "true" if val else "false", user_id=user_id)
        elif attr == "scraper_timeout":
            set_raw_setting(session, db_key, str(val), user_id=user_id)
        elif val is not None:
            set_raw_setting(session, db_key, str(val), user_id=user_id)

    session.commit()
    return get_settings(session, user_id=user_id)

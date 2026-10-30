from typing import Literal

from pydantic import BaseModel, Field

LLMProvider = Literal["gemini", "openai", "claude", "ollama"]


class SettingsRead(BaseModel):
    active_provider: LLMProvider = "gemini"
    gemini_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    scraper_headless: bool = True
    scraper_timeout: int = 30


class SettingsUpdate(BaseModel):
    active_provider: LLMProvider | None = None
    gemini_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ollama_base_url: str | None = None
    scraper_headless: bool | None = None
    scraper_timeout: int | None = Field(default=None, ge=1, le=300)

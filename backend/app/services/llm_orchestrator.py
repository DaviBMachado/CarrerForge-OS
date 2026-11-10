import json
import re
import time
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from sqlmodel import Session

from backend.app.logging_config import get_logger
from backend.app.models import MasterProfile
from backend.app.schemas.settings import LLMProvider
from backend.app.services.settings import SETTING_KEYS, get_raw_setting

logger = get_logger("careerforge.llm")

TargetLanguage = Literal["en", "pt-br"]

_PT_STOPWORDS = {
    "para",
    "com",
    "uma",
    "dos",
    "das",
    "pelo",
    "pela",
    "você",
    "voce",
    "nossa",
    "nosso",
    "requisitos",
    "experiência",
    "experiencia",
    "desenvolvimento",
    "desenvolvedor",
    "desenvolvedora",
    "engenheiro",
    "engenheira",
    "vaga",
    "trabalho",
    "empresa",
    "atuar",
    "conhecimento",
    "sistemas",
    "dados",
    "sênior",
    "pleno",
    "júnior",
    "contratação",
    "benefícios",
    "sobre",
    "atuação",
}

_EN_STOPWORDS = {
    "the",
    "and",
    "with",
    "for",
    "you",
    "our",
    "requirements",
    "experience",
    "development",
    "developer",
    "engineer",
    "role",
    "work",
    "company",
    "knowledge",
    "systems",
    "senior",
    "junior",
    "benefits",
    "about",
    "looking",
    "build",
    "team",
    "skills",
    "responsibilities",
    "qualifications",
}

_PERSONA_RULES: list[tuple[str, set[str]]] = [
    (
        "AI Automation Engineer",
        {
            "llm",
            "llms",
            "rag",
            "genai",
            "generative ai",
            "ia generativa",
            "langchain",
            "langgraph",
            "prompt",
            "agent",
            "agents",
            "agentes",
            "openai",
            "anthropic",
            "gemini",
            "faiss",
            "vector",
            "embedding",
            "embeddings",
            "iara",
            "ai automation",
            "automação com ia",
        },
    ),
    (
        "Cloud & Serverless Engineer",
        {
            "serverless",
            "lambda",
            "step functions",
            "terraform",
            "iac",
            "cloudwatch",
            "datadog",
            "devops",
            "sre",
            "infraestrutura",
            "infrastructure",
            "kubernetes",
            "ecs",
            "eks",
            "s3",
            "athena",
            "iam",
            "cloud",
        },
    ),
    (
        "Full-Stack Software Engineer",
        {
            "fullstack",
            "full-stack",
            "full stack",
            "react",
            "angular",
            "typescript",
            "frontend",
            "front-end",
            "ui",
            "ux",
            "css",
            "html",
            "nextjs",
            "next.js",
        },
    ),
    (
        "Backend Python Engineer",
        {
            "python",
            "backend",
            "back-end",
            "fastapi",
            "django",
            "flask",
            "rest",
            "restful",
            "api",
            "apis",
            "microsserviços",
            "microservices",
            "postgresql",
            "mysql",
            "sql",
            "nosql",
            "dynamodb",
            "pydantic",
            "pytest",
        },
    ),
]


class LLMError(RuntimeError):
    """Base error for LLM orchestration failures."""


class LLMConfigurationError(LLMError):
    """Raised when provider credentials or URL settings are missing or invalid."""


class LLMProviderError(LLMError):
    """Raised when an upstream LLM provider returns an error or invalid payload."""


def detect_language(text: str) -> TargetLanguage:
    """Identify whether a raw job posting is primarily English ('en') or Portuguese ('pt-br')."""
    if not text or not text.strip():
        return "en"

    lowered = text.lower()
    words = re.findall(r"[a-zà-ÿ]+", lowered)
    if not words:
        return "en"

    pt_score = sum(1 for w in words if w in _PT_STOPWORDS)
    en_score = sum(1 for w in words if w in _EN_STOPWORDS)

    # Portuguese diacritics and characteristic endings provide strong tie-breaker signal
    pt_diacritics = len(re.findall(r"[ãõçáéíóúâêô]", lowered))
    if pt_diacritics >= 2:
        pt_score += 2

    return "pt-br" if pt_score > en_score else "en"


def _extract_profile_tokens(profile: MasterProfile | dict[str, Any] | None) -> set[str]:
    if profile is None:
        return set()

    raw_chunks: list[str] = []
    if isinstance(profile, dict):
        raw_chunks.append(str(profile.get("headline") or ""))
        raw_chunks.append(str(profile.get("summary_base") or ""))
        raw_chunks.append(json.dumps(profile.get("skills_json") or {}))
        raw_chunks.append(json.dumps(profile.get("experiences") or []))
    else:
        raw_chunks.append(profile.headline or "")
        raw_chunks.append(profile.summary_base or "")
        raw_chunks.append(json.dumps(profile.skills_json or {}))
        for exp in getattr(profile, "experiences", []) or []:
            raw_chunks.append(exp.role or "")
            raw_chunks.append(json.dumps(exp.bullets_json or []))

    combined = " ".join(raw_chunks).lower()
    return set(re.findall(r"[a-z0-9à-ÿ+#.-]+", combined))


def select_persona(
    job_text: str,
    profile: MasterProfile | dict[str, Any] | None = None,
) -> str:
    """Select the strongest focus lens matching job requirements and candidate profile."""
    if not job_text or not job_text.strip():
        return "Backend Python Engineer"

    lowered_job = job_text.lower()
    job_words = set(re.findall(r"[a-z0-9à-ÿ+#.-]+", lowered_job))
    profile_tokens = _extract_profile_tokens(profile)

    best_persona = "Backend Python Engineer"
    best_score = -1.0

    for persona_title, keywords in _PERSONA_RULES:
        score = 0.0
        for kw in keywords:
            matched = (kw in lowered_job) if " " in kw else (kw in job_words)
            if matched:
                # Boost keywords also grounded in the candidate's profile
                grounded = (kw in profile_tokens) or not profile_tokens
                score += 1.5 if grounded else 1.0
        if score > best_score and score > 0:
            best_score = score
            best_persona = persona_title

    return best_persona


def _strip_json_fences(raw_text: str) -> str:
    cleaned = raw_text.strip()
    fence_match = re.match(
        r"^```(?:json)?\s*(.*?)\s*```$", cleaned, flags=re.DOTALL | re.IGNORECASE
    )
    if fence_match:
        return fence_match.group(1).strip()
    return cleaned


def _redact_secret(text: str, secret: str | None) -> str:
    if not secret:
        return text
    return text.replace(secret, "[REDACTED]")


def _validate_ollama_url(base_url: str) -> str:
    parsed = urlparse(base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LLMConfigurationError("Invalid ollama_base_url: must start with http:// or https://")
    return base_url.rstrip("/")


class LLMOrchestrator:
    """Unified multi-provider LLM client reading active credentials from SystemSetting."""

    DEFAULT_MODELS: dict[LLMProvider, str] = {
        "gemini": "gemini-3.8-flash",
        "openai": "gpt-4o-mini",
        "claude": "claude-3-5-sonnet-latest",
        "ollama": "llama3.1",
    }

    def __init__(
        self,
        session: Session,
        http_client: httpx.Client | None = None,
        timeout: float = 90.0,
        user_id: int = 1,
    ) -> None:
        self.session = session
        self._http_client = http_client
        self._timeout = timeout
        self.user_id = user_id

    def get_active_provider(self) -> LLMProvider:
        raw = (
            get_raw_setting(
                self.session,
                SETTING_KEYS["active_provider"],
                "gemini",
                user_id=self.user_id,
            )
            or "gemini"
        )
        if raw in ("gemini", "openai", "claude", "ollama"):
            return raw  # type: ignore[return-value]
        return "gemini"

    def _require_key(self, setting_attr: str, provider_label: str) -> str:
        key = get_raw_setting(self.session, SETTING_KEYS[setting_attr], user_id=self.user_id)
        if not key or not key.strip():
            raise LLMConfigurationError(
                f"Missing API key for provider '{provider_label}'. Configure it in Settings."
            )
        return key.strip()

    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        *,
        json_mode: bool = False,
        temperature: float = 0.2,
        provider_override: LLMProvider | None = None,
    ) -> str:
        provider = provider_override or self.get_active_provider()
        if not prompt or not prompt.strip():
            raise ValueError("Prompt must not be empty.")

        if self._http_client is not None:
            return self._dispatch(
                self._http_client,
                provider=provider,
                prompt=prompt,
                system_prompt=system_prompt,
                json_mode=json_mode,
                temperature=temperature,
            )

        with httpx.Client(timeout=self._timeout) as client:
            return self._dispatch(
                client,
                provider=provider,
                prompt=prompt,
                system_prompt=system_prompt,
                json_mode=json_mode,
                temperature=temperature,
            )

    def generate_json(
        self,
        prompt: str,
        system_prompt: str = "",
        *,
        temperature: float = 0.1,
        provider_override: LLMProvider | None = None,
    ) -> dict[str, Any]:
        raw = self.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            json_mode=True,
            temperature=temperature,
            provider_override=provider_override,
        )
        cleaned = _strip_json_fences(raw)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(f"Provider did not return valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise LLMProviderError("Expected a JSON object from LLM response.")
        return parsed

    def _dispatch(
        self,
        client: httpx.Client,
        *,
        provider: LLMProvider,
        prompt: str,
        system_prompt: str,
        json_mode: bool,
        temperature: float,
    ) -> str:
        payload_size = len(prompt) + len(system_prompt)
        configured_model = self.DEFAULT_MODELS.get(provider, "default")
        logger.info(
            f"Dispatching LLM call: provider='{provider}' model='{configured_model}' "
            f"payload_chars={payload_size} json_mode={json_mode}"
        )
        secret: str | None = None
        try:
            if provider == "gemini":
                secret = self._require_key("gemini_api_key", "gemini")
                return self._call_gemini(
                    client, secret, prompt, system_prompt, json_mode, temperature
                )
            if provider == "openai":
                secret = self._require_key("openai_api_key", "openai")
                return self._call_openai(
                    client, secret, prompt, system_prompt, json_mode, temperature
                )
            if provider == "claude":
                secret = self._require_key("anthropic_api_key", "claude")
                return self._call_claude(
                    client, secret, prompt, system_prompt, json_mode, temperature
                )
            if provider == "ollama":
                base_url = (
                    get_raw_setting(
                        self.session,
                        SETTING_KEYS["ollama_base_url"],
                        "http://localhost:11434",
                        user_id=self.user_id,
                    )
                    or "http://localhost:11434"
                )
                return self._call_ollama(
                    client,
                    _validate_ollama_url(base_url),
                    prompt,
                    system_prompt,
                    json_mode,
                    temperature,
                )
            raise LLMConfigurationError(f"Unsupported LLM provider: {provider}")
        except LLMError:
            raise
        except httpx.HTTPError as exc:
            safe_msg = _redact_secret(str(exc), secret)
            logger.error(f"HTTP error communicating with {provider}: {safe_msg}")
            raise LLMProviderError(
                f"HTTP error communicating with {provider}: {safe_msg}"
            ) from None

    def _call_gemini(
        self,
        client: httpx.Client,
        api_key: str,
        prompt: str,
        system_prompt: str,
        json_mode: bool,
        temperature: float,
    ) -> str:
        primary_model = self.DEFAULT_MODELS["gemini"]
        candidate_models = [primary_model]
        if primary_model != "gemini-3.5-flash":
            candidate_models.append("gemini-3.5-flash")

        generation_config: dict[str, Any] = {"temperature": temperature}
        if json_mode:
            generation_config["responseMimeType"] = "application/json"

        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        last_resp: httpx.Response | None = None
        for model in candidate_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            for attempt in range(2):
                start_time = time.perf_counter()
                resp = client.post(url, headers={"x-goog-api-key": api_key}, json=payload)
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                last_resp = resp
                if resp.status_code == 200:
                    data = resp.json()
                    usage = data.get("usageMetadata") or {}
                    total_tokens = usage.get("totalTokenCount")
                    tok_str = f"tokens={total_tokens}" if total_tokens is not None else "tokens=n/a"
                    logger.info(
                        f"Gemini call succeeded: model='{model}' "
                        f"elapsed={elapsed_ms:.1f}ms {tok_str}"
                    )
                    try:
                        return str(data["candidates"][0]["content"]["parts"][0]["text"])
                    except (KeyError, IndexError, TypeError) as exc:
                        raise LLMProviderError("Unexpected Gemini response schema.") from exc
                if resp.status_code in (503, 429) and attempt == 0:
                    logger.warning(
                        f"Gemini status {resp.status_code} for model='{model}'. Retrying in 1s..."
                    )
                    time.sleep(1.0)
                    continue
                break

        if last_resp is not None:
            err_msg = _redact_secret(last_resp.text, api_key)
            logger.error(f"Gemini call failed: status={last_resp.status_code} error={err_msg}")
            raise LLMProviderError(f"Gemini API error ({last_resp.status_code}): {err_msg}")
        raise LLMProviderError("Gemini API call failed with no response.")

    def _call_openai(
        self,
        client: httpx.Client,
        api_key: str,
        prompt: str,
        system_prompt: str,
        json_mode: bool,
        temperature: float,
    ) -> str:
        url = "https://api.openai.com/v1/chat/completions"
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        model = self.DEFAULT_MODELS["openai"]
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        start_time = time.perf_counter()
        resp = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        if resp.status_code >= 400:
            err_msg = _redact_secret(resp.text, api_key)
            logger.error(
                f"OpenAI call failed ({resp.status_code}) in {elapsed_ms:.1f}ms: {err_msg}"
            )
            raise LLMProviderError(f"OpenAI API error ({resp.status_code}): {err_msg}")
        data = resp.json()
        usage = data.get("usage") or {}
        total_tokens = usage.get("total_tokens")
        tok_str = f"tokens={total_tokens}" if total_tokens is not None else "tokens=n/a"
        logger.info(f"OpenAI call succeeded: model='{model}' elapsed={elapsed_ms:.1f}ms {tok_str}")
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("Unexpected OpenAI response schema.") from exc

    def _call_claude(
        self,
        client: httpx.Client,
        api_key: str,
        prompt: str,
        system_prompt: str,
        json_mode: bool,
        temperature: float,
    ) -> str:
        url = "https://api.anthropic.com/v1/messages"
        effective_system = system_prompt
        if json_mode:
            json_instruction = (
                "Respond strictly with a valid JSON object without markdown wrappers."
            )
            effective_system = (
                f"{system_prompt}\n\n{json_instruction}".strip()
                if system_prompt
                else json_instruction
            )

        model = self.DEFAULT_MODELS["claude"]
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": 4096,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if effective_system:
            payload["system"] = effective_system

        start_time = time.perf_counter()
        resp = client.post(
            url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            json=payload,
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        if resp.status_code >= 400:
            err_msg = _redact_secret(resp.text, api_key)
            logger.error(
                f"Claude call failed ({resp.status_code}) in {elapsed_ms:.1f}ms: {err_msg}"
            )
            raise LLMProviderError(f"Claude API error ({resp.status_code}): {err_msg}")
        data = resp.json()
        usage = data.get("usage") or {}
        inp = usage.get("input_tokens") or 0
        out = usage.get("output_tokens") or 0
        total_tokens = inp + out if (inp or out) else None
        tok_str = f"tokens={total_tokens}" if total_tokens is not None else "tokens=n/a"
        logger.info(f"Claude call succeeded: model='{model}' elapsed={elapsed_ms:.1f}ms {tok_str}")
        try:
            return str(data["content"][0]["text"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("Unexpected Claude response schema.") from exc

    def _call_ollama(
        self,
        client: httpx.Client,
        base_url: str,
        prompt: str,
        system_prompt: str,
        json_mode: bool,
        temperature: float,
    ) -> str:
        url = f"{base_url}/api/chat"
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        model = self.DEFAULT_MODELS["ollama"]
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_mode:
            payload["format"] = "json"

        start_time = time.perf_counter()
        resp = client.post(url, json=payload)
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        if resp.status_code >= 400:
            logger.error(
                f"Ollama call failed ({resp.status_code}) in {elapsed_ms:.1f}ms: {resp.text}"
            )
            raise LLMProviderError(f"Ollama API error ({resp.status_code}): {resp.text}")
        data = resp.json()
        tokens = data.get("eval_count")
        tok_str = f"tokens={tokens}" if tokens is not None else "tokens=n/a"
        logger.info(f"Ollama call succeeded: model='{model}' elapsed={elapsed_ms:.1f}ms {tok_str}")
        try:
            return str(data["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise LLMProviderError("Unexpected Ollama response schema.") from exc

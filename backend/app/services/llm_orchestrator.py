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


FALLBACK_MODELS: dict[str, list[str]] = {
    "gemini": [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
        "gemini-3.8-flash",
    ],
    "openai": [
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4.1-mini",
        "o1-mini",
        "o3-mini",
    ],
    "openrouter": [
        "openai/gpt-4o-mini",
        "google/gemini-2.5-flash",
        "anthropic/claude-3.5-sonnet",
        "meta-llama/llama-3.3-70b-instruct",
        "deepseek/deepseek-chat",
    ],
    "claude": [
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
        "claude-3-opus-latest",
    ],
    "ollama": [
        "llama3.1",
        "mistral",
        "qwen2.5",
    ],
}


def fetch_provider_models(
    provider: str, api_key: str | None = None, base_url: str | None = None
) -> list[str]:
    """Fetch live available models from provider API with reliable static fallback."""
    fallbacks = FALLBACK_MODELS.get(provider, [])
    cleaned_key = api_key.strip() if api_key else ""
    try:
        with httpx.Client(timeout=10.0) as client:
            if provider == "openrouter":
                headers = {}
                if cleaned_key:
                    headers["Authorization"] = f"Bearer {cleaned_key}"
                resp = client.get("https://openrouter.ai/api/v1/models", headers=headers)
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    models = [str(m.get("id")) for m in data if m.get("id")]
                    return models if models else fallbacks

            elif provider == "gemini" and cleaned_key:
                resp = client.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={cleaned_key}"
                )
                if resp.status_code == 200:
                    items = resp.json().get("models", [])
                    extracted = [
                        m["name"].replace("models/", "")
                        for m in items
                        if "name" in m
                        and (
                            not m.get("supportedGenerationMethods")
                            or "generateContent" in m.get("supportedGenerationMethods", [])
                        )
                    ]
                    return extracted if extracted else fallbacks

            elif provider == "openai" and cleaned_key:
                resp = client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {cleaned_key}"},
                )
                if resp.status_code == 200:
                    items = resp.json().get("data", [])
                    extracted = [
                        m["id"] for m in items if any(k in m["id"] for k in ("gpt-", "o1", "o3"))
                    ]
                    extracted.sort()
                    return extracted if extracted else fallbacks

            elif provider == "claude" and cleaned_key:
                resp = client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": cleaned_key, "anthropic-version": "2023-06-01"},
                )
                if resp.status_code == 200:
                    items = resp.json().get("data", [])
                    extracted = [m["id"] for m in items if "id" in m]
                    return extracted if extracted else fallbacks

            elif provider == "ollama":
                target_url = (base_url or "http://localhost:11434").rstrip("/")
                resp = client.get(f"{target_url}/api/tags")
                if resp.status_code == 200:
                    models = [m["name"] for m in resp.json().get("models", []) if "name" in m]
                    return models if models else fallbacks
    except Exception as exc:
        logger.warning(f"Could not fetch models dynamically for {provider}: {exc}")

    return fallbacks


def _validate_ollama_url(base_url: str) -> str:
    parsed = urlparse(base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LLMConfigurationError("Invalid ollama_base_url: must start with http:// or https://")
    return base_url.rstrip("/")


class LLMOrchestrator:
    """Unified multi-provider LLM client reading active credentials from SystemSetting."""

    DEFAULT_MODELS: dict[LLMProvider, str] = {
        "gemini": "gemini-3.5-flash-lite",
        "openai": "gpt-4o-mini",
        "claude": "claude-3-5-sonnet-latest",
        "ollama": "llama3.1",
        "openrouter": "openai/gpt-4o-mini",
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
        if raw in ("gemini", "openai", "claude", "ollama", "openrouter"):
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
        selected_model = get_raw_setting(
            self.session,
            SETTING_KEYS.get("selected_model", "llm_selected_model"),
            user_id=self.user_id,
        )
        configured_model = selected_model or self.DEFAULT_MODELS.get(provider, "default")
        logger.info(
            f"Dispatching LLM call: provider='{provider}' model='{configured_model}' "
            f"payload_chars={payload_size} json_mode={json_mode}"
        )
        secret: str | None = None
        try:
            if provider == "gemini":
                secret = self._require_key("gemini_api_key", "gemini")
                return self._call_gemini(
                    client,
                    secret,
                    prompt,
                    system_prompt,
                    json_mode,
                    temperature,
                    model=configured_model,
                )
            if provider == "openai":
                secret = self._require_key("openai_api_key", "openai")
                return self._call_openai(
                    client,
                    secret,
                    prompt,
                    system_prompt,
                    json_mode,
                    temperature,
                    model=configured_model,
                )
            if provider == "claude":
                secret = self._require_key("anthropic_api_key", "claude")
                return self._call_claude(
                    client,
                    secret,
                    prompt,
                    system_prompt,
                    json_mode,
                    temperature,
                    model=configured_model,
                )
            if provider == "openrouter":
                secret = self._require_key("openrouter_api_key", "openrouter")
                return self._call_openrouter(
                    client,
                    secret,
                    prompt,
                    system_prompt,
                    json_mode,
                    temperature,
                    model=configured_model,
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
                    model=configured_model,
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
        model: str | None = None,
    ) -> str:
        primary_model = model or self.DEFAULT_MODELS["gemini"]
        candidate_models = [primary_model]
        if primary_model != "gemini-3.5-flash" and "flash" in primary_model:
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
        for cand_model in candidate_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{cand_model}:generateContent"
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
                        f"Gemini call succeeded: model='{cand_model}' "
                        f"elapsed={elapsed_ms:.1f}ms {tok_str}"
                    )
                    try:
                        return str(data["candidates"][0]["content"]["parts"][0]["text"])
                    except (KeyError, IndexError, TypeError) as exc:
                        raise LLMProviderError("Unexpected Gemini response schema.") from exc
                if resp.status_code in (503, 429) and attempt == 0:
                    logger.warning(
                        f"Gemini status {resp.status_code} for model='{cand_model}'. Retrying in 1s..."
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
        model: str | None = None,
    ) -> str:
        url = "https://api.openai.com/v1/chat/completions"
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        effective_model = model or self.DEFAULT_MODELS["openai"]
        payload: dict[str, Any] = {
            "model": effective_model,
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
        logger.info(
            f"OpenAI call succeeded: model='{effective_model}' elapsed={elapsed_ms:.1f}ms {tok_str}"
        )
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("Unexpected OpenAI response schema.") from exc

    def _call_openrouter(
        self,
        client: httpx.Client,
        api_key: str,
        prompt: str,
        system_prompt: str,
        json_mode: bool,
        temperature: float,
        model: str | None = None,
    ) -> str:
        url = "https://openrouter.ai/api/v1/chat/completions"
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        effective_model = model or self.DEFAULT_MODELS["openrouter"]
        payload: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "CareerForge OS",
        }
        start_time = time.perf_counter()
        resp = client.post(
            url,
            headers=headers,
            json=payload,
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        # If model does not support response_format: {"type": "json_object"}, retry with prompt directive
        if (
            resp.status_code == 400
            and json_mode
            and any(
                term in resp.text.lower() for term in ("response_format", "json_object", "format")
            )
        ):
            logger.warning(
                f"OpenRouter model '{effective_model}' rejected response_format; retrying with system prompt JSON directive."
            )
            fallback_payload = dict(payload)
            fallback_payload.pop("response_format", None)
            json_instruction = (
                "Respond strictly with a valid JSON object without markdown wrappers."
            )
            fallback_messages: list[dict[str, str]] = []
            for msg in messages:
                fallback_messages.append(dict(msg))
            if fallback_messages and fallback_messages[0]["role"] == "system":
                fallback_messages[0]["content"] = (
                    f"{fallback_messages[0]['content']}\n\n{json_instruction}".strip()
                )
            else:
                fallback_messages.insert(0, {"role": "system", "content": json_instruction})
            fallback_payload["messages"] = fallback_messages

            start_time = time.perf_counter()
            resp = client.post(url, headers=headers, json=fallback_payload)
            elapsed_ms = (time.perf_counter() - start_time) * 1000
        if resp.status_code >= 400:
            err_msg = _redact_secret(resp.text, api_key)
            logger.error(
                f"OpenRouter call failed ({resp.status_code}) in {elapsed_ms:.1f}ms: {err_msg}"
            )
            raise LLMProviderError(f"OpenRouter API error ({resp.status_code}): {err_msg}")
        data = resp.json()
        usage = data.get("usage") or {}
        total_tokens = usage.get("total_tokens")
        tok_str = f"tokens={total_tokens}" if total_tokens is not None else "tokens=n/a"
        logger.info(
            f"OpenRouter call succeeded: model='{effective_model}' elapsed={elapsed_ms:.1f}ms {tok_str}"
        )
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("Unexpected OpenRouter response schema.") from exc

    def _call_claude(
        self,
        client: httpx.Client,
        api_key: str,
        prompt: str,
        system_prompt: str,
        json_mode: bool,
        temperature: float,
        model: str | None = None,
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

        effective_model = model or self.DEFAULT_MODELS["claude"]
        payload: dict[str, Any] = {
            "model": effective_model,
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
        logger.info(
            f"Claude call succeeded: model='{effective_model}' elapsed={elapsed_ms:.1f}ms {tok_str}"
        )
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
        model: str | None = None,
    ) -> str:
        url = f"{base_url}/api/chat"
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        effective_model = model or self.DEFAULT_MODELS["ollama"]
        payload: dict[str, Any] = {
            "model": effective_model,
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
        logger.info(
            f"Ollama call succeeded: model='{effective_model}' elapsed={elapsed_ms:.1f}ms {tok_str}"
        )
        try:
            return str(data["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise LLMProviderError("Unexpected Ollama response schema.") from exc

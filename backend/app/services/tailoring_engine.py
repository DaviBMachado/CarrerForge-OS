import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session, select

from backend.app.models import Experience, JobPosting, MasterProfile, TailoredArtifact
from backend.app.services.llm_orchestrator import (
    LLMOrchestrator,
    LLMProviderError,
    TargetLanguage,
    detect_language,
    select_persona,
)
from backend.app.services.resume_compiler import ResumeSpec

_KNOWN_TECH_VOCAB: set[str] = {
    "python",
    "java",
    "javascript",
    "typescript",
    "golang",
    "rust",
    "c#",
    "c++",
    "ruby",
    "php",
    "kotlin",
    "swift",
    "scala",
    "elixir",
    "react",
    "angular",
    "vue",
    "nextjs",
    "next.js",
    "nodejs",
    "node.js",
    "fastapi",
    "django",
    "flask",
    "spring",
    "spring boot",
    "dotnet",
    ".net",
    "graphql",
    "grpc",
    "rest",
    "restful",
    "postgresql",
    "postgres",
    "mysql",
    "mongodb",
    "redis",
    "dynamodb",
    "cassandra",
    "elasticsearch",
    "snowflake",
    "bigquery",
    "athena",
    "oracle",
    "sql",
    "nosql",
    "aws",
    "gcp",
    "azure",
    "lambda",
    "step functions",
    "s3",
    "ec2",
    "ecs",
    "eks",
    "sqs",
    "sns",
    "iam",
    "cloudwatch",
    "datadog",
    "prometheus",
    "grafana",
    "docker",
    "kubernetes",
    "k8s",
    "terraform",
    "ansible",
    "jenkins",
    "github actions",
    "gitlab ci",
    "ci/cd",
    "kafka",
    "rabbitmq",
    "airflow",
    "spark",
    "hadoop",
    "dbt",
    "llm",
    "llms",
    "rag",
    "langchain",
    "langgraph",
    "faiss",
    "pytorch",
    "tensorflow",
    "scikit-learn",
    "pandas",
    "numpy",
    "pytest",
    "junit",
    "cypress",
    "playwright",
    "selenium",
    "servicenow",
    "jwt",
    "oauth",
    "openapi",
    "swagger",
    "scrum",
    "kanban",
}


class FactualGuardrailError(ValueError):
    """Raised when tailored output introduces fabricated facts not grounded in MasterProfile."""


class TailoringResult(BaseModel):
    artifact_id: int
    job_id: int
    language: TargetLanguage
    selected_persona: str
    ats_keywords: list[str] = Field(default_factory=list)
    match_percentage: int = 0
    resume_spec: ResumeSpec
    cover_letter_text: str


def _tokenize_lower(text: str) -> set[str]:
    raw_tokens = re.findall(r"[a-z0-9à-ÿ+#./-]+", text.lower())
    return {tok.strip(".,;:!?/\\-") for tok in raw_tokens if tok.strip(".,;:!?/\\-")}


def _collect_profile_grounding(
    profile: MasterProfile,
    experiences: list[Experience],
) -> tuple[str, set[str], list[str]]:
    chunks: list[str] = [
        profile.full_name or "",
        profile.headline or "",
        profile.summary_base or "",
    ]
    if isinstance(profile.contact_json, dict):
        chunks.extend(str(v) for v in profile.contact_json.values() if v)
    elif isinstance(profile.contact_json, list):
        chunks.extend(str(v) for v in profile.contact_json if v)

    if isinstance(profile.skills_json, dict):
        for cat, items in profile.skills_json.items():
            chunks.append(str(cat))
            if isinstance(items, list):
                chunks.extend(str(i) for i in items)
            else:
                chunks.append(str(items))
    elif isinstance(profile.skills_json, list):
        chunks.extend(str(i) for i in profile.skills_json)

    if isinstance(profile.languages_json, list):
        chunks.extend(str(i) for i in profile.languages_json)

    companies: list[str] = []
    for exp in experiences:
        if exp.company:
            companies.append(exp.company)
            chunks.append(exp.company)
        if exp.role:
            chunks.append(exp.role)
        for bullet in exp.bullets_json or []:
            if isinstance(bullet, dict):
                chunks.append(str(bullet.get("text") or ""))
                for tag in bullet.get("tags") or []:
                    chunks.append(str(tag))
            else:
                chunks.append(str(bullet))

    corpus = " ".join(chunks).lower()
    tokens = _tokenize_lower(corpus)
    return corpus, tokens, companies


def _term_in_text(term: str, lowered_text: str, tokens: set[str]) -> bool:
    t = term.lower().strip()
    if not t:
        return False
    if " " in t or "." in t or "/" in t or "+" in t or "#" in t:
        pattern = rf"(?<![a-z0-9à-ÿ]){re.escape(t)}(?![a-z0-9à-ÿ])"
        return bool(re.search(pattern, lowered_text))
    return t in tokens


def extract_ats_keywords(
    job_text: str,
    profile: MasterProfile | None = None,
    experiences: list[Experience] | None = None,
) -> tuple[list[str], list[str], int]:
    """Return (job_keywords, grounded_keywords, match_percentage)."""
    lowered_job = (job_text or "").lower()
    job_tokens = _tokenize_lower(lowered_job)

    matched_in_job: list[str] = []
    for kw in sorted(_KNOWN_TECH_VOCAB):
        if _term_in_text(kw, lowered_job, job_tokens):
            matched_in_job.append(kw)

    if profile is None:
        return matched_in_job, matched_in_job, (100 if matched_in_job else 0)

    exps = (
        experiences if experiences is not None else list(getattr(profile, "experiences", []) or [])
    )
    corpus, prof_tokens, _ = _collect_profile_grounding(profile, exps)

    grounded: list[str] = []
    for kw in matched_in_job:
        if _term_in_text(kw, corpus, prof_tokens):
            grounded.append(kw)

    if not matched_in_job:
        return [], [], 100

    pct = round((len(grounded) / len(matched_in_job)) * 100)
    return matched_in_job, grounded, pct


def verify_factual_guardrails(
    spec: ResumeSpec,
    profile: MasterProfile,
    experiences: list[Experience] | None = None,
) -> None:
    """Ensure ResumeSpec contains no fabricated technologies or ungrounded companies."""
    exps = (
        experiences if experiences is not None else list(getattr(profile, "experiences", []) or [])
    )
    corpus, prof_tokens, allowed_companies = _collect_profile_grounding(profile, exps)

    spec_chunks: list[str] = [spec.name, spec.title]
    for section in spec.sections:
        spec_chunks.append(section.title)
        for block in section.all_blocks():
            spec_chunks.append(block.text)
            if block.type == "subheading" and "|" in block.text:
                rhs = block.text.split("|", 1)[1].strip()
                rhs_clean = re.sub(r"\([^)]*\)", "", rhs).strip()
                if rhs_clean and allowed_companies:
                    rhs_lower = rhs_clean.lower()
                    if (
                        not any(
                            comp.lower() in rhs_lower or rhs_lower in comp.lower()
                            for comp in allowed_companies
                        )
                        and rhs_lower not in corpus
                    ):
                        raise FactualGuardrailError(
                            f"Ungrounded company or organization in subheading: '{rhs_clean}'"
                        )

    spec_text = " ".join(spec_chunks).lower()
    spec_tokens = _tokenize_lower(spec_text)

    fabricated_techs: list[str] = []
    for tech in sorted(_KNOWN_TECH_VOCAB):
        if _term_in_text(tech, spec_text, spec_tokens):
            if not _term_in_text(tech, corpus, prof_tokens):
                fabricated_techs.append(tech)

    if fabricated_techs:
        joined = ", ".join(fabricated_techs)
        raise FactualGuardrailError(
            f"Guardrail violation: ungrounded technologies detected in resume: {joined}"
        )


def build_tailoring_system_prompt(
    language: TargetLanguage,
    persona: str,
    grounded_keywords: list[str],
) -> str:
    lang_label = "Portuguese (pt-BR)" if language == "pt-br" else "English (en)"
    kw_str = ", ".join(grounded_keywords) if grounded_keywords else "None"
    return (
        "You are an elite ATS Resume & Cover Letter Tailoring Engine.\n"
        "STRICT FACTUAL INTEGRITY GUARDRAIL (NON-NEGOTIABLE):\n"
        "1. NEVER fabricate, invent, or extrapolate companies, roles, dates, degrees, "
        "certifications, metrics, or technologies not explicitly present in MasterProfile.\n"
        "2. Rephrasing must be 100% truth-preserving. Only embed ATS keywords grounded "
        "in the candidate's real profile.\n"
        "3. Structure experience bullet points using impact-driven phrasing "
        "(Action + Context + Metric) preserving exact numbers from the profile.\n"
        f"4. Output language MUST be strictly {lang_label}.\n"
        f"5. Adopt the focus lens / persona: '{persona}'.\n"
        "6. Naturally align these grounded ATS keywords into the summary and "
        f"experience bullets: [{kw_str}].\n"
        "7. Draft a concise, professional, 3-paragraph cover letter "
        "(separated by blank lines '\\n\\n') tailored to the target company and role.\n"
        "8. Respond strictly with a JSON object containing keys 'resume_spec' "
        "(conforming to ResumeSpec schema: name, title, contact, filename_title, "
        "sections[{title, blocks[{type, text}], bullets[]}]) and "
        "'cover_letter' (string with 3 paragraphs)."
    )


def _serialize_profile_for_prompt(
    profile: MasterProfile,
    experiences: list[Experience],
) -> dict[str, Any]:
    return {
        "full_name": profile.full_name,
        "headline": profile.headline,
        "contact": profile.contact_json,
        "summary_base": profile.summary_base,
        "skills": profile.skills_json,
        "languages": profile.languages_json,
        "experiences": [
            {
                "company": exp.company,
                "role": exp.role,
                "location": exp.location,
                "start_date": exp.start_date,
                "end_date": exp.end_date,
                "is_current": exp.is_current,
                "bullets": exp.bullets_json or [],
            }
            for exp in experiences
        ],
    }


def _normalize_cover_letter(raw_letter: Any) -> str:
    if isinstance(raw_letter, dict):
        paragraphs = raw_letter.get("paragraphs")
        if isinstance(paragraphs, list):
            text = "\n\n".join(str(p).strip() for p in paragraphs if str(p).strip())
        else:
            text = str(raw_letter.get("body") or raw_letter.get("text") or "").strip()
    elif isinstance(raw_letter, list):
        text = "\n\n".join(str(p).strip() for p in raw_letter if str(p).strip())
    else:
        text = str(raw_letter or "").strip()

    if not text:
        raise LLMProviderError("Generated cover letter is empty.")

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) != 3:
        raise LLMProviderError(
            f"Cover letter must contain exactly 3 paragraphs, got {len(paragraphs)}."
        )
    return "\n\n".join(paragraphs)


def tailor_job_application(
    session: Session,
    job: JobPosting,
    profile: MasterProfile,
    orchestrator: LLMOrchestrator | None = None,
) -> TailoringResult:
    """Run the ATS tailoring & cover letter pipeline and persist TailoredArtifact."""
    assert job.id is not None, "JobPosting must be persisted before tailoring."
    experiences = list(
        session.exec(select(Experience).where(Experience.profile_id == profile.id)).all()
    )
    job_text = job.cleaned_text or job.raw_description or ""
    language = detect_language(job_text)
    persona = select_persona(job_text, profile)
    _, grounded_keywords, match_pct = extract_ats_keywords(job_text, profile, experiences)

    system_prompt = build_tailoring_system_prompt(language, persona, grounded_keywords)
    user_prompt = (
        f"TARGET JOB:\nTitle: {job.title}\nCompany: {job.company}\n"
        f"Description:\n{job_text}\n\n"
        f"CANDIDATE MASTER PROFILE (SOLE SOURCE OF TRUTH):\n"
        f"{_serialize_profile_for_prompt(profile, experiences)}"
    )

    llm = orchestrator or LLMOrchestrator(session)
    payload = llm.generate_json(prompt=user_prompt, system_prompt=system_prompt)

    raw_spec = payload.get("resume_spec") if "resume_spec" in payload else payload
    try:
        resume_spec = ResumeSpec.model_validate(raw_spec)
    except ValidationError as exc:
        raise LLMProviderError(f"LLM output did not match ResumeSpec schema: {exc}") from exc

    verify_factual_guardrails(resume_spec, profile, experiences)

    raw_letter = payload.get("cover_letter") or payload.get("cover_letter_text")
    cover_letter_text = _normalize_cover_letter(raw_letter)

    summary_text = ""
    for sec in resume_spec.sections:
        if sec.title.lower() in {
            "resumo profissional",
            "professional summary",
            "summary",
            "resumo",
        }:
            summary_text = " ".join(b.text for b in sec.all_blocks()).strip()
            break

    artifact = session.exec(
        select(TailoredArtifact).where(TailoredArtifact.job_id == job.id)
    ).first()
    if artifact is None:
        artifact = TailoredArtifact(job_id=job.id)

    artifact.selected_persona = persona
    artifact.tailored_summary = summary_text or None
    artifact.tailored_experience_json = resume_spec.model_dump()
    artifact.cover_letter_text = cover_letter_text

    job.language = language
    job.status = "tailored"
    session.add(job)
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    assert artifact.id is not None

    return TailoringResult(
        artifact_id=artifact.id,
        job_id=job.id,
        language=language,
        selected_persona=persona,
        ats_keywords=grounded_keywords,
        match_percentage=match_pct,
        resume_spec=resume_spec,
        cover_letter_text=cover_letter_text,
    )

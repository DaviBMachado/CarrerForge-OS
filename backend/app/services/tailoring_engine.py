import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session, select

from backend.app.logging_config import get_logger
from backend.app.models import Experience, JobPosting, MasterProfile, TailoredArtifact
from backend.app.services.llm_orchestrator import (
    LLMOrchestrator,
    LLMProviderError,
    TargetLanguage,
    detect_language,
    select_persona,
)
from backend.app.services.resume_compiler import ResumeSpec

logger = get_logger("careerforge.tailor")

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


class GupyRundown(BaseModel):
    short_bio: str
    impact_metrics: list[str] = Field(default_factory=list)
    prioritized_skill_tags: list[str] = Field(default_factory=list)
    language: TargetLanguage
    selected_persona: str


class SolveQuestionRequest(BaseModel):
    question: str = Field(min_length=2)


class SolveQuestionResponse(BaseModel):
    question: str
    answer: str
    language: TargetLanguage
    grounded_experiences: list[str] = Field(default_factory=list)


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
                        err_msg = f"Ungrounded company or organization in subheading: '{rhs_clean}'"
                        logger.warning(f"Factual guardrail violation: {err_msg}")
                        raise FactualGuardrailError(err_msg)

    spec_text = " ".join(spec_chunks).lower()
    spec_tokens = _tokenize_lower(spec_text)

    fabricated_techs: list[str] = []
    for tech in sorted(_KNOWN_TECH_VOCAB):
        if _term_in_text(tech, spec_text, spec_tokens):
            if not _term_in_text(tech, corpus, prof_tokens):
                fabricated_techs.append(tech)

    if fabricated_techs:
        joined = ", ".join(fabricated_techs)
        err_msg = f"Guardrail violation: ungrounded technologies detected in resume: {joined}"
        logger.warning(f"Factual guardrail violation: {err_msg}")
        raise FactualGuardrailError(err_msg)

    logger.info("Factual guardrail check passed: zero ungrounded technologies detected")


def build_tailoring_system_prompt(
    language: TargetLanguage,
    persona: str,
    grounded_keywords: list[str],
) -> str:
    lang_label = "Portuguese (pt-BR)" if language == "pt-br" else "English (en)"
    kw_str = ", ".join(grounded_keywords) if grounded_keywords else "None"
    return (
        "You are an elite ATS Resume & Cover Letter Tailoring Engine.\n"
        "STRICT FACTUAL INTEGRITY & ANTI-AI DIRECTIVES (NON-NEGOTIABLE):\n"
        "1. NEVER fabricate, invent, or extrapolate companies, roles, dates, degrees, "
        "certifications, metrics, or technologies not explicitly present in MasterProfile.\n"
        "2. Rephrasing must be 100% truth-preserving. Only embed ATS keywords grounded "
        "in the candidate's real profile.\n"
        "3. Resume writing style: strictly structure experience bullets as Action + Scope + Metric "
        "preserving exact numbers from the profile. Prohibit subjective personality adjectives, "
        "hollow corporate buzzwords, and symmetric triple-item lists.\n"
        f"4. Output language MUST be strictly {lang_label}.\n"
        f"5. Adopt the focus lens / persona: '{persona}'.\n"
        "6. Naturally align these grounded ATS keywords into the summary and "
        f"experience bullets: [{kw_str}].\n"
        "7. Cover letter directive: draft a concise, professional, 3-paragraph cover letter "
        "(separated by blank lines '\\n\\n') tailored to the target company and role. "
        "Begin with a balanced, courteous professional greeting and transition directly "
        "into technical alignment between job requirements and candidate's delivered solutions. "
        "Prohibit corporate flattery, cliché opening lines, redundant concluding summaries, "
        "and robotic transitions.\n"
        "8. Conceptual Anti-AI directive: write with human technical clarity and high "
        "information density. Eliminate robotic templates, formulaic symmetries, "
        "and empty filler.\n"
        "9. Respond strictly with a JSON object containing keys 'resume_spec' "
        "(conforming to ResumeSpec schema: name, title, contact, filename_title, "
        "sections[{title, blocks[{type, text}]}]) and "
        "'cover_letter' (string with 3 paragraphs separated by '\\n\\n').\n"
        "   Allowed block types: 'paragraph' (summary), 'subheading' (Role | Company (Dates)), "
        "'bullet' (impact achievement), 'skillline' (**Category:** skills)."
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
    if not paragraphs:
        raise LLMProviderError("Generated cover letter is empty.")

    while len(paragraphs) > 3:
        # Merge the two shortest adjacent paragraphs to maintain exactly 3
        min_idx = 0
        min_len = float("inf")
        for i in range(len(paragraphs) - 1):
            combined_len = len(paragraphs[i]) + len(paragraphs[i + 1])
            if combined_len < min_len:
                min_len = combined_len
                min_idx = i
        paragraphs[min_idx] = f"{paragraphs[min_idx]} {paragraphs[min_idx + 1]}"
        paragraphs.pop(min_idx + 1)

    while len(paragraphs) < 3:
        # Split the longest paragraph on sentence boundary to maintain exactly 3
        longest_idx = max(range(len(paragraphs)), key=lambda idx: len(paragraphs[idx]))
        sentences = re.split(r"(?<=[.!?])\s+", paragraphs[longest_idx])
        if len(sentences) >= 2:
            mid = max(1, len(sentences) // 2)
            p1 = " ".join(sentences[:mid]).strip()
            p2 = " ".join(sentences[mid:]).strip()
            paragraphs[longest_idx] = p1
            paragraphs.insert(longest_idx + 1, p2)
        else:
            mid = max(1, len(paragraphs[longest_idx]) // 2)
            p1 = paragraphs[longest_idx][:mid].strip()
            p2 = paragraphs[longest_idx][mid:].strip()
            paragraphs[longest_idx] = p1
            paragraphs.insert(longest_idx + 1, p2)

    return "\n\n".join(paragraphs)


def tailor_job_application(
    session: Session,
    job: JobPosting,
    profile: MasterProfile,
    orchestrator: LLMOrchestrator | None = None,
) -> TailoringResult:
    """Run the ATS tailoring & cover letter pipeline and persist TailoredArtifact."""
    assert job.id is not None, "JobPosting must be persisted before tailoring."
    logger.info(
        f"Starting tailoring lifecycle for job_id={job.id} ('{job.title}' at '{job.company}')"
    )
    experiences = list(
        session.exec(select(Experience).where(Experience.profile_id == profile.id)).all()
    )
    job_text = job.cleaned_text or job.raw_description or ""
    language = detect_language(job_text)
    persona = select_persona(job_text, profile)
    _, grounded_keywords, match_pct = extract_ats_keywords(job_text, profile, experiences)
    logger.info(
        f"Tailoring strategy selected: persona='{persona}' language='{language}' "
        f"keywords_count={len(grounded_keywords)} match_percentage={match_pct}%"
    )

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
    logger.info(f"Factual guardrails passed for job_id={job.id}")

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
    assert job.id is not None
    logger.info(f"Tailoring completed for job_id={job.id} (artifact_id={artifact.id})")
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


def get_tailored_artifact(
    session: Session,
    job: JobPosting,
    profile: MasterProfile,
) -> TailoringResult | None:
    """Retrieve persisted TailoredArtifact and build TailoringResult without LLM calls."""
    artifact = session.exec(
        select(TailoredArtifact).where(TailoredArtifact.job_id == job.id)
    ).first()
    if artifact is None or not artifact.tailored_experience_json:
        return None

    raw_spec = artifact.tailored_experience_json
    if isinstance(raw_spec, dict) and "ats" in raw_spec:
        raw_spec = raw_spec["ats"]
    resume_spec = ResumeSpec.model_validate(raw_spec)

    job_text = job.cleaned_text or job.raw_description or ""
    experiences = list(
        session.exec(select(Experience).where(Experience.profile_id == profile.id)).all()
    )
    _, grounded_keywords, match_pct = extract_ats_keywords(job_text, profile, experiences)
    persona = artifact.selected_persona or select_persona(job_text, profile)
    language: TargetLanguage = (
        job.language  # type: ignore[assignment]
        if job.language in ("pt-br", "en")
        else detect_language(job_text)
    )

    assert artifact.id is not None
    assert job.id is not None
    return TailoringResult(
        artifact_id=artifact.id,
        job_id=job.id,
        language=language,
        selected_persona=persona,
        ats_keywords=grounded_keywords,
        match_percentage=match_pct,
        resume_spec=resume_spec,
        cover_letter_text=artifact.cover_letter_text or "",
    )


def _extract_impact_metrics(experiences: list[Experience]) -> list[str]:
    metrics: list[str] = []
    for exp in experiences:
        for bullet in exp.bullets_json or []:
            text = str(bullet.get("text") if isinstance(bullet, dict) else bullet or "").strip()
            if text and re.search(r"\d", text):
                entry = f"{text} ({exp.company})" if exp.company else text
                if entry not in metrics:
                    metrics.append(entry)
    return metrics


def _extract_prioritized_skills(
    profile: MasterProfile,
    experiences: list[Experience],
    grounded_keywords: list[str],
) -> list[str]:
    all_skills: list[str] = []
    if isinstance(profile.skills_json, dict):
        for items in profile.skills_json.values():
            if isinstance(items, list):
                all_skills.extend(str(i).strip() for i in items if str(i).strip())
            elif items:
                all_skills.append(str(items).strip())
    elif isinstance(profile.skills_json, list):
        all_skills.extend(str(i).strip() for i in profile.skills_json if str(i).strip())

    for exp in experiences:
        for bullet in exp.bullets_json or []:
            if isinstance(bullet, dict):
                for tag in bullet.get("tags") or []:
                    tag_str = str(tag).strip()
                    if tag_str:
                        all_skills.append(tag_str)

    grounded_set = {k.lower() for k in grounded_keywords}
    prioritized: list[str] = []
    seen_lower: set[str] = set()

    for skill in all_skills:
        low = skill.lower()
        if low in grounded_set and low not in seen_lower:
            prioritized.append(skill)
            seen_lower.add(low)

    for kw in grounded_keywords:
        if kw.lower() not in seen_lower:
            prioritized.append(kw)
            seen_lower.add(kw.lower())

    for skill in all_skills:
        low = skill.lower()
        if low not in seen_lower:
            prioritized.append(skill)
            seen_lower.add(low)

    return prioritized[:20]


def generate_gupy_rundown(
    session: Session,
    job: JobPosting,
    profile: MasterProfile,
) -> GupyRundown:
    """Build and persist Gupy / Form Assistant profile fields grounded in MasterProfile."""
    assert job.id is not None, "JobPosting must be persisted before generating rundown."
    experiences = list(
        session.exec(select(Experience).where(Experience.profile_id == profile.id)).all()
    )
    job_text = job.cleaned_text or job.raw_description or ""
    language = detect_language(job_text)
    persona = select_persona(job_text, profile)
    _, grounded_keywords, _ = extract_ats_keywords(job_text, profile, experiences)

    artifact = session.exec(
        select(TailoredArtifact).where(TailoredArtifact.job_id == job.id)
    ).first()
    if artifact is None:
        artifact = TailoredArtifact(job_id=job.id)

    if artifact.tailored_summary and artifact.tailored_summary.strip():
        short_bio = artifact.tailored_summary.strip()
    elif profile.summary_base and profile.summary_base.strip():
        kw_suffix = f" ({', '.join(grounded_keywords[:6])})" if grounded_keywords else ""
        short_bio = f"{profile.full_name} — {persona}. {profile.summary_base.strip()}{kw_suffix}"
    else:
        kw_str = ", ".join(grounded_keywords[:6]) or (profile.headline or persona)
        if language == "pt-br":
            short_bio = f"{profile.full_name} — {persona} com foco em {kw_str}."
        else:
            short_bio = f"{profile.full_name} — {persona} focused on {kw_str}."

    metrics = _extract_impact_metrics(experiences)
    skills = _extract_prioritized_skills(profile, experiences, grounded_keywords)

    rundown = GupyRundown(
        short_bio=short_bio,
        impact_metrics=metrics,
        prioritized_skill_tags=skills,
        language=language,
        selected_persona=artifact.selected_persona or persona,
    )

    artifact.gupy_rundown_json = rundown.model_dump()
    if not artifact.selected_persona:
        artifact.selected_persona = persona
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return rundown


def _verify_qa_guardrails(
    answer_text: str,
    profile: MasterProfile,
    experiences: list[Experience],
) -> None:
    corpus, prof_tokens, _ = _collect_profile_grounding(profile, experiences)
    ans_lower = answer_text.lower()
    ans_tokens = _tokenize_lower(ans_lower)

    fabricated_techs: list[str] = []
    for tech in sorted(_KNOWN_TECH_VOCAB):
        if _term_in_text(tech, ans_lower, ans_tokens):
            if not _term_in_text(tech, corpus, prof_tokens):
                fabricated_techs.append(tech)

    if fabricated_techs:
        joined = ", ".join(fabricated_techs)
        raise FactualGuardrailError(
            f"Guardrail violation: ungrounded technologies in Q&A answer: {joined}"
        )


def solve_screening_question(
    session: Session,
    job: JobPosting,
    profile: MasterProfile,
    question: str,
    orchestrator: LLMOrchestrator | None = None,
) -> SolveQuestionResponse:
    """Draft a truthful screening answer strictly grounded in MasterProfile achievements."""
    assert job.id is not None, "JobPosting must be persisted before solving questions."
    clean_q = (question or "").strip()
    if not clean_q:
        raise ValueError("Question must not be empty.")

    experiences = list(
        session.exec(select(Experience).where(Experience.profile_id == profile.id)).all()
    )
    job_text = job.cleaned_text or job.raw_description or ""
    # Match question language if strongly signaled, otherwise job language
    q_lang = detect_language(clean_q)
    j_lang = detect_language(job_text)
    language: TargetLanguage = "pt-br" if (q_lang == "pt-br" or j_lang == "pt-br") else "en"
    lang_label = "Portuguese (pt-BR)" if language == "pt-br" else "English (en)"
    persona = select_persona(job_text, profile)

    system_prompt = (
        "You are an elite Career Interview & Screening Q&A Assistant.\n"
        "STRICT FACTUAL INTEGRITY & ANTI-AI DIRECTIVES (NON-NEGOTIABLE):\n"
        "1. Answer the screening question in first person ('I' / 'Eu') drawing STRICTLY "
        "from the candidate's real experiences and metrics in MasterProfile.\n"
        "2. Tone and style: objective, impersonal, and binary regarding technical requirements. "
        "State directly whether the requirement is met followed by concrete facts and metrics. "
        "Eliminate robotic filler, evasive generalities, self-aggrandizing adjectives, "
        "and AI boilerplate.\n"
        "3. NEVER invent companies, tools, technologies, certifications, or metrics "
        "not explicitly present in MasterProfile.\n"
        "4. Keep the answer concise (1 to 2 paragraphs, ready to paste into Gupy or ATS forms).\n"
        f"5. Output language MUST be strictly {lang_label}.\n"
        f"6. Adopt the focus lens: '{persona}'.\n"
        "7. Respond strictly with a JSON object containing keys: "
        "'answer' (string) and 'grounded_experiences' (list of company/project names cited)."
    )

    user_prompt = (
        f"TARGET JOB: {job.title} at {job.company}\n"
        f"SCREENING QUESTION: {clean_q}\n\n"
        f"CANDIDATE MASTER PROFILE (SOLE SOURCE OF TRUTH):\n"
        f"{_serialize_profile_for_prompt(profile, experiences)}"
    )

    llm = orchestrator or LLMOrchestrator(session, timeout=15.0)
    payload = llm.generate_json(
        prompt=user_prompt,
        system_prompt=system_prompt,
        temperature=0.2,
    )

    answer = str(payload.get("answer") or "").strip()
    if not answer:
        raise LLMProviderError("LLM returned an empty screening answer.")

    _verify_qa_guardrails(answer, profile, experiences)

    raw_grounded = payload.get("grounded_experiences")
    if isinstance(raw_grounded, list) and raw_grounded:
        grounded_exps = [str(item).strip() for item in raw_grounded if str(item).strip()]
    else:
        grounded_exps = [
            exp.company
            for exp in experiences
            if exp.company and exp.company.lower() in answer.lower()
        ]

    response = SolveQuestionResponse(
        question=clean_q,
        answer=answer,
        language=language,
        grounded_experiences=grounded_exps,
    )

    artifact = session.exec(
        select(TailoredArtifact).where(TailoredArtifact.job_id == job.id)
    ).first()
    if artifact is None:
        artifact = TailoredArtifact(job_id=job.id, selected_persona=persona)

    history = list(artifact.gupy_qa_json) if isinstance(artifact.gupy_qa_json, list) else []
    history.append(response.model_dump())
    artifact.gupy_qa_json = history
    session.add(artifact)
    session.commit()
    return response

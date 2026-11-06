from typing import Any

from sqlmodel import Session, select

from backend.app.models import Experience, MasterProfile
from backend.app.services.llm_orchestrator import LLMOrchestrator

INGESTION_SYSTEM_PROMPT = """You are an expert ATS Resume Analyzer and Career Profiler.
Analyze the provided raw resume text and extract the candidate's career data into
a strict JSON object with this exact structure:
{
  "full_name": "Full Name",
  "headline": "Target role or headline (e.g., Senior Backend Engineer)",
  "summary_base": "Concise professional summary / bio",
  "contact": {
    "email": "candidate@example.com or empty",
    "phone": "Phone or empty",
    "location": "City, Country or empty",
    "linkedin": "LinkedIn URL or empty",
    "github": "GitHub URL or empty"
  },
  "skills": {
    "Languages": ["Python", "Go"],
    "Frameworks": ["FastAPI", "Django"],
    "Cloud/DevOps": ["Docker", "AWS"],
    "Databases": ["PostgreSQL", "Redis"]
  },
  "languages": ["Português (Nativo)", "Inglês (Avançado)"],
  "experiences": [
    {
      "company": "Company Name",
      "role": "Job Title",
      "location": "Location or Remote",
      "start_date": "YYYY-MM or string",
      "end_date": "YYYY-MM or string or null",
      "is_current": false,
      "bullets": [
        {
          "text": "Action verb + task + quantified result / achievement.",
          "tags": ["relevant", "skills"]
        }
      ]
    }
  ]
}
Return only valid JSON without markdown wrapping.
Extract all real experiences and achievements present in the text."""


def normalize_parsed_profile(data: dict[str, Any]) -> dict[str, Any]:
    full_name = str(data.get("full_name") or "").strip()
    headline = str(data.get("headline") or "").strip() or None
    summary_base = str(data.get("summary_base") or "").strip() or None

    contact_raw = data.get("contact") or data.get("contact_json") or {}
    contact: dict[str, str] = {}
    if isinstance(contact_raw, dict):
        for k, v in contact_raw.items():
            if v:
                contact[str(k)] = str(v).strip()

    skills_raw = data.get("skills") or data.get("skills_json") or {}
    skills: dict[str, list[str]] = {}
    if isinstance(skills_raw, dict):
        for k, v in skills_raw.items():
            if isinstance(v, list):
                skills[str(k)] = [str(item).strip() for item in v if str(item).strip()]
            elif isinstance(v, str) and v.strip():
                skills[str(k)] = [s.strip() for s in v.split(",") if s.strip()]
    elif isinstance(skills_raw, list):
        skills["General"] = [str(s).strip() for s in skills_raw if str(s).strip()]

    languages_raw = data.get("languages") or data.get("languages_json") or []
    languages: list[str] = []
    if isinstance(languages_raw, list):
        languages = [str(lang).strip() for lang in languages_raw if str(lang).strip()]
    elif isinstance(languages_raw, str) and languages_raw.strip():
        languages = [s.strip() for s in languages_raw.split(",") if s.strip()]

    experiences_raw = data.get("experiences") or []
    experiences: list[dict[str, Any]] = []
    if isinstance(experiences_raw, list):
        for exp in experiences_raw:
            if not isinstance(exp, dict):
                continue
            company = str(exp.get("company") or "").strip()
            role = str(exp.get("role") or "").strip()
            if not company and not role:
                continue

            raw_bullets = exp.get("bullets") or exp.get("bullets_json") or []
            bullets: list[dict[str, Any]] = []
            if isinstance(raw_bullets, list):
                for b in raw_bullets:
                    if isinstance(b, dict):
                        b_text = str(b.get("text") or "").strip()
                        b_tags = [str(t).strip() for t in b.get("tags", []) if str(t).strip()]
                        if b_text:
                            bullets.append({"text": b_text, "tags": b_tags})
                    elif isinstance(b, str) and b.strip():
                        bullets.append({"text": b.strip(), "tags": []})

            experiences.append(
                {
                    "company": company or "Empresa",
                    "role": role or "Cargo",
                    "location": str(exp.get("location") or "").strip() or None,
                    "start_date": str(exp.get("start_date") or "").strip() or None,
                    "end_date": str(exp.get("end_date") or "").strip() or None,
                    "is_current": bool(exp.get("is_current", False)),
                    "bullets": bullets,
                }
            )

    return {
        "full_name": full_name,
        "headline": headline,
        "summary_base": summary_base,
        "contact": contact,
        "skills": skills,
        "languages": languages,
        "experiences": experiences,
    }


def parse_resume_text(
    text: str, session: Session, orchestrator: LLMOrchestrator | None = None
) -> dict[str, Any]:
    llm = orchestrator or LLMOrchestrator(session)
    prompt = f"Extract resume details from this resume text:\n\n{text}"
    raw_dict = llm.generate_json(prompt=prompt, system_prompt=INGESTION_SYSTEM_PROMPT)
    return normalize_parsed_profile(raw_dict)


def persist_profile_ingestion(
    session: Session, profile: MasterProfile, data: dict[str, Any]
) -> MasterProfile:
    assert profile.id is not None, "Profile must have an ID before persisting ingestion"

    try:
        if data.get("full_name"):
            profile.full_name = data["full_name"]
        if data.get("headline"):
            profile.headline = data["headline"]
        if data.get("summary_base"):
            profile.summary_base = data["summary_base"]
        if data.get("contact"):
            profile.contact_json = data["contact"]
        if data.get("skills"):
            profile.skills_json = data["skills"]
        if data.get("languages"):
            profile.languages_json = data["languages"]

        session.add(profile)

        # Remove existing experiences for atomic recreation
        existing_exps = session.exec(
            select(Experience).where(Experience.profile_id == profile.id)
        ).all()
        for exp in existing_exps:
            session.delete(exp)

        session.flush()

        # Insert extracted experiences
        for exp_data in data.get("experiences", []):
            new_exp = Experience(
                profile_id=profile.id,
                company=exp_data["company"],
                role=exp_data["role"],
                location=exp_data.get("location"),
                start_date=exp_data.get("start_date"),
                end_date=exp_data.get("end_date"),
                is_current=exp_data.get("is_current", False),
                bullets_json=exp_data.get("bullets", []),
            )
            session.add(new_exp)

        session.commit()
        session.refresh(profile)
        return profile
    except Exception:
        session.rollback()
        raise

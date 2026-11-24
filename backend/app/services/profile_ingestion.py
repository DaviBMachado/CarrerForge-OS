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
  "professional_experiences": [
    {
      "company": "Company Name",
      "role": "Job Title (formal employment, CLT, PJ, corporate internships)",
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
  ],
  "academic_projects": [
    {
      "company": "University / Institution Name",
      "role": "Project or Research Title (thesis, capstone, scientific research, scholarship)",
      "location": "Location or Remote",
      "start_date": "YYYY-MM or string",
      "end_date": "YYYY-MM or string or null",
      "is_current": false,
      "bullets": [
        {
          "text": "Research methodology, tools used, and scientific / academic outcome.",
          "tags": ["relevant", "skills"]
        }
      ]
    }
  ]
}
Return only valid JSON without markdown wrapping.
CRITICAL: Strictly separate commercial employment (professional_experiences)
from university research/thesis/capstone work (academic_projects).
Never omit formal corporate jobs when academic projects are present."""


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

    def _parse_exp_list(raw_list: Any, default_academic: bool) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        if not isinstance(raw_list, list):
            return parsed
        for exp in raw_list:
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

            is_acad = bool(exp.get("is_academic", default_academic))
            is_contract = bool(exp.get("is_contract", False))
            parsed.append(
                {
                    "company": company or ("Universidade" if is_acad else "Empresa"),
                    "role": role or ("Pesquisa / Projeto" if is_acad else "Cargo"),
                    "location": str(exp.get("location") or "").strip() or None,
                    "start_date": str(exp.get("start_date") or "").strip() or None,
                    "end_date": str(exp.get("end_date") or "").strip() or None,
                    "is_current": bool(exp.get("is_current", False)),
                    "is_academic": is_acad,
                    "is_contract": is_contract,
                    "bullets": bullets,
                }
            )
        return parsed

    experiences: list[dict[str, Any]] = []
    has_split = "professional_experiences" in data or "academic_projects" in data
    if has_split:
        experiences.extend(
            _parse_exp_list(data.get("professional_experiences"), default_academic=False)
        )
        experiences.extend(_parse_exp_list(data.get("academic_projects"), default_academic=True))
    if "experiences" in data and (not has_split or not experiences):
        experiences.extend(_parse_exp_list(data.get("experiences"), default_academic=False))

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
        if data.get("full_name") and not (profile.full_name or "").strip():
            profile.full_name = data["full_name"]

        if data.get("headline") and not (profile.headline or "").strip():
            profile.headline = data["headline"]
        if data.get("summary_base") and not (profile.summary_base or "").strip():
            profile.summary_base = data["summary_base"]

        # Merge contact
        contact = dict(profile.contact_json or {})
        for k, v in (data.get("contact") or {}).items():
            if v and not contact.get(k):
                contact[k] = v
        profile.contact_json = contact

        # Merge skills
        skills = dict(profile.skills_json or {})
        for cat, items in (data.get("skills") or {}).items():
            existing_items = list(skills.get(cat) or [])
            existing_lower = {str(x).lower().strip() for x in existing_items}
            for item in items:
                if str(item).lower().strip() not in existing_lower:
                    existing_items.append(item)
                    existing_lower.add(str(item).lower().strip())
            skills[cat] = existing_items
        profile.skills_json = skills

        # Merge languages
        langs = list(profile.languages_json or [])
        langs_lower = {str(x).lower().strip() for x in langs}
        for lang in data.get("languages") or []:
            if str(lang).lower().strip() not in langs_lower:
                langs.append(lang)
                langs_lower.add(str(lang).lower().strip())
        profile.languages_json = langs

        session.add(profile)
        session.flush()

        # Non-destructive merge of experiences by (company, role)
        existing_exps = session.exec(
            select(Experience).where(Experience.profile_id == profile.id)
        ).all()
        exp_map: dict[tuple[str, str], Experience] = {
            (e.company.strip().lower(), e.role.strip().lower()): e for e in existing_exps
        }

        for exp_data in data.get("experiences", []):
            comp_key = (exp_data["company"].strip().lower(), exp_data["role"].strip().lower())
            matched_exp = exp_map.get(comp_key)
            incoming_bullets = exp_data.get("bullets", [])

            if matched_exp:
                if not matched_exp.location and exp_data.get("location"):
                    matched_exp.location = exp_data["location"]
                if not matched_exp.start_date and exp_data.get("start_date"):
                    matched_exp.start_date = exp_data["start_date"]
                if not matched_exp.end_date and exp_data.get("end_date"):
                    matched_exp.end_date = exp_data["end_date"]
                if exp_data.get("is_contract"):
                    matched_exp.is_contract = True
                if exp_data.get("is_academic"):
                    matched_exp.is_academic = True

                curr_bullets = list(matched_exp.bullets_json or [])
                existing_bullet_texts = {
                    (b.get("text", "") if isinstance(b, dict) else str(b)).strip().lower()
                    for b in curr_bullets
                }
                for b in incoming_bullets:
                    b_text = (b.get("text", "") if isinstance(b, dict) else str(b)).strip()
                    if b_text and b_text.lower() not in existing_bullet_texts:
                        curr_bullets.append(b)
                        existing_bullet_texts.add(b_text.lower())
                matched_exp.bullets_json = curr_bullets
                session.add(matched_exp)
            else:
                new_exp = Experience(
                    user_id=profile.user_id,
                    profile_id=profile.id,
                    company=exp_data["company"],
                    role=exp_data["role"],
                    location=exp_data.get("location"),
                    start_date=exp_data.get("start_date"),
                    end_date=exp_data.get("end_date"),
                    is_current=exp_data.get("is_current", False),
                    is_academic=exp_data.get("is_academic", False),
                    is_contract=exp_data.get("is_contract", False),
                    bullets_json=incoming_bullets,
                )
                session.add(new_exp)
                exp_map[comp_key] = new_exp

        session.commit()
        session.refresh(profile)
        return profile
    except Exception:
        session.rollback()
        raise

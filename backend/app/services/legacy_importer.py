import re
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from backend.app.models import Experience, MasterProfile

_DATE_TOKEN_PATTERN = r"(?:\d{1,2}/\d{4}|\d{4}|[A-Za-zÀ-ÿ]{3,9}\.?\s+\d{4})"
_END_TOKEN_PATTERN = rf"(?:{_DATE_TOKEN_PATTERN}|presente?|atual|current)"
_DATE_RANGE_RE = re.compile(
    rf"^\s*({_DATE_TOKEN_PATTERN})\s*[-–—]\s*({_END_TOKEN_PATTERN})\s*$",
    re.IGNORECASE,
)
_SINGLE_DATE_RE = re.compile(rf"^\s*({_DATE_TOKEN_PATTERN})\s*$", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

_PROJECT_INDICATOR_RE = re.compile(
    r"\b(?:"
    r"projeto|projetos|project|projects|hackathon|case|cases|"
    r"prêmio|premio|award|\d+[º°]\s*lugar|\d+(?:st|nd|rd|th)\s*place|"
    r"desafio|challenge"
    r")\b",
    re.IGNORECASE,
)

_ROLE_INDICATOR_RE = re.compile(
    r"\b(?:"
    r"engenheiro|engenheira|desenvolvedor|desenvolvedora|estagiário|estagiária|"
    r"analista|arquiteto|arquiteta|tech\s+lead|líder|lider|consultor|consultora|"
    r"software\s+engineer|developer|intern|architect|manager|lead|specialist|"
    r"pleno|sênior|senior|júnior|junior|trainee"
    r")\b",
    re.IGNORECASE,
)

_TECH_LINE_RE = re.compile(
    r"^(?:\*\*)?(?:tecnologias|technologies|tech\s*stack|stack|ferramentas|tools)(?:\*\*)?\s*:\s*(.+)$",
    re.IGNORECASE,
)


def derive_tags(text: str, explicit_tag: str | None = None) -> list[str]:
    tags: set[str] = set()
    if explicit_tag:
        clean_exp = explicit_tag.strip().strip(":*.")
        if clean_exp and len(clean_exp.split()) <= 3:
            tags.add(clean_exp)

    keywords = [
        "Python",
        "AWS",
        "Lambda",
        "Step Functions",
        "S3",
        "Athena",
        "EC2",
        "IAM",
        "DynamoDB",
        "PostgreSQL",
        "MySQL",
        "SQL",
        "NoSQL",
        "REST",
        "APIs RESTful",
        "Angular",
        "TypeScript",
        "React",
        "Java",
        "Docker",
        "Terraform",
        "Serverless",
        "CI/CD",
        "Datadog",
        "CloudWatch",
        "ServiceNow",
        "pytest",
        "RAG",
        "LLM",
        "IARA",
        "LangChain",
        "FAISS",
        "boto3",
        "httpx",
        "Pydantic",
        "Scrum",
        "Multi-tenant",
        "JWT",
    ]
    for kw in keywords:
        pattern = rf"\b{re.escape(kw)}\b"
        if re.search(pattern, text, re.IGNORECASE):
            tags.add(kw)

    return sorted(tags)


def _extract_tech_list_tags(raw_tech_list: str) -> list[str]:
    extracted: list[str] = []
    for item in raw_tech_list.split(","):
        cleaned = re.sub(r"\([^)]*\)", "", item).strip().strip(".*:")
        if cleaned:
            extracted.append(cleaned)
    return extracted


def _parse_experience_heading(
    heading: str,
    current_section: str,
    last_employment_company: str | None,
) -> tuple[dict[str, Any], str | None]:
    paren_match = re.search(r"\(([^)]+)\)$", heading)
    paren_text = paren_match.group(1).strip() if paren_match else ""
    clean_heading = re.sub(r"\([^)]+\)$", "", heading).strip()

    date_range_match = _DATE_RANGE_RE.match(paren_text) if paren_text else None
    single_date_match = _SINGLE_DATE_RE.match(paren_text) if paren_text else None

    section_is_projects = bool(re.search(r"projeto|project", current_section, re.IGNORECASE))
    has_project_indicator = bool(
        _PROJECT_INDICATOR_RE.search(heading) or _PROJECT_INDICATOR_RE.search(paren_text)
    )
    has_pipe_separator = "|" in clean_heading
    has_role_indicator = bool(_ROLE_INDICATOR_RE.search(clean_heading))

    is_project = (
        section_is_projects
        or has_project_indicator
        or (not date_range_match and not (has_pipe_separator and single_date_match))
        or (not has_pipe_separator and not has_role_indicator)
    )

    start_date: str | None = None
    end_date: str | None = None
    is_current = False

    if date_range_match:
        start_date = date_range_match.group(1).strip()
        raw_end = date_range_match.group(2).strip()
        end_date = raw_end
        if re.search(r"presente?|atual|current", raw_end, re.IGNORECASE):
            is_current = True
    elif single_date_match:
        start_date = single_date_match.group(1).strip()
        end_date = start_date
    elif paren_text:
        year_match = _YEAR_RE.search(paren_text)
        if year_match:
            start_date = year_match.group(1)
            end_date = start_date

    if is_project:
        if has_pipe_separator:
            parts = [p.strip() for p in clean_heading.split("|", 1)]
            role = parts[0]
            base_company = parts[1]
        else:
            role = clean_heading
            base_company = last_employment_company or ""

        context_note = ""
        if paren_text and not date_range_match and not single_date_match:
            context_note = paren_text

        if base_company and context_note:
            company = f"{base_company} · Projeto ({context_note})"
        elif base_company:
            company = f"{base_company} · Projeto"
        elif context_note:
            company = f"Projeto ({context_note})"
        else:
            company = "Projeto"

        return (
            {
                "company": company,
                "role": role,
                "location": "São Paulo, SP",
                "start_date": start_date,
                "end_date": end_date,
                "is_current": is_current,
                "bullets_json": [],
            },
            last_employment_company,
        )

    # Standard employment entry
    if has_pipe_separator:
        parts = clean_heading.split("|", 1)
        role = parts[0].strip()
        company = parts[1].strip()
    elif "—" in clean_heading or "-" in clean_heading:
        sep = "—" if "—" in clean_heading else "-"
        parts = clean_heading.split(sep, 1)
        first, second = parts[0].strip(), parts[1].strip()
        if _ROLE_INDICATOR_RE.search(first) and not _ROLE_INDICATOR_RE.search(second):
            role, company = first, second
        else:
            company, role = first, second
    else:
        role = clean_heading
        company = last_employment_company or "Experiência Profissional"

    return (
        {
            "company": company,
            "role": role,
            "location": "São Paulo, SP",
            "start_date": start_date,
            "end_date": end_date,
            "is_current": is_current,
            "bullets_json": [],
        },
        company,
    )


def _finalize_experience_bullets(exp: dict[str, Any]) -> None:
    extra_tags: list[str] = exp.pop("_extra_tech_tags", [])
    if not extra_tags or not exp["bullets_json"]:
        return

    for bullet in exp["bullets_json"]:
        b_text = bullet["text"]
        b_tags = set(bullet["tags"])
        for tech in extra_tags:
            if re.search(rf"\b{re.escape(tech)}\b", b_text, re.IGNORECASE):
                b_tags.add(tech)
        bullet["tags"] = sorted(b_tags)

    # Ensure stack technologies declared for the project are present on the last bullet
    last_tags = set(exp["bullets_json"][-1]["tags"])
    last_tags.update(extra_tags)
    exp["bullets_json"][-1]["tags"] = sorted(last_tags)


def parse_markdown_resume(md_content: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "full_name": "",
        "headline": "",
        "contact_json": {},
        "summary_base": "",
        "skills_json": {},
        "languages_json": [],
        "experiences": [],
    }

    lines = [line.strip() for line in md_content.splitlines()]
    current_section = ""
    current_subsection = ""
    summary_lines = []
    current_exp: dict[str, Any] | None = None
    last_employment_company: str | None = None

    for line in lines:
        if line.startswith("# ") and not current_section:
            data["full_name"] = line[2:].strip()
            continue

        if not current_section and "|" in line:
            parts = [p.strip() for p in line.split("|")]
            for part in parts:
                if "@" in part:
                    data["contact_json"]["email"] = part
                elif re.search(r"\(\d{2}\)", part) or re.search(r"\d{4,5}-\d{4}", part):
                    data["contact_json"]["phone"] = part
                elif "linkedin.com" in part:
                    data["contact_json"]["linkedin"] = (
                        part if part.startswith("http") else f"https://{part}"
                    )
                elif "github.com" in part:
                    data["contact_json"]["github"] = (
                        part if part.startswith("http") else f"https://{part}"
                    )
                elif ("SP" in part or "São Paulo" in part) and "location" not in data[
                    "contact_json"
                ]:
                    data["contact_json"]["location"] = part
            continue

        if line.startswith("## "):
            sec_title = line[3:].strip()
            current_section = sec_title
            current_subsection = ""
            if current_exp:
                _finalize_experience_bullets(current_exp)
                data["experiences"].append(current_exp)
                current_exp = None
            continue

        if current_section == "Resumo Profissional":
            if line and not line.startswith("#"):
                summary_lines.append(line)
                data["summary_base"] = " ".join(summary_lines)

        elif current_section == "Competências Técnicas":
            if line.startswith("### "):
                current_subsection = line[4:].strip()
                data["skills_json"][current_subsection] = []
            elif line.startswith("- ") and current_subsection:
                items = [item.strip() for item in line[2:].split(",") if item.strip()]
                data["skills_json"][current_subsection].extend(items)

        elif current_section == "Idiomas":
            if line.startswith("- "):
                lang_text = line[2:].strip()
                if lang_text:
                    data["languages_json"].append(lang_text)

        elif current_section == "Experiência Profissional":
            if line.startswith("### "):
                if current_exp:
                    _finalize_experience_bullets(current_exp)
                    data["experiences"].append(current_exp)
                heading = line[4:].strip()
                current_exp, last_employment_company = _parse_experience_heading(
                    heading=heading,
                    current_section=current_section,
                    last_employment_company=last_employment_company,
                )

            elif line.startswith("- ") and current_exp:
                bullet_content = line[2:].strip()
                tech_match = _TECH_LINE_RE.match(bullet_content)
                if tech_match:
                    tech_tags = _extract_tech_list_tags(tech_match.group(1))
                    current_exp.setdefault("_extra_tech_tags", []).extend(tech_tags)
                    continue

                topic_match = re.match(r"^\*\*([^*:]+):?\*\*:?\s*(.*)$", bullet_content)
                if topic_match:
                    topic = topic_match.group(1).strip().rstrip(":")
                    body = topic_match.group(2).strip()
                    full_text = f"{topic}: {body}" if body else topic
                    tags = derive_tags(full_text)
                    current_exp["bullets_json"].append({"text": full_text, "tags": tags})
                else:
                    tags = derive_tags(bullet_content)
                    current_exp["bullets_json"].append({"text": bullet_content, "tags": tags})

    if current_exp:
        _finalize_experience_bullets(current_exp)
        data["experiences"].append(current_exp)

    return data


def import_legacy_data(
    session: Session,
    profile: MasterProfile,
    resume_path: Path | None = None,
) -> dict[str, Any]:
    if resume_path is None:
        resume_path = (
            Path(__file__).resolve().parent.parent.parent
            / "tests"
            / "fixtures"
            / "sample_resume.md"
        )

    if not resume_path.is_file():
        raise FileNotFoundError(f"Resume file not found at {resume_path}")

    md_content = resume_path.read_text(encoding="utf-8")
    parsed = parse_markdown_resume(md_content)

    # Update profile fields
    profile.full_name = parsed["full_name"]
    profile.headline = parsed["headline"]
    profile.contact_json = parsed["contact_json"]
    profile.summary_base = parsed["summary_base"]
    profile.skills_json = parsed["skills_json"]
    profile.languages_json = parsed["languages_json"]

    session.add(profile)
    session.flush()

    # Clear existing experiences for this profile to ensure clean re-import
    existing_exps = session.exec(
        select(Experience).where(Experience.profile_id == profile.id)
    ).all()
    for exp in existing_exps:
        session.delete(exp)
    session.flush()

    total_bullets = 0
    experiences_created = 0

    for exp_data in parsed["experiences"]:
        bullets = list(exp_data["bullets_json"])

        assert profile.id is not None
        experience = Experience(
            profile_id=profile.id,
            company=exp_data["company"],
            role=exp_data["role"],
            location=exp_data["location"],
            start_date=exp_data["start_date"],
            end_date=exp_data["end_date"],
            is_current=exp_data["is_current"],
            bullets_json=bullets,
        )
        session.add(experience)
        total_bullets += len(bullets)
        experiences_created += 1

    session.commit()
    session.refresh(profile)

    return {
        "status": "imported",
        "profile_id": profile.id,
        "experiences_count": experiences_created,
        "specs_scanned": 0,
        "total_bullets": total_bullets,
    }

import json
import re
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from backend.app.models import Experience, MasterProfile


def derive_tags(text: str, explicit_tag: str | None = None) -> list[str]:
    tags = set()
    if explicit_tag:
        clean_exp = explicit_tag.strip().strip(":*")
        if clean_exp:
            tags.add(clean_exp)

    keywords = [
        "Python",
        "AWS",
        "Lambda",
        "Step Functions",
        "S3",
        "Athena",
        "DynamoDB",
        "PostgreSQL",
        "MySQL",
        "REST",
        "API",
        "APIs RESTful",
        "Angular",
        "TypeScript",
        "React",
        "Java",
        "Docker",
        "Terraform",
        "CI/CD",
        "Datadog",
        "CloudWatch",
        "ServiceNow",
        "pytest",
        "RAG",
        "LLM",
        "LangChain",
        "FAISS",
        "Scrum",
        "Multi-tenant",
        "JWT",
    ]
    for kw in keywords:
        pattern = rf"\b{re.escape(kw)}\b"
        if re.search(pattern, text, re.IGNORECASE):
            tags.add(kw)

    return sorted(tags)


def parse_markdown_resume(md_content: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "full_name": "Davi Bernardes Machado",
        "headline": "Engenheiro de Software Backend | Python, AWS, IA Generativa",
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
                    data["experiences"].append(current_exp)
                heading = line[4:].strip()
                dates_match = re.search(r"\(([^)]+)\)$", heading)
                date_str = dates_match.group(1) if dates_match else ""
                clean_heading = re.sub(r"\([^)]+\)$", "", heading).strip()

                if "|" in clean_heading:
                    parts = clean_heading.split("|", 1)
                    role = parts[0].strip()
                    company = parts[1].strip()
                elif "—" in clean_heading or "-" in clean_heading:
                    sep = "—" if "—" in clean_heading else "-"
                    parts = clean_heading.split(sep, 1)
                    company = parts[0].strip()
                    role = parts[1].strip()
                else:
                    role = clean_heading
                    company = "Projetos Relevantes"

                start_date = None
                end_date = None
                is_current = False
                if date_str:
                    if "-" in date_str:
                        d_parts = date_str.split("-")
                        start_date = d_parts[0].strip()
                        end_date = d_parts[1].strip()
                        if "present" in end_date.lower() or "atual" in end_date.lower():
                            is_current = True
                    else:
                        start_date = date_str
                        end_date = date_str

                current_exp = {
                    "company": company,
                    "role": role,
                    "location": "São Paulo, SP",
                    "start_date": start_date,
                    "end_date": end_date,
                    "is_current": is_current,
                    "bullets_json": [],
                }

            elif line.startswith("- ") and current_exp:
                bullet_content = line[2:].strip()
                tag_match = re.match(r"^\*\*([^*:]+):?\*\*:?\s*(.*)$", bullet_content)
                if tag_match:
                    tag_name = tag_match.group(1).strip()
                    text = tag_match.group(2).strip() or bullet_content
                    tags = derive_tags(text, tag_name)
                    current_exp["bullets_json"].append({"text": text, "tags": tags})
                else:
                    tags = derive_tags(bullet_content)
                    current_exp["bullets_json"].append({"text": bullet_content, "tags": tags})

    if current_exp:
        data["experiences"].append(current_exp)

    return data


def scan_specs_directory(specs_dir: Path) -> tuple[int, list[dict[str, Any]]]:
    if not specs_dir.is_dir():
        return 0, []

    spec_files = list(specs_dir.glob("*.json"))
    additional_bullets: list[dict[str, Any]] = []

    for spec_file in spec_files:
        try:
            content = json.loads(spec_file.read_text(encoding="utf-8"))
            for section in content.get("sections", []):
                sec_title = section.get("title", "").lower()
                if "experiência" in sec_title or "experience" in sec_title:
                    for block in section.get("blocks", []):
                        if block.get("type") == "bullet":
                            b_text = block.get("text", "").strip()
                            if b_text:
                                tags = derive_tags(b_text)
                                additional_bullets.append({"text": b_text, "tags": tags})
        except Exception:
            continue

    return len(spec_files), additional_bullets


def import_legacy_data(
    session: Session,
    profile: MasterProfile,
    resume_path: Path | None = None,
    specs_dir: Path | None = None,
) -> dict[str, Any]:
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    if resume_path is None:
        resume_path = project_root / "general-resumes" / "Davi_Bernardes_Machado_Curriculo_ATS.md"
    if specs_dir is None:
        specs_dir = project_root / "_tools" / "specs"

    if not resume_path.is_file():
        raise FileNotFoundError(f"Resume file not found at {resume_path}")

    md_content = resume_path.read_text(encoding="utf-8")
    parsed = parse_markdown_resume(md_content)
    specs_count, spec_bullets = scan_specs_directory(specs_dir)

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
        # Merge spec bullets if this is the primary Itaú experience
        bullets = list(exp_data["bullets_json"])
        if "itaú" in exp_data["company"].lower():
            existing_texts = {b["text"].lower() for b in bullets}
            for sb in spec_bullets:
                clean_t = sb["text"].lower()
                if clean_t not in existing_texts:
                    existing_texts.add(clean_t)
                    bullets.append(sb)

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
        "specs_scanned": specs_count,
        "total_bullets": total_bullets,
    }

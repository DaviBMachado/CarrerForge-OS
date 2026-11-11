import { useEffect, useRef, useState } from "react";
import {
  api,
  BulletItem,
  ContactInfo,
  ExperienceItem,
  MasterProfile,
  SkillsMatrix,
} from "../api";
import { useAppStore } from "../store";

interface MasterProfileHubProps {
  onNotify: (type: "success" | "error", message: string) => void;
}

const DEFAULT_SKILL_CATEGORIES: SkillsMatrix = {
  Languages: [],
  Frameworks: [],
  "Cloud/DevOps": [],
};

function normalizeSkills(raw: unknown): SkillsMatrix {
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const entries = Object.entries(raw as Record<string, unknown>);
    if (entries.length > 0) {
      const result: SkillsMatrix = {};
      for (const [k, v] of entries) {
        result[k] = Array.isArray(v) ? v.map(String) : [];
      }
      return result;
    }
  }
  return { ...DEFAULT_SKILL_CATEGORIES };
}

function formatTagDisplay(tag: string): string {
  const clean = tag.trim().replace(/^#+/, "");
  return `#${clean}`;
}

export function MasterProfileHub({ onNotify }: MasterProfileHubProps) {
  const fetchProfile = useAppStore((s) => s.fetchProfile);
  const setProfileStore = useAppStore((s) => s.setProfile);

  const [profile, setProfile] = useState<MasterProfile | null>(null);
  const [loading, setLoading] = useState(!useAppStore.getState().profileLoaded);
  const [savingProfile, setSavingProfile] = useState(false);
  const [uploadingResume, setUploadingResume] = useState(false);
  const [isDraggingResume, setIsDraggingResume] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Personal & Contact state
  const [fullName, setFullName] = useState("");
  const [headline, setHeadline] = useState("");
  const [summaryBase, setSummaryBase] = useState("");
  const [contact, setContact] = useState<ContactInfo>({
    email: "",
    phone: "",
    location: "",
    linkedin: "",
    github: "",
  });
  const [languages, setLanguages] = useState<string[]>([]);
  const [newLanguage, setNewLanguage] = useState("");

  // Skills Matrix state
  const [skills, setSkills] = useState<SkillsMatrix>(DEFAULT_SKILL_CATEGORIES);
  const [skillInputs, setSkillInputs] = useState<Record<string, string>>({});
  const [newCategoryName, setNewCategoryName] = useState("");

  // Experiences state
  const [experiences, setExperiences] = useState<ExperienceItem[]>([]);
  const [expandedIds, setExpandedIds] = useState<Record<number, boolean>>({});
  const [savingExpId, setSavingExpId] = useState<number | null>(null);
  const [tagInputs, setTagInputs] = useState<Record<string, string>>({});

  // New Experience inline creator
  const [showNewExpForm, setShowNewExpForm] = useState(false);
  const [newExpCompany, setNewExpCompany] = useState("");
  const [newExpRole, setNewExpRole] = useState("");
  const [newExpLocation, setNewExpLocation] = useState("");
  const [newExpStart, setNewExpStart] = useState("");
  const [newExpEnd, setNewExpEnd] = useState("");
  const [newExpCurrent, setNewExpCurrent] = useState(false);
  const [newExpAcademic, setNewExpAcademic] = useState(false);
  const [creatingExp, setCreatingExp] = useState(false);

  const hydrateFromProfile = (data: MasterProfile) => {
    setProfile(data);
    setProfileStore(data);
    setFullName(data.full_name || "");
    setHeadline(data.headline || "");
    setSummaryBase(data.summary_base || "");
    const c =
      data.contact_json && typeof data.contact_json === "object"
        ? data.contact_json
        : {};
    setContact({
      email: c.email || "",
      phone: c.phone || "",
      location: c.location || "",
      linkedin: c.linkedin || "",
      github: c.github || "",
    });
    setLanguages(
      Array.isArray(data.languages_json) ? data.languages_json.map(String) : []
    );
    setSkills(normalizeSkills(data.skills_json));
    setExperiences(data.experiences || []);
    if (data.experiences && data.experiences.length > 0) {
      setExpandedIds((prev) =>
        Object.keys(prev).length > 0 ? prev : { [data.experiences[0].id]: true }
      );
    }
  };

  useEffect(() => {
    fetchProfile()
      .then((data) => {
        if (data) hydrateFromProfile(data);
        setLoading(false);
      })
      .catch((err: Error) => {
        onNotify("error", `Failed to load profile: ${err.message}`);
        setLoading(false);
      });
  }, []);

  const handleSaveProfile = async () => {
    setSavingProfile(true);
    try {
      const updated = await api.updateProfile({
        full_name: fullName.trim(),
        headline: headline.trim() || null,
        summary_base: summaryBase.trim() || null,
        contact_json: contact,
        skills_json: skills,
        languages_json: languages,
      });
      hydrateFromProfile(updated);
      onNotify("success", "Master profile details & skills matrix saved.");
    } catch (err) {
      onNotify(
        "error",
        err instanceof Error ? err.message : "Failed to save profile"
      );
    } finally {
      setSavingProfile(false);
    }
  };

  const handleResumeUpload = async (file: File) => {
    const ext = file.name.substring(file.name.lastIndexOf(".")).toLowerCase();
    const allowed = [".pdf", ".docx", ".md", ".txt"];
    if (!allowed.includes(ext)) {
      onNotify(
        "error",
        `Unsupported file type '${ext}'. Please upload a .pdf, .docx, .md, or .txt file.`
      );
      return;
    }

    setUploadingResume(true);
    try {
      const updated = await api.uploadResume(file);
      hydrateFromProfile(updated);
      onNotify(
        "success",
        `Resume parsed successfully! Extracted ${updated.experiences?.length || 0} experiences and updated profile.`
      );
    } catch (err) {
      onNotify(
        "error",
        err instanceof Error ? err.message : "Failed to parse and upload resume"
      );
    } finally {
      setUploadingResume(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  };

  // Skills Matrix handlers
  const handleAddSkill = (category: string) => {
    const raw = (skillInputs[category] || "").trim();
    if (!raw) return;
    setSkills((prev) => {
      const existing = prev[category] || [];
      if (existing.includes(raw)) return prev;
      return { ...prev, [category]: [...existing, raw] };
    });
    setSkillInputs((prev) => ({ ...prev, [category]: "" }));
  };

  const handleRemoveSkill = (category: string, skillToRemove: string) => {
    setSkills((prev) => ({
      ...prev,
      [category]: (prev[category] || []).filter((s) => s !== skillToRemove),
    }));
  };

  const handleAddCategory = () => {
    const cat = newCategoryName.trim();
    if (!cat || skills[cat]) return;
    setSkills((prev) => ({ ...prev, [cat]: [] }));
    setNewCategoryName("");
  };

  const handleDeleteCategory = (category: string) => {
    setSkills((prev) => {
      const next = { ...prev };
      delete next[category];
      return next;
    });
  };

  // Languages handlers
  const handleAddLanguage = () => {
    const lang = newLanguage.trim();
    if (!lang || languages.includes(lang)) return;
    setLanguages((prev) => [...prev, lang]);
    setNewLanguage("");
  };

  // Experience Accordion & CRUD handlers
  const toggleAccordion = (id: number) => {
    setExpandedIds((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const updateLocalExp = (
    id: number,
    updater: (exp: ExperienceItem) => ExperienceItem
  ) => {
    setExperiences((prev) =>
      prev.map((exp) => (exp.id === id ? updater(exp) : exp))
    );
  };

  const handleCreateExperience = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newExpCompany.trim() || !newExpRole.trim()) return;
    setCreatingExp(true);
    try {
      const created = await api.createExperience({
        company: newExpCompany.trim(),
        role: newExpRole.trim(),
        location: newExpLocation.trim() || null,
        start_date: newExpStart.trim() || null,
        end_date: newExpCurrent ? "Present" : newExpEnd.trim() || null,
        is_current: newExpCurrent,
        is_academic: newExpAcademic,
        bullets_json: [
          {
            text: "Delivered backend services and scalable APIs.",
            tags: ["python", "fastapi"],
          },
        ],
      });
      setExperiences((prev) => [...prev, created]);
      setExpandedIds((prev) => ({ ...prev, [created.id]: true }));
      setNewExpCompany("");
      setNewExpRole("");
      setNewExpLocation("");
      setNewExpStart("");
      setNewExpEnd("");
      setNewExpCurrent(false);
      setNewExpAcademic(false);
      setShowNewExpForm(false);
      onNotify("success", `Added experience at ${created.company}.`);
      void useAppStore.getState().fetchProfile(true);
    } catch (err) {
      onNotify(
        "error",
        err instanceof Error ? err.message : "Failed to add experience"
      );
    } finally {
      setCreatingExp(false);
    }
  };

  const handleSaveExperience = async (exp: ExperienceItem) => {
    const cleanedBullets: BulletItem[] = exp.bullets_json
      .map((b) => ({
        text: b.text.trim(),
        tags: b.tags.map((t) => t.trim().replace(/^#+/, "")).filter(Boolean),
      }))
      .filter((b) => b.text.length > 0);

    setSavingExpId(exp.id);
    try {
      const saved = await api.updateExperience(exp.id, {
        company: exp.company.trim() || "Company",
        role: exp.role.trim() || "Role",
        location: exp.location?.trim() || null,
        start_date: exp.start_date?.trim() || null,
        end_date: exp.end_date?.trim() || null,
        is_current: exp.is_current,
        is_academic: Boolean(exp.is_academic),
        bullets_json: cleanedBullets,
      });
      updateLocalExp(exp.id, () => saved);
      onNotify("success", `Saved experience: ${saved.role} @ ${saved.company}`);
      void useAppStore.getState().fetchProfile(true);
    } catch (err) {
      onNotify(
        "error",
        err instanceof Error ? err.message : "Failed to update experience"
      );
    } finally {
      setSavingExpId(null);
    }
  };

  const handleDeleteExperience = async (id: number, company: string) => {
    try {
      await api.deleteExperience(id);
      setExperiences((prev) => prev.filter((e) => e.id !== id));
      onNotify("success", `Removed ${company} from Master Profile.`);
      void useAppStore.getState().fetchProfile(true);
    } catch (err) {
      onNotify(
        "error",
        err instanceof Error ? err.message : "Failed to delete experience"
      );
    }
  };

  const handleAddBullet = (expId: number) => {
    updateLocalExp(expId, (exp) => ({
      ...exp,
      bullets_json: [
        ...exp.bullets_json,
        { text: "New achievement or technical contribution", tags: [] },
      ],
    }));
  };

  const handleBulletTextChange = (
    expId: number,
    bulletIdx: number,
    text: string
  ) => {
    updateLocalExp(expId, (exp) => ({
      ...exp,
      bullets_json: exp.bullets_json.map((b, idx) =>
        idx === bulletIdx ? { ...b, text } : b
      ),
    }));
  };

  const handleDeleteBullet = (expId: number, bulletIdx: number) => {
    updateLocalExp(expId, (exp) => ({
      ...exp,
      bullets_json: exp.bullets_json.filter((_, idx) => idx !== bulletIdx),
    }));
  };

  const handleAddBulletTag = (expId: number, bulletIdx: number) => {
    const key = `${expId}-${bulletIdx}`;
    const rawTag = (tagInputs[key] || "").trim().replace(/^#+/, "");
    if (!rawTag) return;

    updateLocalExp(expId, (exp) => ({
      ...exp,
      bullets_json: exp.bullets_json.map((b, idx) => {
        if (idx !== bulletIdx) return b;
        if (b.tags.some((t) => t.toLowerCase() === rawTag.toLowerCase())) {
          return b;
        }
        return { ...b, tags: [...b.tags, rawTag] };
      }),
    }));
    setTagInputs((prev) => ({ ...prev, [key]: "" }));
  };

  const handleRemoveBulletTag = (
    expId: number,
    bulletIdx: number,
    tagToRemove: string
  ) => {
    updateLocalExp(expId, (exp) => ({
      ...exp,
      bullets_json: exp.bullets_json.map((b, idx) =>
        idx === bulletIdx
          ? { ...b, tags: b.tags.filter((t) => t !== tagToRemove) }
          : b
      ),
    }));
  };

  if (loading) {
    return (
      <section className="panel" aria-busy="true">
        <p>Loading Master Profile Hub...</p>
      </section>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <div className="hub-header">
        <div>
          <h1 style={{ fontSize: "1.5rem" }}>Master Profile Hub</h1>
          <p style={{ color: "var(--ink-secondary)", fontSize: "0.9rem" }}>
            Single source of truth for personal details, categorized skills, and
            atomic tagged work experiences.
          </p>
        </div>

        <div className="hub-actions">
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleSaveProfile}
            disabled={savingProfile || uploadingResume}
          >
            {savingProfile ? "Saving Profile..." : "Save Profile & Skills"}
          </button>
        </div>
      </div>

      {/* Resume Upload Dropzone */}
      <section
        className={`panel ${isDraggingResume ? "drag-over" : ""}`}
        style={{
          border: isDraggingResume
            ? "2px dashed var(--accent)"
            : "2px dashed var(--border-subtle)",
          backgroundColor: isDraggingResume
            ? "var(--accent-tint)"
            : "var(--bg-surface)",
          padding: "1.5rem",
          borderRadius: "8px",
          textAlign: "center",
          transition: "border-color 0.15s ease, background-color 0.15s ease",
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!uploadingResume) setIsDraggingResume(true);
        }}
        onDragLeave={() => setIsDraggingResume(false)}
        onDrop={(e) => {
          e.preventDefault();
          setIsDraggingResume(false);
          if (!uploadingResume && e.dataTransfer.files?.[0]) {
            handleResumeUpload(e.dataTransfer.files[0]);
          }
        }}
        aria-label="Upload resume document"
      >
        <input
          type="file"
          ref={fileInputRef}
          style={{ display: "none" }}
          accept=".pdf,.docx,.md,.txt"
          onChange={(e) => {
            if (e.target.files?.[0]) {
              handleResumeUpload(e.target.files[0]);
            }
          }}
        />

        {uploadingResume ? (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: "0.75rem",
              padding: "0.5rem 0",
            }}
          >
            <div
              className="spinner"
              style={{
                width: "2rem",
                height: "2rem",
                border: "3px solid var(--border-subtle)",
                borderTopColor: "var(--accent)",
                borderRadius: "50%",
              }}
            />
            <div style={{ fontWeight: 600, color: "var(--accent)" }}>
              Extraindo texto e estruturando perfil...
            </div>
            <div style={{ fontSize: "0.85rem", color: "var(--ink-secondary)" }}>
              A IA está analisando seu currículo e mapeando contatos, habilidades e experiências.
            </div>
          </div>
        ) : (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: "0.5rem",
            }}
          >
            <div style={{ fontSize: "1rem", fontWeight: 600 }}>
              Ingestão Inteligente de Currículo
            </div>
            <p
              style={{
                fontSize: "0.85rem",
                color: "var(--ink-secondary)",
                margin: 0,
                maxWidth: "600px",
              }}
            >
              Arraste e solte seu arquivo de currículo (.pdf, .docx, .md, .txt) aqui ou clique
              no botão abaixo para estruturar automaticamente suas experiências e habilidades.
            </p>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "1rem",
                marginTop: "0.5rem",
              }}
            >
              <button
                type="button"
                className="btn"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploadingResume}
              >
                Selecionar Arquivo
              </button>
              <span
                style={{ fontSize: "0.75rem", color: "var(--ink-secondary)" }}
              >
                Formatos aceitos: PDF, DOCX, Markdown, TXT (máx. 10MB)
              </span>
            </div>
          </div>
        )}
      </section>

      <div className="hub-grid">
        {/* 1. Contact & Personal Information Card */}
        <section className="panel" aria-labelledby="personal-info-heading">
          <div className="panel-header">
            <h2 id="personal-info-heading" className="panel-title">
              Contact & Personal Information
            </h2>
            {profile && (
              <span
                style={{ fontSize: "0.75rem", color: "var(--ink-secondary)" }}
              >
                ID #{profile.id}
              </span>
            )}
          </div>

          <div className="form-grid">
            <div className="field">
              <label className="field-label" htmlFor="profile-full-name">
                Full Name
              </label>
              <input
                id="profile-full-name"
                className="input"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="ex: Jane Doe"
              />
            </div>

            <div className="field">
              <label className="field-label" htmlFor="profile-headline">
                Professional Headline
              </label>
              <input
                id="profile-headline"
                className="input"
                value={headline}
                onChange={(e) => setHeadline(e.target.value)}
                placeholder="Senior Backend Engineer | Python & Cloud"
              />
            </div>

            <div className="field">
              <label className="field-label" htmlFor="profile-email">
                Email
              </label>
              <input
                id="profile-email"
                className="input"
                type="email"
                value={contact.email || ""}
                onChange={(e) =>
                  setContact((prev) => ({ ...prev, email: e.target.value }))
                }
                placeholder="name@example.com"
              />
            </div>

            <div className="field">
              <label className="field-label" htmlFor="profile-phone">
                Phone
              </label>
              <input
                id="profile-phone"
                className="input"
                value={contact.phone || ""}
                onChange={(e) =>
                  setContact((prev) => ({ ...prev, phone: e.target.value }))
                }
                placeholder="+55 (11) 99999-9999"
              />
            </div>

            <div className="field">
              <label className="field-label" htmlFor="profile-location">
                Location
              </label>
              <input
                id="profile-location"
                className="input"
                value={contact.location || ""}
                onChange={(e) =>
                  setContact((prev) => ({ ...prev, location: e.target.value }))
                }
                placeholder="São Paulo, SP"
              />
            </div>

            <div className="field">
              <label className="field-label" htmlFor="profile-linkedin">
                LinkedIn URL
              </label>
              <input
                id="profile-linkedin"
                className="input"
                value={contact.linkedin || ""}
                onChange={(e) =>
                  setContact((prev) => ({ ...prev, linkedin: e.target.value }))
                }
                placeholder="https://linkedin.com/in/..."
              />
            </div>

            <div className="field" style={{ gridColumn: "1 / -1" }}>
              <label className="field-label" htmlFor="profile-github">
                GitHub / Portfolio URL
              </label>
              <input
                id="profile-github"
                className="input"
                value={contact.github || ""}
                onChange={(e) =>
                  setContact((prev) => ({ ...prev, github: e.target.value }))
                }
                placeholder="https://github.com/..."
              />
            </div>
          </div>

          <div className="field">
            <label className="field-label" htmlFor="profile-summary">
              Base Executive Summary
            </label>
            <textarea
              id="profile-summary"
              className="textarea"
              value={summaryBase}
              onChange={(e) => setSummaryBase(e.target.value)}
              placeholder="Core professional summary used as foundation for tailored resumes..."
            />
          </div>

          <div className="field">
            <span className="field-label">Spoken Languages</span>
            <div className="tag-list">
              {languages.map((lang) => (
                <span key={lang} className="tag-chip">
                  {lang}
                  <button
                    type="button"
                    className="tag-remove"
                    onClick={() =>
                      setLanguages((prev) => prev.filter((l) => l !== lang))
                    }
                    aria-label={`Remove language ${lang}`}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            <div className="inline-add" style={{ marginTop: "0.25rem" }}>
              <input
                className="input"
                value={newLanguage}
                onChange={(e) => setNewLanguage(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    handleAddLanguage();
                  }
                }}
                placeholder="Add language (e.g. Português Nativo, Inglês Avançado)"
                aria-label="New language"
              />
              <button
                type="button"
                className="btn btn-sm"
                onClick={handleAddLanguage}
              >
                Add
              </button>
            </div>
          </div>
        </section>

        {/* 2. Categorized Skills Matrix */}
        <section className="panel" aria-labelledby="skills-matrix-heading">
          <div className="panel-header">
            <h2 id="skills-matrix-heading" className="panel-title">
              Categorized Skills Matrix
            </h2>
            <div className="inline-add">
              <input
                className="input"
                style={{ width: "160px", padding: "0.25rem 0.5rem" }}
                value={newCategoryName}
                onChange={(e) => setNewCategoryName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    handleAddCategory();
                  }
                }}
                placeholder="New category..."
                aria-label="New skill category name"
              />
              <button
                type="button"
                className="btn btn-sm"
                onClick={handleAddCategory}
              >
                + Category
              </button>
            </div>
          </div>

          <div
            style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}
          >
            {Object.entries(skills).map(([category, items]) => (
              <div key={category} className="skill-category">
                <div className="skill-category-header">
                  <strong style={{ fontSize: "0.875rem" }}>{category}</strong>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => handleDeleteCategory(category)}
                    aria-label={`Remove category ${category}`}
                  >
                    Remove
                  </button>
                </div>

                <div className="tag-list">
                  {items.length === 0 && (
                    <span
                      style={{
                        fontSize: "0.8rem",
                        color: "var(--ink-secondary)",
                      }}
                    >
                      No skills added yet.
                    </span>
                  )}
                  {items.map((skill) => (
                    <span key={skill} className="tag-chip">
                      {skill}
                      <button
                        type="button"
                        className="tag-remove"
                        onClick={() => handleRemoveSkill(category, skill)}
                        aria-label={`Remove ${skill} from ${category}`}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>

                <div className="inline-add">
                  <input
                    className="input"
                    value={skillInputs[category] || ""}
                    onChange={(e) =>
                      setSkillInputs((prev) => ({
                        ...prev,
                        [category]: e.target.value,
                      }))
                    }
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        handleAddSkill(category);
                      }
                    }}
                    placeholder={`Add skill to ${category}...`}
                    aria-label={`Add skill to ${category}`}
                  />
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => handleAddSkill(category)}
                  >
                    Add
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* 3. Work Experiences Accordion List */}
      <section className="panel" aria-labelledby="experiences-heading">
        <div className="panel-header">
          <div>
            <h2 id="experiences-heading" className="panel-title">
              Work Experiences ({experiences.length})
            </h2>
            <p style={{ fontSize: "0.8125rem", color: "var(--ink-secondary)" }}>
              Expand any role to edit metadata, atomic bullet points, and
              competency tags (e.g. #python, #fastapi).
            </p>
          </div>

          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setShowNewExpForm((prev) => !prev)}
          >
            {showNewExpForm ? "Cancel" : "+ Add Experience"}
          </button>
        </div>

        {showNewExpForm && (
          <form
            onSubmit={handleCreateExperience}
            className="skill-category"
            style={{ gap: "0.875rem" }}
          >
            <h3 style={{ fontSize: "0.95rem" }}>New Work Experience</h3>
            <div className="form-grid">
              <div className="field">
                <label className="field-label" htmlFor="new-exp-role">
                  Role / Title *
                </label>
                <input
                  id="new-exp-role"
                  className="input"
                  required
                  value={newExpRole}
                  onChange={(e) => setNewExpRole(e.target.value)}
                  placeholder="Senior Backend Developer"
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="new-exp-company">
                  Company *
                </label>
                <input
                  id="new-exp-company"
                  className="input"
                  required
                  value={newExpCompany}
                  onChange={(e) => setNewExpCompany(e.target.value)}
                  placeholder="Acme Corp"
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="new-exp-location">
                  Location
                </label>
                <input
                  id="new-exp-location"
                  className="input"
                  value={newExpLocation}
                  onChange={(e) => setNewExpLocation(e.target.value)}
                  placeholder="Remote / São Paulo, SP"
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="new-exp-start">
                  Start Date
                </label>
                <input
                  id="new-exp-start"
                  className="input"
                  value={newExpStart}
                  onChange={(e) => setNewExpStart(e.target.value)}
                  placeholder="Jan 2023"
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="new-exp-end">
                  End Date
                </label>
                <input
                  id="new-exp-end"
                  className="input"
                  disabled={newExpCurrent}
                  value={newExpCurrent ? "Present" : newExpEnd}
                  onChange={(e) => setNewExpEnd(e.target.value)}
                  placeholder="Present"
                />
              </div>
              <div
                className="field"
                style={{
                  justifyContent: "flex-end",
                  paddingBottom: "0.4rem",
                  flexDirection: "row",
                  gap: "1rem",
                  alignItems: "center",
                }}
              >
                <label
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "0.5rem",
                    fontSize: "0.875rem",
                    cursor: "pointer",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={newExpCurrent}
                    onChange={(e) => setNewExpCurrent(e.target.checked)}
                  />
                  Current Role
                </label>
                <label
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "0.5rem",
                    fontSize: "0.875rem",
                    cursor: "pointer",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={newExpAcademic}
                    onChange={(e) => setNewExpAcademic(e.target.checked)}
                  />
                  Projeto Acadêmico
                </label>
              </div>
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.5rem" }}>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={creatingExp}
              >
                {creatingExp ? "Creating..." : "Create Experience"}
              </button>
            </div>
          </form>
        )}

        {experiences.length === 0 ? (
          <p style={{ color: "var(--ink-secondary)", fontSize: "0.9rem" }}>
            No work experiences recorded yet. Use "+ Add Experience" or "Import
            Legacy Resume" to populate your career timeline.
          </p>
        ) : (
          <div className="accordion-list">
            {experiences.map((exp) => {
              const isOpen = Boolean(expandedIds[exp.id]);
              const panelId = `exp-panel-${exp.id}`;
              const endLabel = exp.is_current ? "Present" : exp.end_date;
              const dateRange =
                exp.start_date && endLabel && exp.start_date !== endLabel
                  ? `${exp.start_date} — ${endLabel}`
                  : exp.start_date || endLabel || "N/A";

              return (
                <div key={exp.id} className="accordion-item">
                  <h3>
                    <button
                      type="button"
                      className="accordion-trigger"
                      aria-expanded={isOpen}
                      aria-controls={panelId}
                      onClick={() => toggleAccordion(exp.id)}
                    >
                      <div className="accordion-summary">
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "0.5rem",
                            flexWrap: "wrap",
                          }}
                        >
                          <span className="accordion-role">
                            {exp.role} · {exp.company}
                          </span>
                          {exp.is_academic && (
                            <span
                              className="status-pill"
                              style={{
                                fontSize: "0.7rem",
                                fontWeight: 600,
                                padding: "0.1rem 0.45rem",
                                backgroundColor:
                                  "var(--accent-tint, rgba(99, 102, 241, 0.15))",
                                color: "var(--accent, #6366f1)",
                                borderColor: "var(--accent, #6366f1)",
                              }}
                            >
                              Projeto Acadêmico
                            </span>
                          )}
                        </div>
                        <span className="accordion-meta">
                          {dateRange}
                          {exp.location ? ` · ${exp.location}` : ""} ·{" "}
                          {exp.bullets_json.length} atomic{" "}
                          {exp.bullets_json.length === 1 ? "bullet" : "bullets"}
                        </span>
                      </div>
                      <span aria-hidden="true" style={{ fontSize: "0.85rem" }}>
                        {isOpen ? "▲" : "▼"}
                      </span>
                    </button>
                  </h3>

                  {isOpen && (
                    <div id={panelId} className="accordion-body">
                      <div className="form-grid">
                        <div className="field">
                          <label
                            className="field-label"
                            htmlFor={`exp-role-${exp.id}`}
                          >
                            Role
                          </label>
                          <input
                            id={`exp-role-${exp.id}`}
                            className="input"
                            value={exp.role}
                            onChange={(e) =>
                              updateLocalExp(exp.id, (cur) => ({
                                ...cur,
                                role: e.target.value,
                              }))
                            }
                          />
                        </div>

                        <div className="field">
                          <label
                            className="field-label"
                            htmlFor={`exp-company-${exp.id}`}
                          >
                            Company
                          </label>
                          <input
                            id={`exp-company-${exp.id}`}
                            className="input"
                            value={exp.company}
                            onChange={(e) =>
                              updateLocalExp(exp.id, (cur) => ({
                                ...cur,
                                company: e.target.value,
                              }))
                            }
                          />
                        </div>

                        <div className="field">
                          <label
                            className="field-label"
                            htmlFor={`exp-location-${exp.id}`}
                          >
                            Location
                          </label>
                          <input
                            id={`exp-location-${exp.id}`}
                            className="input"
                            value={exp.location || ""}
                            onChange={(e) =>
                              updateLocalExp(exp.id, (cur) => ({
                                ...cur,
                                location: e.target.value,
                              }))
                            }
                          />
                        </div>

                        <div className="field">
                          <label
                            className="field-label"
                            htmlFor={`exp-start-${exp.id}`}
                          >
                            Start Date
                          </label>
                          <input
                            id={`exp-start-${exp.id}`}
                            className="input"
                            value={exp.start_date || ""}
                            onChange={(e) =>
                              updateLocalExp(exp.id, (cur) => ({
                                ...cur,
                                start_date: e.target.value,
                              }))
                            }
                          />
                        </div>

                        <div className="field">
                          <label
                            className="field-label"
                            htmlFor={`exp-end-${exp.id}`}
                          >
                            End Date
                          </label>
                          <input
                            id={`exp-end-${exp.id}`}
                            className="input"
                            value={exp.end_date || ""}
                            onChange={(e) =>
                              updateLocalExp(exp.id, (cur) => ({
                                ...cur,
                                end_date: e.target.value,
                              }))
                            }
                          />
                        </div>

                        <div
                          className="field"
                          style={{
                            justifyContent: "flex-end",
                            paddingBottom: "0.4rem",
                            flexDirection: "row",
                            gap: "1rem",
                            alignItems: "center",
                          }}
                        >
                          <label
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: "0.5rem",
                              fontSize: "0.875rem",
                              cursor: "pointer",
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={exp.is_current}
                              onChange={(e) =>
                                updateLocalExp(exp.id, (cur) => ({
                                  ...cur,
                                  is_current: e.target.checked,
                                }))
                              }
                            />
                            Currently working here
                          </label>
                          <label
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: "0.5rem",
                              fontSize: "0.875rem",
                              cursor: "pointer",
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={Boolean(exp.is_academic)}
                              onChange={(e) =>
                                updateLocalExp(exp.id, (cur) => ({
                                  ...cur,
                                  is_academic: e.target.checked,
                                }))
                              }
                            />
                            Projeto Acadêmico
                          </label>
                        </div>
                      </div>

                      <div className="bullet-list">
                        <div
                          style={{
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                          }}
                        >
                          <strong style={{ fontSize: "0.875rem" }}>
                            Atomic Bullet Points & Competency Tags
                          </strong>
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() => handleAddBullet(exp.id)}
                          >
                            + Add Bullet
                          </button>
                        </div>

                        {exp.bullets_json.map((bullet, bIdx) => {
                          const tagKey = `${exp.id}-${bIdx}`;
                          return (
                            <div key={bIdx} className="bullet-card">
                              <div className="bullet-row">
                                <textarea
                                  className="textarea"
                                  style={{ minHeight: "54px" }}
                                  value={bullet.text}
                                  onChange={(e) =>
                                    handleBulletTextChange(
                                      exp.id,
                                      bIdx,
                                      e.target.value
                                    )
                                  }
                                  aria-label={`Bullet ${bIdx + 1} text`}
                                />
                                <button
                                  type="button"
                                  className="btn btn-sm btn-danger"
                                  onClick={() =>
                                    handleDeleteBullet(exp.id, bIdx)
                                  }
                                  aria-label={`Delete bullet ${bIdx + 1}`}
                                >
                                  Delete
                                </button>
                              </div>

                              <div
                                style={{
                                  display: "flex",
                                  flexWrap: "wrap",
                                  justifyContent: "space-between",
                                  gap: "0.5rem",
                                  alignItems: "center",
                                }}
                              >
                                <div className="tag-list">
                                  {bullet.tags.map((tag) => (
                                    <span key={tag} className="tag-chip">
                                      {formatTagDisplay(tag)}
                                      <button
                                        type="button"
                                        className="tag-remove"
                                        onClick={() =>
                                          handleRemoveBulletTag(
                                            exp.id,
                                            bIdx,
                                            tag
                                          )
                                        }
                                        aria-label={`Remove tag ${tag}`}
                                      >
                                        ×
                                      </button>
                                    </span>
                                  ))}
                                </div>

                                <div className="inline-add">
                                  <input
                                    className="input"
                                    style={{
                                      width: "170px",
                                      padding: "0.2rem 0.5rem",
                                      fontSize: "0.8rem",
                                    }}
                                    value={tagInputs[tagKey] || ""}
                                    onChange={(e) =>
                                      setTagInputs((prev) => ({
                                        ...prev,
                                        [tagKey]: e.target.value,
                                      }))
                                    }
                                    onKeyDown={(e) => {
                                      if (e.key === "Enter") {
                                        e.preventDefault();
                                        handleAddBulletTag(exp.id, bIdx);
                                      }
                                    }}
                                    placeholder="#python, #fastapi..."
                                    aria-label={`Add tag to bullet ${bIdx + 1}`}
                                  />
                                  <button
                                    type="button"
                                    className="btn btn-sm"
                                    onClick={() =>
                                      handleAddBulletTag(exp.id, bIdx)
                                    }
                                  >
                                    + Tag
                                  </button>
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>

                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                          paddingTop: "0.5rem",
                          borderTop: "1px solid var(--border-subtle)",
                        }}
                      >
                        <button
                          type="button"
                          className="btn btn-danger btn-sm"
                          onClick={() =>
                            handleDeleteExperience(exp.id, exp.company)
                          }
                        >
                          Delete Experience
                        </button>

                        <button
                          type="button"
                          className="btn btn-primary"
                          onClick={() => handleSaveExperience(exp)}
                          disabled={savingExpId === exp.id}
                        >
                          {savingExpId === exp.id
                            ? "Saving..."
                            : "Save Experience Changes"}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}

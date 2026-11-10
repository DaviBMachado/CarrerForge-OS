import { useEffect, useState } from "react";
import {
  api,
  GupyRundown,
  JobPostingItem,
  MasterProfile,
  PreviewResponse,
  ResumeBlock,
  ResumeSection,
  ResumeSpecPayload,
  SolveQuestionResponse,
} from "../api";
import { base64ToPdfBlob } from "./GeneralResumeBuilder";
import { useAppStore } from "../store";

interface TailoringStudioProps {
  selectedJob: JobPostingItem | null;
  onSelectJob: (job: JobPostingItem) => void;
  onNotify: (type: "success" | "error", message: string) => void;
}

type StudioTab = "resume" | "cover-letter" | "gupy";

const KNOWN_KEYWORDS = [
  "python",
  "fastapi",
  "django",
  "flask",
  "react",
  "typescript",
  "javascript",
  "postgresql",
  "sql",
  "aws",
  "lambda",
  "step functions",
  "s3",
  "docker",
  "kubernetes",
  "terraform",
  "llm",
  "rag",
  "langchain",
  "rest",
  "graphql",
  "pytest",
  "ci/cd",
];

function profileToInitialSpec(
  profile: MasterProfile | null,
  job: JobPostingItem | null
): ResumeSpecPayload {
  if (
    !profile ||
    (!profile.full_name && (!profile.experiences || profile.experiences.length === 0))
  ) {
    return {
      name: "",
      title: "",
      contact: "",
      filename_title: job
        ? `Resume_${job.company.replace(/\s+/g, "_")}`
        : "Resume",
      sections: [],
    };
  }

  const c = profile.contact_json || {};
  const contactLine = [c.email, c.phone, c.location, c.linkedin, c.github]
    .filter(Boolean)
    .join(" | ");

  const sections: ResumeSection[] = [];
  if (profile.summary_base || profile.headline) {
    sections.push({
      title: "Professional Summary",
      blocks: [
        {
          type: "paragraph",
          text:
            profile.summary_base ||
            (profile.headline
              ? `${profile.full_name} — ${profile.headline}`
              : profile.full_name),
        },
      ],
    });
  }

  if (profile.skills_json && Object.keys(profile.skills_json).length > 0) {
    const skillParts = Object.entries(profile.skills_json)
      .filter(([, items]) => Array.isArray(items) && items.length > 0)
      .map(([cat, items]) => `**${cat}:** ${(items as string[]).join(", ")}`);
    if (skillParts.length > 0) {
      sections.push({
        title: "Technical Skills",
        blocks: [{ type: "skillline", text: skillParts.join(" || ") }],
      });
    }
  }

  if (profile.experiences && profile.experiences.length > 0) {
    const expBlocks: ResumeBlock[] = [];
    for (const exp of profile.experiences) {
      const period = [exp.start_date, exp.is_current ? "Present" : exp.end_date]
        .filter(Boolean)
        .join(" - ");
      expBlocks.push({
        type: "subheading",
        text: `${exp.role} | ${exp.company}${period ? ` (${period})` : ""}`,
      });
      for (const b of exp.bullets_json || []) {
        if (b.text.trim()) {
          expBlocks.push({ type: "bullet", text: b.text.trim() });
        }
      }
    }
    sections.push({ title: "Professional Experience", blocks: expBlocks });
  }

  const name = profile.full_name || "";
  const title = profile.headline
    ? (name ? `${name} - ${profile.headline}` : profile.headline)
    : name;

  return {
    name,
    title,
    contact: contactLine,
    filename_title: job
      ? `Resume_${name ? `${name.replace(/\s+/g, "_")}_` : ""}${job.company.replace(/\s+/g, "_")}`
      : name ? `Resume_${name.replace(/\s+/g, "_")}` : "Resume",
    sections,
  };
}

function normalizeSections(sections: ResumeSection[]): ResumeSection[] {
  return sections.map((sec) => {
    const blocks: ResumeBlock[] = [...(sec.blocks || [])];
    if (sec.bullets && sec.bullets.length > 0) {
      for (const b of sec.bullets) {
        blocks.push({ type: "bullet", text: b });
      }
    }
    return { title: sec.title, blocks };
  });
}

function deriveLocalKeywords(
  jobText: string,
  profile: MasterProfile | null
): { keywords: string[]; matchPct: number } {
  const lowerJob = (jobText || "").toLowerCase();
  const jobMatches = KNOWN_KEYWORDS.filter((kw) => lowerJob.includes(kw));
  if (jobMatches.length === 0) {
    return { keywords: [], matchPct: 0 };
  }
  if (!profile) {
    return { keywords: jobMatches, matchPct: 0 };
  }
  const profileBlob = JSON.stringify(profile).toLowerCase();
  const grounded = jobMatches.filter((kw) => profileBlob.includes(kw));
  const pct = Math.round((grounded.length / jobMatches.length) * 100);
  return { keywords: grounded.length > 0 ? grounded : jobMatches, matchPct: pct };
}

export function TailoringStudio({
  selectedJob,
  onSelectJob,
  onNotify,
}: TailoringStudioProps) {
  const jobs = useAppStore((s) => s.jobs);
  const profile = useAppStore((s) => s.profile);
  const fetchJobs = useAppStore((s) => s.fetchJobs);
  const fetchProfile = useAppStore((s) => s.fetchProfile);
  const [activeTab, setActiveTab] = useState<StudioTab>("resume");

  const [persona, setPersona] = useState<string>(() => profile?.headline || "");
  const [language, setLanguage] = useState<string>("en");
  const [atsKeywords, setAtsKeywords] = useState<string[]>([]);
  const [matchPercentage, setMatchPercentage] = useState<number>(0);

  const [resumeSpec, setResumeSpec] = useState<ResumeSpecPayload>(() =>
    profileToInitialSpec(profile, selectedJob)
  );
  const [coverLetter, setCoverLetter] = useState<string>("");

  const [rundown, setRundown] = useState<GupyRundown | null>(null);
  const [questionInput, setQuestionInput] = useState<string>("");
  const [qaHistory, setQaHistory] = useState<SolveQuestionResponse[]>([]);

  const [tailoring, setTailoring] = useState(false);
  const [solvingQuestion, setSolvingQuestion] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [compiling, setCompiling] = useState(false);
  const [exportingFormat, setExportingFormat] = useState<"pdf" | "docx" | null>(
    null
  );

  // Load jobs and profile on mount
  useEffect(() => {
    Promise.all([
      fetchJobs().catch(() => [] as JobPostingItem[]),
      fetchProfile().catch(() => null),
    ]).then(([loadedJobs, loadedProfile]) => {
      const initialJob = selectedJob || loadedJobs[0] || null;
      if (initialJob && !selectedJob) {
        onSelectJob(initialJob);
      }
      if (
        loadedProfile &&
        (loadedProfile.full_name ||
          (loadedProfile.experiences && loadedProfile.experiences.length > 0))
      ) {
        setResumeSpec((prev) => {
          const isClean =
            !prev.name && (!prev.sections || prev.sections.length === 0);
          return isClean
            ? profileToInitialSpec(loadedProfile, initialJob)
            : prev;
        });
        setPersona((prev) => prev || loadedProfile.headline || "");
      }
    });
  }, []);

  // Synchronize clean resume spec if profile changes in store
  useEffect(() => {
    if (
      profile &&
      (profile.full_name ||
        (profile.experiences && profile.experiences.length > 0))
    ) {
      setResumeSpec((prev) => {
        const isClean =
          !prev.name && (!prev.sections || prev.sections.length === 0);
        return isClean ? profileToInitialSpec(profile, selectedJob) : prev;
      });
      setPersona((prev) => prev || profile.headline || "");
    }
  }, [profile, selectedJob]);

  // Sync job-specific rundown, existing tailored artifact, and baseline keywords when selectedJob changes
  useEffect(() => {
    if (!selectedJob) return;
    const jobText = selectedJob.cleaned_text || selectedJob.raw_description || "";
    const local = deriveLocalKeywords(jobText, profile);
    setAtsKeywords(local.keywords);
    setMatchPercentage(local.matchPct);
    setLanguage(selectedJob.language || "en");

    api
      .getTailoredJob(selectedJob.id)
      .then((tailored) => {
        setPersona(tailored.selected_persona);
        setLanguage(tailored.language);
        setAtsKeywords(tailored.ats_keywords);
        setMatchPercentage(tailored.match_percentage);
        setResumeSpec({
          ...tailored.resume_spec,
          sections: normalizeSections(tailored.resume_spec.sections || []),
        });
        setCoverLetter(tailored.cover_letter_text);
      })
      .catch(() => {
        // Not tailored yet: fallback to initial spec from master profile if empty
        if (profile) {
          setResumeSpec((prev) => {
            const isClean =
              !prev.name && (!prev.sections || prev.sections.length === 0);
            return isClean ? profileToInitialSpec(profile, selectedJob) : prev;
          });
        }
      });

    api
      .getGupyRundown(selectedJob.id)
      .then((data) => {
        setRundown(data);
        if (data.selected_persona) setPersona(data.selected_persona);
        if (data.language) setLanguage(data.language);
      })
      .catch(() => {
        // Fallback if rundown cannot be fetched
      });
  }, [selectedJob?.id, profile]);

  // Debounced live PDF compilation whenever resumeSpec changes
  useEffect(() => {
    let cancelled = false;
    setCompiling(true);

    const timer = window.setTimeout(() => {
      const isSpecEmpty =
        !resumeSpec.name &&
        !resumeSpec.title &&
        (!resumeSpec.sections || resumeSpec.sections.length === 0);

      if (isSpecEmpty) {
        setPreview(null);
        setCompiling(false);
        return;
      }

      api
        .previewResume(resumeSpec)
        .then((res) => {
          if (cancelled) return;
          setPreview(res);
          setCurrentPage((prev) => Math.min(Math.max(1, prev), res.page_count));
        })
        .catch((err: Error) => {
          if (!cancelled) {
            onNotify("error", `Preview compilation failed: ${err.message}`);
          }
        })
        .finally(() => {
          if (!cancelled) setCompiling(false);
        });
    }, 300);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [resumeSpec]);

  // Manage Blob URL lifecycle for the in-memory PDF viewer
  useEffect(() => {
    if (!preview?.pdf_base64) {
      setPdfUrl(null);
      return;
    }
    const blob = base64ToPdfBlob(preview.pdf_base64);
    const url = URL.createObjectURL(blob);
    setPdfUrl(url);
    return () => {
      URL.revokeObjectURL(url);
    };
  }, [preview?.pdf_base64]);

  const handleCopy = async (id: string, text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Fallback copy if clipboard API is restricted
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setCopiedId(id);
    onNotify("success", `Copied ${label} to clipboard.`);
    window.setTimeout(() => {
      setCopiedId((prev) => (prev === id ? null : prev));
    }, 2000);
  };

  const handleRunTailor = async () => {
    if (!selectedJob) {
      onNotify("error", "Select a job posting first to run AI tailoring.");
      return;
    }
    setTailoring(true);
    try {
      const result = await api.tailorJob(selectedJob.id);
      setPersona(result.selected_persona);
      setLanguage(result.language);
      setAtsKeywords(result.ats_keywords);
      setMatchPercentage(result.match_percentage);
      setResumeSpec({
        ...result.resume_spec,
        sections: normalizeSections(result.resume_spec.sections || []),
      });
      setCoverLetter(result.cover_letter_text);

      // Refresh Gupy rundown with newly tailored summary
      api
        .getGupyRundown(selectedJob.id)
        .then((r) => setRundown(r))
        .catch(() => {});

      onNotify(
        "success",
        `Tailored for ${selectedJob.company} using "${result.selected_persona}" lens (${result.match_percentage}% ATS match).`
      );
    } catch (err) {
      onNotify(
        "error",
        err instanceof Error ? err.message : "AI Tailoring failed"
      );
    } finally {
      setTailoring(false);
    }
  };

  const handleSolveQuestion = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedJob) {
      onNotify("error", "Select a job posting before solving screening questions.");
      return;
    }
    const q = questionInput.trim();
    if (q.length < 2) {
      onNotify("error", "Enter a screening question (at least 2 characters).");
      return;
    }
    setSolvingQuestion(true);
    try {
      const res = await api.solveQuestion(selectedJob.id, q);
      setQaHistory((prev) => [res, ...prev]);
      setQuestionInput("");
      onNotify("success", "Drafted grounded screening answer.");
    } catch (err) {
      onNotify(
        "error",
        err instanceof Error ? err.message : "Failed to generate answer"
      );
    } finally {
      setSolvingQuestion(false);
    }
  };

  const handleUpdateBlock = (
    sectionIdx: number,
    blockIdx: number,
    newText: string
  ) => {
    setResumeSpec((prev) => {
      const nextSections = prev.sections.map((sec, sIdx) => {
        if (sIdx !== sectionIdx) return sec;
        const nextBlocks = (sec.blocks || []).map((blk, bIdx) =>
          bIdx === blockIdx ? { ...blk, text: newText } : blk
        );
        return { ...sec, blocks: nextBlocks };
      });
      return { ...prev, sections: nextSections };
    });
  };

  const handleAddBullet = (sectionIdx: number) => {
    setResumeSpec((prev) => {
      const nextSections = prev.sections.map((sec, sIdx) => {
        if (sIdx !== sectionIdx) return sec;
        return {
          ...sec,
          blocks: [
            ...(sec.blocks || []),
            {
              type: "bullet" as const,
              text: "Delivered measurable impact aligned with target role requirements.",
            },
          ],
        };
      });
      return { ...prev, sections: nextSections };
    });
  };

  const handleRemoveBlock = (sectionIdx: number, blockIdx: number) => {
    setResumeSpec((prev) => {
      const nextSections = prev.sections.map((sec, sIdx) => {
        if (sIdx !== sectionIdx) return sec;
        return {
          ...sec,
          blocks: (sec.blocks || []).filter((_, bIdx) => bIdx !== blockIdx),
        };
      });
      return { ...prev, sections: nextSections };
    });
  };

  const handleExportResume = async (format: "pdf" | "docx") => {
    setExportingFormat(format);
    try {
      const { blob, filename } = await api.exportResume(resumeSpec, format);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      onNotify("success", `Downloaded ${filename}`);
    } catch (err) {
      onNotify(
        "error",
        err instanceof Error
          ? err.message
          : `Failed to export ${format.toUpperCase()}`
      );
    } finally {
      setExportingFormat(null);
    }
  };

  const handleExportCoverLetter = async (format: "pdf" | "docx") => {
    setExportingFormat(format);
    try {
      const { blob, filename } = await api.exportCoverLetter(
        {
          name: resumeSpec.name || "",
          title: resumeSpec.title || "Cover Letter",
          contact: resumeSpec.contact || "",
          recipient: selectedJob
            ? `Hiring Team — ${selectedJob.company}`
            : "Hiring Manager",
          subject: selectedJob ? `Application: ${selectedJob.title}` : "",
          body: coverLetter,
          filename_title: selectedJob
            ? `Cover_Letter_${selectedJob.company.replace(/\s+/g, "_")}`
            : "Cover_Letter",
        },
        format
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      onNotify("success", `Downloaded ${filename}`);
    } catch (err) {
      onNotify(
        "error",
        err instanceof Error ? err.message : "Failed to export Cover Letter"
      );
    } finally {
      setExportingFormat(null);
    }
  };

  const pageCount = preview?.page_count ?? 1;
  const isOverflow = preview ? !preview.is_valid_2_page || pageCount > 2 : false;
  const fillPercent = Math.min(100, Math.round((pageCount / 2) * 100));
  const meterColor = isOverflow
    ? "var(--danger)"
    : pageCount === 2
      ? "var(--success)"
      : "var(--accent)";

  return (
    <div className="studio-shell">
      {/* Studio Top Bar */}
      <div className="hub-header">
        <div>
          <h1 style={{ fontSize: "1.45rem" }}>
            LLM Tailoring Studio & Dual-Mode Workstation
          </h1>
          <p style={{ color: "var(--ink-secondary)", fontSize: "0.875rem" }}>
            Factual ATS alignment, autonomous persona delegation, Gupy Q&A
            solver, and live 2-page PDF budget preview.
          </p>
        </div>

        <div className="hub-actions" style={{ alignItems: "center" }}>
          <div className="status-pill" title="Autonomous Persona Lens">
            <span
              className="status-dot"
              style={{ backgroundColor: "var(--accent)" }}
            />
            <span>Persona: {persona}</span>
          </div>

          <div className="status-pill" title="Detected Target Language">
            <span>Lang: {language.toUpperCase()}</span>
          </div>

          <button
            type="button"
            className="btn btn-primary"
            onClick={handleRunTailor}
            disabled={tailoring || !selectedJob}
          >
            {tailoring ? "Tailoring with AI..." : "✨ Tailor with AI"}
          </button>
        </div>
      </div>

      {/* 3-Pane Split Workstation */}
      <div className="studio-grid">
        {/* LEFT PANE: Job Spec & ATS Keywords */}
        <aside
          className="panel studio-pane"
          aria-labelledby="studio-left-heading"
        >
          <div className="panel-header">
            <h2 id="studio-left-heading" className="panel-title">
              Job Spec & ATS Match
            </h2>
            <span
              className="status-pill"
              style={{
                borderColor:
                  matchPercentage >= 70
                    ? "var(--success)"
                    : matchPercentage >= 40
                      ? "var(--warning)"
                      : "var(--border-strong)",
                color: "var(--ink-primary)",
                fontWeight: 600,
              }}
            >
              {matchPercentage}% Match
            </span>
          </div>

          <div className="field" style={{ flexShrink: 0 }}>
            <label className="field-label" htmlFor="studio-job-select">
              Target Job Posting
            </label>
            <select
              id="studio-job-select"
              className="select"
              value={selectedJob?.id ?? ""}
              onChange={(e) => {
                const found = jobs.find((j) => j.id === Number(e.target.value));
                if (found) onSelectJob(found);
              }}
            >
              {jobs.length === 0 ? (
                <option value="">No jobs discovered yet</option>
              ) : (
                jobs.map((j) => (
                  <option key={j.id} value={j.id}>
                    {j.title} — {j.company} ({j.platform})
                  </option>
                ))
              )}
            </select>
          </div>

          {selectedJob ? (
            (() => {
              const jobText =
                selectedJob.cleaned_text || selectedJob.raw_description || "";
              const wordCount = jobText.trim()
                ? jobText.trim().split(/\s+/).length
                : 0;
              const isShort = wordCount > 0 && wordCount < 50;

              return (
                <>
                  <div className="studio-job-summary">
                    <div style={{ fontWeight: 600, fontSize: "0.95rem" }}>
                      {selectedJob.title}
                    </div>
                    <div
                      style={{
                        fontSize: "0.825rem",
                        color: "var(--ink-secondary)",
                        display: "flex",
                        flexWrap: "wrap",
                        gap: "0.4rem",
                        alignItems: "center",
                        marginTop: "0.2rem",
                      }}
                    >
                      <span>{selectedJob.company}</span>
                      <span>•</span>
                      <span>{selectedJob.location || "Remote / Flexible"}</span>
                    </div>
                    <div
                      style={{
                        display: "flex",
                        flexWrap: "wrap",
                        gap: "0.35rem",
                        alignItems: "center",
                        marginTop: "0.5rem",
                      }}
                    >
                      <span
                        className="status-pill"
                        style={{ fontSize: "0.75rem", padding: "0.15rem 0.5rem" }}
                      >
                        Modalidade: {selectedJob.remote_type || "Não especificada"}
                      </span>
                      <span
                        className="status-pill"
                        style={{ fontSize: "0.75rem", padding: "0.15rem 0.5rem" }}
                      >
                        Status: {selectedJob.status}
                      </span>
                      <span
                        className="status-pill"
                        style={{ fontSize: "0.75rem", padding: "0.15rem 0.5rem" }}
                      >
                        {selectedJob.platform.toUpperCase()}
                      </span>
                      {isShort && (
                        <span
                          className="status-pill"
                          style={{
                            fontSize: "0.75rem",
                            padding: "0.15rem 0.5rem",
                            borderColor: "var(--warning)",
                            color: "var(--warning)",
                          }}
                        >
                          Descrição curta ({wordCount} palavras)
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="field" style={{ flexShrink: 0 }}>
                    <span className="field-label">
                      Grounded ATS Keywords ({atsKeywords.length})
                    </span>
                    <div className="tag-list" aria-label="Extracted ATS keywords">
                      {atsKeywords.length === 0 ? (
                        <span
                          style={{
                            fontSize: "0.8rem",
                            color: "var(--ink-placeholder)",
                          }}
                        >
                          Click &quot;Tailor with AI&quot; to extract keywords.
                        </span>
                      ) : (
                        atsKeywords.map((kw) => (
                          <span key={kw} className="tag-chip">
                            {kw}
                          </span>
                        ))
                      )}
                    </div>
                  </div>

                  <div
                    className="field"
                    style={{
                      flex: 1,
                      display: "flex",
                      flexDirection: "column",
                      minHeight: 0,
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        marginBottom: "0.35rem",
                        flexShrink: 0,
                      }}
                    >
                      <span className="field-label" style={{ marginBottom: 0 }}>
                        Job Description
                      </span>
                      <span
                        style={{
                          fontSize: "0.75rem",
                          color: "var(--ink-secondary)",
                        }}
                      >
                        {wordCount} palavras
                      </span>
                    </div>
                    <div
                      className="studio-job-scroll"
                      tabIndex={0}
                      role="region"
                      aria-label="Full job description"
                    >
                      {jobText || "No job description available."}
                    </div>
                  </div>
                </>
              );
            })()
          ) : (
            <p style={{ color: "var(--ink-secondary)", fontSize: "0.875rem" }}>
              Select a scraped job from Job Radar or use the dropdown above to
              inspect its description and ATS keywords.
            </p>
          )}
        </aside>

        {/* CENTER PANE: Dual-Mode Editor Tabs */}
        <section
          className="panel studio-pane"
          aria-label="Tailoring Editor Workspace"
        >
          <div className="studio-tabs-header">
            <div
              className="nav-tabs"
              role="tablist"
              aria-label="Tailoring Studio Editor Mode"
            >
              <button
                type="button"
                role="tab"
                id="studio-tab-resume"
                aria-selected={activeTab === "resume"}
                aria-controls="studio-panel-resume"
                className={`nav-tab ${activeTab === "resume" ? "active" : ""}`}
                onClick={() => setActiveTab("resume")}
              >
                Resume Editor
              </button>
              <button
                type="button"
                role="tab"
                id="studio-tab-cover"
                aria-selected={activeTab === "cover-letter"}
                aria-controls="studio-panel-cover"
                className={`nav-tab ${activeTab === "cover-letter" ? "active" : ""}`}
                onClick={() => setActiveTab("cover-letter")}
              >
                Cover Letter
              </button>
              <button
                type="button"
                role="tab"
                id="studio-tab-gupy"
                aria-selected={activeTab === "gupy"}
                aria-controls="studio-panel-gupy"
                className={`nav-tab ${activeTab === "gupy" ? "active" : ""}`}
                onClick={() => setActiveTab("gupy")}
              >
                Gupy / Form Assistant
              </button>
            </div>
          </div>

          {/* TAB 1: Resume Editor */}
          {activeTab === "resume" && (
            <div
              id="studio-panel-resume"
              role="tabpanel"
              aria-labelledby="studio-tab-resume"
              className="studio-tab-body"
            >
              <div className="form-grid">
                <div className="field">
                  <label className="field-label" htmlFor="spec-header-title">
                    Header Title & Focus Lens
                  </label>
                  <input
                    id="spec-header-title"
                    className="input"
                    value={resumeSpec.title || ""}
                    onChange={(e) =>
                      setResumeSpec((prev) => ({
                        ...prev,
                        title: e.target.value,
                      }))
                    }
                  />
                </div>
                <div className="field">
                  <label className="field-label" htmlFor="spec-header-contact">
                    Contact Line
                  </label>
                  <input
                    id="spec-header-contact"
                    className="input"
                    value={
                      Array.isArray(resumeSpec.contact)
                        ? resumeSpec.contact.join(" | ")
                        : resumeSpec.contact || ""
                    }
                    onChange={(e) =>
                      setResumeSpec((prev) => ({
                        ...prev,
                        contact: e.target.value,
                      }))
                    }
                  />
                </div>
              </div>

              <div className="studio-sections-scroll">
                {resumeSpec.sections.length === 0 ? (
                  <div
                    style={{
                      textAlign: "center",
                      padding: "2.5rem 1rem",
                      color: "var(--ink-secondary)",
                    }}
                  >
                    <p style={{ margin: "0 0 0.5rem 0", fontWeight: 600 }}>
                      No resume content yet
                    </p>
                    <p style={{ margin: 0, fontSize: "0.85rem" }}>
                      Configure your Master Profile or click &quot;✨ Tailor with AI&quot; to generate your customized resume.
                    </p>
                  </div>
                ) : (
                  resumeSpec.sections.map((sec, sIdx) => (
                  <div key={`${sec.title}-${sIdx}`} className="skill-category">
                    <div className="skill-category-header">
                      <strong style={{ fontSize: "0.9rem" }}>{sec.title}</strong>
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={() => handleAddBullet(sIdx)}
                      >
                        + Add Bullet
                      </button>
                    </div>

                    <div className="bullet-list">
                      {(sec.blocks || []).map((blk, bIdx) => (
                        <div key={bIdx} className="bullet-row">
                          {blk.type === "subheading" ? (
                            <input
                              aria-label={`${sec.title} subheading ${bIdx + 1}`}
                              className="input"
                              style={{ fontWeight: 600 }}
                              value={blk.text}
                              onChange={(e) =>
                                handleUpdateBlock(sIdx, bIdx, e.target.value)
                              }
                            />
                          ) : (
                            <textarea
                              aria-label={`${sec.title} ${blk.type} ${bIdx + 1}`}
                              className="textarea"
                              style={{ minHeight: "62px" }}
                              value={blk.text}
                              onChange={(e) =>
                                handleUpdateBlock(sIdx, bIdx, e.target.value)
                              }
                            />
                          )}
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() =>
                              void handleCopy(
                                `blk-${sIdx}-${bIdx}`,
                                blk.text,
                                "block"
                              )
                            }
                            title="Copy block text"
                          >
                            {copiedId === `blk-${sIdx}-${bIdx}` ? "✓" : "Copy"}
                          </button>
                          <button
                            type="button"
                            className="tag-remove"
                            onClick={() => handleRemoveBlock(sIdx, bIdx)}
                            aria-label="Remove block"
                            title="Remove block"
                          >
                            ✕
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )))}
              </div>
            </div>
          )}

          {/* TAB 2: Cover Letter */}
          {activeTab === "cover-letter" && (
            <div
              id="studio-panel-cover"
              role="tabpanel"
              aria-labelledby="studio-tab-cover"
              className="studio-tab-body"
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: "0.5rem",
                  flexWrap: "wrap",
                }}
              >
                <span className="field-label">
                  3-Paragraph Tailored Cover Letter
                </span>
                <div className="inline-add">
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() =>
                      void handleCopy(
                        "cover-letter-copy",
                        coverLetter,
                        "Cover Letter"
                      )
                    }
                  >
                    {copiedId === "cover-letter-copy"
                      ? "✓ Copied"
                      : "Copy Text"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => void handleExportCoverLetter("docx")}
                    disabled={exportingFormat !== null}
                  >
                    Export DOCX
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-primary"
                    onClick={() => void handleExportCoverLetter("pdf")}
                    disabled={exportingFormat !== null}
                  >
                    Export PDF
                  </button>
                </div>
              </div>

              <textarea
                id="cover-letter-textarea"
                aria-label="Cover Letter Editor"
                className="textarea"
                placeholder="Click '✨ Tailor with AI' to generate a tailored cover letter, or write one here..."
                style={{ minHeight: "480px", lineHeight: 1.6 }}
                value={coverLetter}
                onChange={(e) => setCoverLetter(e.target.value)}
              />
            </div>
          )}

          {/* TAB 3: Gupy / Form Assistant */}
          {activeTab === "gupy" && (
            <div
              id="studio-panel-gupy"
              role="tabpanel"
              aria-labelledby="studio-tab-gupy"
              className="studio-tab-body"
            >
              {/* 1-Click Copy Profile Rundown Blocks */}
              <div className="skill-category">
                <div className="skill-category-header">
                  <strong style={{ fontSize: "0.875rem" }}>
                    Tailored Short Bio (Gupy / LinkedIn Summary)
                  </strong>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() =>
                      void handleCopy(
                        "gupy-bio",
                        rundown?.short_bio ||
                          resumeSpec.sections[0]?.blocks?.[0]?.text ||
                          "",
                        "Short Bio"
                      )
                    }
                  >
                    {copiedId === "gupy-bio" ? "✓ Copied" : "Copy Bio"}
                  </button>
                </div>
                <p
                  style={{
                    fontSize: "0.825rem",
                    color: "var(--ink-secondary)",
                  }}
                >
                  {rundown?.short_bio ||
                    resumeSpec.sections[0]?.blocks?.[0]?.text ||
                    "Run tailoring or select a job to populate your tailored bio."}
                </p>
              </div>

              <div className="skill-category">
                <div className="skill-category-header">
                  <strong style={{ fontSize: "0.875rem" }}>
                    Key Impact Metrics (1-Click Copy)
                  </strong>
                </div>
                {rundown?.impact_metrics &&
                rundown.impact_metrics.length > 0 ? (
                  <div className="bullet-list">
                    {rundown.impact_metrics.map((metric, idx) => (
                      <div
                        key={idx}
                        className="studio-copy-row"
                      >
                        <span style={{ fontSize: "0.8125rem" }}>{metric}</span>
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() =>
                            void handleCopy(
                              `metric-${idx}`,
                              metric,
                              "Impact Metric"
                            )
                          }
                        >
                          {copiedId === `metric-${idx}` ? "✓" : "Copy"}
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p
                    style={{
                      fontSize: "0.8125rem",
                      color: "var(--ink-placeholder)",
                    }}
                  >
                    Quantified achievements from your Master Profile will appear
                    here.
                  </p>
                )}
              </div>

              <div className="skill-category">
                <div className="skill-category-header">
                  <strong style={{ fontSize: "0.875rem" }}>
                    Prioritized Skill Tags
                  </strong>
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() =>
                      void handleCopy(
                        "gupy-skills",
                        (rundown?.prioritized_skill_tags || atsKeywords).join(
                          ", "
                        ),
                        "Skill Tags"
                      )
                    }
                  >
                    {copiedId === "gupy-skills" ? "✓ Copied All" : "Copy All"}
                  </button>
                </div>
                <div className="tag-list">
                  {(rundown?.prioritized_skill_tags || atsKeywords).map(
                    (tag) => (
                      <span key={tag} className="tag-chip">
                        {tag}
                      </span>
                    )
                  )}
                </div>
              </div>

              {/* Interactive Screening Q&A Solver */}
              <div className="skill-category">
                <strong style={{ fontSize: "0.9rem" }}>
                  Interactive Screening Q&A Solver
                </strong>
                <form
                  onSubmit={handleSolveQuestion}
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "0.6rem",
                  }}
                >
                  <label className="field-label" htmlFor="gupy-question-input">
                    Paste Gupy / ATS Screening Question
                  </label>
                  <textarea
                    id="gupy-question-input"
                    className="textarea"
                    style={{ minHeight: "68px" }}
                    placeholder="e.g., How do you handle ambiguous requirements or scale backend workflows?"
                    value={questionInput}
                    onChange={(e) => setQuestionInput(e.target.value)}
                  />
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "flex-end",
                      gap: "0.5rem",
                    }}
                  >
                    <button
                      type="submit"
                      className="btn btn-primary btn-sm"
                      disabled={solvingQuestion || !selectedJob}
                    >
                      {solvingQuestion
                        ? "Drafting Answer..."
                        : "Solve Screening Question"}
                    </button>
                  </div>
                </form>

                {qaHistory.length > 0 && (
                  <div className="bullet-list" style={{ marginTop: "0.5rem" }}>
                    {qaHistory.map((item, idx) => (
                      <div key={idx} className="bullet-card">
                        <div
                          style={{
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "flex-start",
                            gap: "0.5rem",
                          }}
                        >
                          <strong style={{ fontSize: "0.825rem" }}>
                            Q: {item.question}
                          </strong>
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() =>
                              void handleCopy(
                                `qa-${idx}`,
                                item.answer,
                                "Screening Answer"
                              )
                            }
                          >
                            {copiedId === `qa-${idx}`
                              ? "✓ Copied"
                              : "Copy Answer"}
                          </button>
                        </div>
                        <p
                          style={{
                            fontSize: "0.825rem",
                            color: "var(--ink-primary)",
                            whiteSpace: "pre-wrap",
                          }}
                        >
                          {item.answer}
                        </p>
                        {item.grounded_experiences.length > 0 && (
                          <div className="tag-list">
                            {item.grounded_experiences.map((exp) => (
                              <span key={exp} className="tag-chip">
                                Grounded: {exp}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </section>

        {/* RIGHT PANE: Live ATS Preview & 2-Page Budget Meter */}
        <aside
          className="panel studio-pane"
          aria-labelledby="studio-right-heading"
          style={{ overflowY: "auto" }}
        >
          <div className="panel-header">
            <div>
              <h2 id="studio-right-heading" className="panel-title">
                Live ATS Preview
              </h2>
              <span
                style={{ fontSize: "0.78rem", color: "var(--ink-secondary)" }}
              >
                {compiling ? "Compiling PDF..." : "Synced with compiler"}
              </span>
            </div>

            <div className="inline-add">
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => void handleExportResume("docx")}
                disabled={exportingFormat !== null}
              >
                {exportingFormat === "docx" ? "..." : "DOCX"}
              </button>
              <button
                type="button"
                className="btn btn-sm btn-primary"
                onClick={() => void handleExportResume("pdf")}
                disabled={exportingFormat !== null}
              >
                {exportingFormat === "pdf" ? "..." : "Download PDF"}
              </button>
            </div>
          </div>

          {/* Compact 2-Page Budget Meter */}
          <div
            className={`budget-meter-card ${isOverflow ? "overflow" : ""}`}
            aria-label="2-Page Budget Meter"
          >
            <div className="budget-meter-header">
              <strong style={{ fontSize: "0.85rem" }}>Page Budget</strong>
              <span
                className="status-pill"
                style={{
                  borderColor: isOverflow ? "var(--danger)" : "var(--success)",
                  color: "var(--ink-primary)",
                }}
              >
                <span
                  className="status-dot"
                  style={{
                    backgroundColor: isOverflow
                      ? "var(--danger)"
                      : "var(--success)",
                  }}
                />
                <span>
                  {pageCount} / 2 pages{" "}
                  {isOverflow ? "(Overflow!)" : "(ATS Ready)"}
                </span>
              </span>
            </div>

            <div
              className="budget-track"
              role="progressbar"
              aria-valuenow={pageCount}
              aria-valuemin={0}
              aria-valuemax={2}
            >
              <div
                className="budget-fill"
                style={{
                  width: `${fillPercent}%`,
                  backgroundColor: meterColor,
                }}
              />
            </div>

            {isOverflow && (
              <div className="budget-alert" role="alert">
                <span>⚠️</span>
                <span>
                  Exceeds 2-page limit! Prune bullets in Resume Editor.
                </span>
              </div>
            )}
          </div>

          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <span
              style={{ fontSize: "0.8rem", color: "var(--ink-secondary)" }}
            >
              Carlito TrueType • A4
            </span>
            <nav className="inline-add" aria-label="PDF page navigation">
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                disabled={currentPage <= 1}
              >
                ←
              </button>
              <span style={{ fontSize: "0.8rem", fontWeight: 600 }}>
                Page {currentPage} of {pageCount}
              </span>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() =>
                  setCurrentPage((p) => Math.min(pageCount, p + 1))
                }
                disabled={currentPage >= pageCount}
              >
                →
              </button>
            </nav>
          </div>

          {pdfUrl ? (
            <iframe
              key={`${pdfUrl}#page=${currentPage}`}
              src={`${pdfUrl}#page=${currentPage}&view=FitH`}
              title="Live Tailored Resume PDF Preview"
              className="pdf-viewport studio-pdf-viewport"
            />
          ) : (
            <div
              className="pdf-viewport studio-pdf-viewport"
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "var(--ink-secondary)",
                textAlign: "center",
                padding: "2rem",
              }}
            >
              <p>
                {compiling
                  ? "Generating PDF preview..."
                  : "Nenhum conteúdo de currículo para exibir na prévia."}
              </p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

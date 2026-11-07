import { useEffect, useState } from "react";
import {
  api,
  MasterProfile,
  PreviewResponse,
  ResumeBlock,
  ResumeSection,
  ResumeSpecPayload,
} from "../api";
import { useAppStore } from "../store";

export interface GeneralResumeBuilderProps {
  onNotify: (type: "success" | "error", message: string) => void;
}

export const OVERFLOW_PARAGRAPH = `
### Principal Platform Architect — Global Scale Enterprise (2016 – 2020)
- Led a cross-functional engineering organization of 45 developers delivering mission-critical financial clearing systems across 12 regions.
- Spearheaded the migration from legacy monolithic batch processors to event-driven microservices, improving system availability from 99.5% to 99.99%.
- Implemented automated compliance auditing, cryptographic ledger verification, and real-time anomaly detection for institutional portfolios.
- Authored internal engineering RFCs covering database sharding, zero-downtime schema migrations, and distributed tracing standards.
- Mentored senior engineers, conducted architectural reviews, and established incident response playbooks that reduced MTTR by 70%.`;

/**
 * Parses lightweight markdown-like resume text into a structured ResumeSpecPayload.
 */
export function parseTextToSpec(raw: string): ResumeSpecPayload {
  const lines = raw.split(/\r?\n/);
  let name = "";
  let title = "";
  const contactLines: string[] = [];
  const sections: ResumeSection[] = [];
  let currentSection: ResumeSection | null = null;

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;

    if (line.startsWith("# ")) {
      name = line.slice(2).trim();
      continue;
    }

    if (line.startsWith("## ")) {
      currentSection = {
        title: line.slice(3).trim(),
        blocks: [],
        bullets: [],
      };
      sections.push(currentSection);
      continue;
    }

    if (!currentSection) {
      if (!title) {
        title = line;
      } else {
        contactLines.push(line);
      }
      continue;
    }

    const blocks: ResumeBlock[] = currentSection.blocks ?? [];
    currentSection.blocks = blocks;

    if (line.startsWith("### ")) {
      blocks.push({ type: "subheading", text: line.slice(4).trim() });
    } else if (line.startsWith("- ") || line.startsWith("• ")) {
      blocks.push({ type: "bullet", text: line.slice(2).trim() });
    } else if (line.includes("**") || line.includes("||")) {
      blocks.push({ type: "skillline", text: line });
    } else {
      blocks.push({ type: "paragraph", text: line });
    }
  }

  return {
    name,
    title: name ? `${name}${title ? ` - ${title}` : ""}` : title,
    contact: contactLines,
    filename_title: name ? `Resume_${name.replace(/\s+/g, "_")}` : "Resume",
    sections,
  };
}

export function profileToText(profile: MasterProfile): string {
  const out: string[] = [];
  if (profile.full_name) {
    out.push(`# ${profile.full_name}`);
  }
  if (profile.headline) out.push(profile.headline);

  const c = profile.contact_json || {};
  const contactParts = [
    c.email,
    c.phone,
    c.location,
    c.linkedin,
    c.github,
  ].filter(Boolean);
  if (contactParts.length > 0) {
    out.push(contactParts.join(" | "));
  }

  if (profile.summary_base) {
    out.push("\n## Executive Summary");
    out.push(profile.summary_base);
  }

  if (profile.skills_json && Object.keys(profile.skills_json).length > 0) {
    const skillParts = Object.entries(profile.skills_json)
      .filter(([, items]) => Array.isArray(items) && items.length > 0)
      .map(([cat, items]) => `**${cat}:** ${items.join(", ")}`);
    if (skillParts.length > 0) {
      out.push("\n## Technical Skills");
      out.push(skillParts.join(" || "));
    }
  }

  if (profile.experiences && profile.experiences.length > 0) {
    out.push("\n## Professional Experience");
    for (const exp of profile.experiences) {
      const period = [exp.start_date, exp.is_current ? "Present" : exp.end_date]
        .filter(Boolean)
        .join(" – ");
      out.push(
        `### ${exp.role} — ${exp.company}${period ? ` (${period})` : ""}`
      );
      for (const b of exp.bullets_json || []) {
        if (b.text.trim()) {
          out.push(`- ${b.text.trim()}`);
        }
      }
    }
  }

  if (profile.languages_json && profile.languages_json.length > 0) {
    out.push("\n## Languages");
    out.push(profile.languages_json.join(" | "));
  }

  return out.join("\n");
}

/**
 * Generates a dense, quantifiable narrative paragraph dynamically extracted
 * from the user's real Master Profile experiences and metrics.
 */
export function generateDynamicParagraph(profile: MasterProfile | null): string {
  if (!profile || !profile.experiences || profile.experiences.length === 0) {
    return OVERFLOW_PARAGRAPH.trim();
  }

  const companyNames = Array.from(
    new Set(profile.experiences.map((e) => e.company.trim()).filter(Boolean))
  );
  const roleNames = Array.from(
    new Set(profile.experiences.map((e) => e.role.trim()).filter(Boolean))
  );

  const skillsList: string[] = [];
  if (profile.skills_json) {
    for (const group of Object.values(profile.skills_json)) {
      if (Array.isArray(group)) {
        skillsList.push(...group.map((s) => s.trim()).filter(Boolean));
      }
    }
  }

  const metricRegex =
    /\b\d+(?:\.\d+)?%|\b\d+\+?(?:\s*(?:k|M|B|ms|s|h|x|users|req\/s|rps|clearing|events|workflows))|\b(?:reduced|increased|improved|saved|optimized|scaled|delivered|automated|cut)\b/i;

  const extractedImpacts: { company: string; role: string; text: string; hasMetric: boolean }[] = [];

  for (const exp of profile.experiences) {
    for (const bullet of exp.bullets_json || []) {
      const txt = bullet.text.trim();
      if (!txt) continue;
      extractedImpacts.push({
        company: exp.company,
        role: exp.role,
        text: txt,
        hasMetric: metricRegex.test(txt),
      });
    }
  }

  const metricBullets = extractedImpacts.filter((item) => item.hasMetric);
  const selectedImpacts = metricBullets.length > 0 ? metricBullets : extractedImpacts;

  const topSkills = skillsList.slice(0, 5).join(", ");
  const companiesStr = companyNames.slice(0, 3).join(", ");
  const headlineRole = roleNames[0] || profile.headline || "Senior Engineer";

  const lines: string[] = [];
  lines.push(`### Principal Impact & Systems Track Record — ${companiesStr || "Key Achievements"}`);
  lines.push(
    `Seasoned ${headlineRole} with verified delivery across ${companiesStr || "enterprise platforms"}.${
      topSkills ? ` Specialized in ${topSkills} with focus on high throughput, fault tolerance, and measurable efficiency gains.` : ""
    }`
  );

  if (selectedImpacts.length > 0) {
    for (const imp of selectedImpacts.slice(0, 4)) {
      lines.push(`- [${imp.company}] ${imp.text}`);
    }
  } else {
    lines.push(`- Drove architectural modernization and reduced operational friction across ${companiesStr}.`);
  }

  return lines.join("\n");
}

export function base64ToPdfBlob(base64: string): Blob {
  const binary = window.atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new Blob([bytes], { type: "application/pdf" });
}

export function GeneralResumeBuilder({ onNotify }: GeneralResumeBuilderProps) {
  const profile = useAppStore((s) => s.profile);
  const fetchProfile = useAppStore((s) => s.fetchProfile);

  const [editorText, setEditorText] = useState(() => {
    if (
      profile &&
      (profile.full_name || (profile.experiences && profile.experiences.length > 0))
    ) {
      return profileToText(profile);
    }
    return "";
  });
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [compiling, setCompiling] = useState(false);
  const [exportingFormat, setExportingFormat] = useState<
    "pdf" | "docx" | null
  >(null);
  const [loadingProfile, setLoadingProfile] = useState(false);

  // Synchronize on mount with Master Profile from backend / store
  useEffect(() => {
    let cancelled = false;
    void fetchProfile().then((data) => {
      if (
        !cancelled &&
        data &&
        (data.full_name || (data.experiences && data.experiences.length > 0))
      ) {
        setEditorText((prev) => (prev.trim() === "" ? profileToText(data) : prev));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [fetchProfile]);

  const spec = parseTextToSpec(editorText);

  // Debounced live compilation on text changes
  useEffect(() => {
    let cancelled = false;
    setCompiling(true);

    const timer = window.setTimeout(() => {
      if (!editorText.trim()) {
        setPreview(null);
        setCompiling(false);
        return;
      }

      api
        .previewResume(parseTextToSpec(editorText))
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
  }, [editorText, onNotify]);

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

  const handleLoadFromProfile = async () => {
    setLoadingProfile(true);
    try {
      const data = await fetchProfile(true);
      if (data) {
        setEditorText(profileToText(data));
        onNotify("success", "Loaded content from Master Profile.");
      } else {
        const fallback = await api.getProfile();
        setEditorText(profileToText(fallback));
        onNotify("success", "Loaded content from Master Profile.");
      }
    } catch (err) {
      onNotify(
        "error",
        err instanceof Error ? err.message : "Failed to load Master Profile"
      );
    } finally {
      setLoadingProfile(false);
    }
  };

  const handleAppendLargeParagraph = async () => {
    let currentProfile = profile;
    if (!currentProfile) {
      try {
        currentProfile = await fetchProfile();
      } catch {
        currentProfile = null;
      }
    }
    const generated = generateDynamicParagraph(currentProfile);
    setEditorText((prev) => `${prev.trim()}\n\n${generated}`);
    onNotify(
      "success",
      currentProfile?.experiences?.length
        ? "Inserted dynamic impact metrics from Master Profile."
        : "Inserted sample impact paragraph."
    );
  };

  const handleDownload = async (format: "pdf" | "docx") => {
    setExportingFormat(format);
    try {
      const { blob, filename } = await api.exportResume(spec, format);
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
        err instanceof Error ? err.message : `Failed to export ${format.toUpperCase()}`
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
    <div style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
      <div className="hub-header">
        <div>
          <h1 style={{ fontSize: "1.5rem" }}>
            General Resume Builder & 2-Page Budget Workstation
          </h1>
          <p style={{ color: "var(--ink-secondary)", fontSize: "0.9rem" }}>
            Live ATS PDF rendering with strict 2-page budget enforcement and
            binary PDF/DOCX export.
          </p>
        </div>

        <div className="hub-actions">
          <button
            type="button"
            className="btn"
            onClick={handleLoadFromProfile}
            disabled={loadingProfile}
          >
            {loadingProfile ? "Loading..." : "Load Master Profile"}
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => handleDownload("docx")}
            disabled={exportingFormat !== null}
          >
            {exportingFormat === "docx" ? "Exporting DOCX..." : "Download DOCX"}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => handleDownload("pdf")}
            disabled={exportingFormat !== null}
          >
            {exportingFormat === "pdf" ? "Exporting PDF..." : "Download PDF"}
          </button>
        </div>
      </div>

      {/* 2-Page Budget Meter */}
      <section
        className={`budget-meter-card ${isOverflow ? "overflow" : ""}`}
        aria-label="2-Page Budget Meter"
      >
        <div className="budget-meter-header">
          <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
            <strong style={{ fontSize: "0.95rem" }}>2-Page Budget Meter</strong>
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
                {isOverflow ? `(${pageCount} pages)` : "(Within ATS Limit)"}
              </span>
            </span>
          </div>

          <span style={{ fontSize: "0.8125rem", color: "var(--ink-secondary)" }}>
            {compiling ? "Compiling live preview..." : "Synced with compiler"}
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
              Document exceeds 2-page limit! Prune bullets or adjust text to fit
              ATS standards.
            </span>
          </div>
        )}
      </section>

      <div className="compiler-workspace">
        {/* Left column: Live Resume Text / Spec Editor */}
        <section className="panel" aria-labelledby="resume-editor-heading">
          <div className="panel-header">
            <div>
              <h2 id="resume-editor-heading" className="panel-title">
                General Resume Content Editor
              </h2>
              <p
                style={{ fontSize: "0.8125rem", color: "var(--ink-secondary)" }}
              >
                Edit markdown-structured resume blocks (`# Name`, `## Section`,
                `### Role`, `- Bullet`).
              </p>
            </div>
            <div className="inline-add">
              <button
                type="button"
                className="btn btn-sm"
                onClick={handleAppendLargeParagraph}
                title="Generate and paste a dense impact paragraph from Master Profile experiences"
              >
                + Paste Large Paragraph
              </button>
              <button
                type="button"
                className="btn btn-sm"
                onClick={() =>
                  setEditorText(
                    profile && (profile.full_name || profile.experiences?.length)
                      ? profileToText(profile)
                      : ""
                  )
                }
              >
                Reset Content
              </button>
            </div>
          </div>

          <div className="field">
            <label className="field-label" htmlFor="resume-live-textarea">
              Resume Source Text
            </label>
            <textarea
              id="resume-live-textarea"
              className="textarea"
              style={{
                minHeight: "580px",
                fontFamily:
                  "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                fontSize: "0.825rem",
                lineHeight: 1.55,
              }}
              value={editorText}
              onChange={(e) => setEditorText(e.target.value)}
              placeholder="Enter resume content or load from Master Profile..."
            />
          </div>
        </section>

        {/* Right column: In-Memory PDF Preview & Page Turn Controls */}
        <section className="panel" aria-labelledby="pdf-preview-heading">
          <div className="panel-header">
            <div>
              <h2 id="pdf-preview-heading" className="panel-title">
                Live ATS PDF Preview
              </h2>
              <p
                style={{ fontSize: "0.8125rem", color: "var(--ink-secondary)" }}
              >
                Rendered in-memory with bundled Carlito TrueType fonts.
              </p>
            </div>

            <nav
              className="inline-add"
              aria-label="PDF page navigation"
            >
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                disabled={currentPage <= 1}
              >
                ← Prev
              </button>
              <span
                style={{
                  fontSize: "0.8125rem",
                  fontWeight: 600,
                  padding: "0 0.4rem",
                }}
              >
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
                Next →
              </button>
            </nav>
          </div>

          {pdfUrl ? (
            <iframe
              key={`${pdfUrl}#page=${currentPage}`}
              src={`${pdfUrl}#page=${currentPage}&view=FitH`}
              title="Compiled Resume PDF Preview"
              className="pdf-viewport"
            />
          ) : (
            <div
              className="pdf-viewport"
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
                  : "Type or load resume content to display the live PDF preview."}
              </p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

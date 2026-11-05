import { useEffect, useState } from "react";
import { api, HealthStatus, JobPostingItem, SystemSettings } from "./api";
import { JobRadar } from "./components/JobRadar";
import { MasterProfileHub } from "./components/MasterProfileHub";
import { ResumePreviewer } from "./components/ResumePreviewer";
import { SettingsModal } from "./components/SettingsModal";
import { TailoringStudio } from "./components/TailoringStudio";
import { useAppStore } from "./store";

type ActiveTab = "radar" | "studio" | "profile" | "compiler" | "status";

export function App() {
  const [activeTab, setActiveTab] = useState<ActiveTab>("radar");
  const selectedJob = useAppStore((s) => s.selectedJob);
  const setSelectedJob = useAppStore((s) => s.setSelectedJob);
  const settings = useAppStore((s) => s.settings);
  const setSettings = useAppStore((s) => s.setSettings);
  const fetchSettings = useAppStore((s) => s.fetchSettings);

  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [toast, setToast] = useState<{
    type: "success" | "error";
    message: string;
  } | null>(null);

  const notify = (type: "success" | "error", message: string) => {
    setToast({ type, message });
    window.setTimeout(() => {
      setToast((prev) => (prev?.message === message ? null : prev));
    }, 4500);
  };

  useEffect(() => {
    api
      .getHealth()
      .then((data) => setHealth(data))
      .catch((err: Error) => setHealthError(err.message));

    void fetchSettings();
  }, [fetchSettings]);

  const isProviderConfigured = (s: SystemSettings | null): boolean => {
    if (!s) return false;
    switch (s.active_provider) {
      case "gemini":
        return Boolean(s.gemini_api_key);
      case "openai":
        return Boolean(s.openai_api_key);
      case "claude":
        return Boolean(s.anthropic_api_key);
      case "ollama":
        return Boolean(s.ollama_base_url);
      default:
        return false;
    }
  };

  const providerLabel = settings?.active_provider
    ? settings.active_provider.toUpperCase()
    : "GEMINI";
  const providerReady = isProviderConfigured(settings);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-group">
          <div className="brand-title">
            <span className="brand-dot" aria-hidden="true" />
            <span>CareerForge OS</span>
          </div>

          <nav className="nav-tabs" aria-label="Main navigation">
            <button
              type="button"
              id="tab-radar"
              aria-controls="tabpanel-radar"
              className={`nav-tab ${activeTab === "radar" ? "active" : ""}`}
              onClick={() => setActiveTab("radar")}
              aria-current={activeTab === "radar" ? "page" : undefined}
            >
              Job Radar
            </button>
            <button
              type="button"
              id="tab-studio"
              aria-controls="tabpanel-studio"
              className={`nav-tab ${activeTab === "studio" ? "active" : ""}`}
              onClick={() => setActiveTab("studio")}
              aria-current={activeTab === "studio" ? "page" : undefined}
            >
              Tailoring Studio
            </button>
            <button
              type="button"
              id="tab-profile"
              aria-controls="tabpanel-profile"
              className={`nav-tab ${activeTab === "profile" ? "active" : ""}`}
              onClick={() => setActiveTab("profile")}
              aria-current={activeTab === "profile" ? "page" : undefined}
            >
              Master Profile
            </button>
            <button
              type="button"
              id="tab-compiler"
              aria-controls="tabpanel-compiler"
              className={`nav-tab ${activeTab === "compiler" ? "active" : ""}`}
              onClick={() => setActiveTab("compiler")}
              aria-current={activeTab === "compiler" ? "page" : undefined}
            >
              Resume Previewer
            </button>
            <button
              type="button"
              id="tab-status"
              aria-controls="tabpanel-status"
              className={`nav-tab ${activeTab === "status" ? "active" : ""}`}
              onClick={() => setActiveTab("status")}
              aria-current={activeTab === "status" ? "page" : undefined}
            >
              Runtime Status
            </button>
          </nav>
        </div>

        <div className="topbar-actions">
          <div
            className="status-pill"
            title={
              providerReady
                ? `Active LLM Provider: ${providerLabel} (Key configured)`
                : `Active LLM Provider: ${providerLabel} (Key missing)`
            }
          >
            <span
              className="status-dot"
              style={{
                backgroundColor: providerReady
                  ? "var(--success)"
                  : "var(--warning)",
              }}
            />
            <span>LLM: {providerLabel}</span>
          </div>

          <div className="status-pill">
            <span
              className="status-dot"
              style={{
                backgroundColor:
                  health?.status === "healthy"
                    ? "var(--success)"
                    : healthError
                      ? "var(--danger)"
                      : "var(--warning)",
              }}
            />
            <span>
              {health?.status === "healthy"
                ? `API v${health.version}`
                : healthError
                  ? "API Offline"
                  : "Connecting..."}
            </span>
          </div>

          <button
            type="button"
            className="btn"
            onClick={() => setSettingsOpen(true)}
          >
            Settings
          </button>
        </div>
      </header>

      <main
        className={`main-container ${activeTab === "studio" ? "main-container-wide" : ""}`}
      >
        <div
          role="tabpanel"
          id="tabpanel-radar"
          aria-labelledby="tab-radar"
          hidden={activeTab !== "radar"}
          style={{
            display: activeTab === "radar" ? "block" : "none",
            width: "100%",
          }}
        >
          <JobRadar
            onNotify={notify}
            onTailorResume={(job: JobPostingItem) => {
              setSelectedJob(job);
              setActiveTab("studio");
              notify(
                "success",
                `Opened Tailoring Studio for ${job.title} at ${job.company}`
              );
            }}
          />
        </div>

        <div
          role="tabpanel"
          id="tabpanel-studio"
          aria-labelledby="tab-studio"
          hidden={activeTab !== "studio"}
          style={{
            display: activeTab === "studio" ? "block" : "none",
            width: "100%",
          }}
        >
          <TailoringStudio
            selectedJob={selectedJob}
            onSelectJob={setSelectedJob}
            onNotify={notify}
          />
        </div>

        <div
          role="tabpanel"
          id="tabpanel-profile"
          aria-labelledby="tab-profile"
          hidden={activeTab !== "profile"}
          style={{
            display: activeTab === "profile" ? "block" : "none",
            width: "100%",
          }}
        >
          <MasterProfileHub onNotify={notify} />
        </div>

        <div
          role="tabpanel"
          id="tabpanel-compiler"
          aria-labelledby="tab-compiler"
          hidden={activeTab !== "compiler"}
          style={{
            display: activeTab === "compiler" ? "block" : "none",
            width: "100%",
          }}
        >
          <ResumePreviewer onNotify={notify} />
        </div>

        <div
          role="tabpanel"
          id="tabpanel-status"
          aria-labelledby="tab-status"
          hidden={activeTab !== "status"}
          style={{
            display: activeTab === "status" ? "block" : "none",
            width: "100%",
          }}
        >
          <section className="panel" aria-labelledby="runtime-status-heading">
            <div className="panel-header">
              <h1 id="runtime-status-heading" className="panel-title">
                Monolith Runtime & Provider Status
              </h1>
            </div>
            {health && (
              <div
                style={{
                  display: "grid",
                  gap: "0.5rem",
                  fontSize: "0.9rem",
                  color: "var(--ink-secondary)",
                }}
              >
                <div>
                  <strong style={{ color: "var(--ink-primary)" }}>
                    Service:
                  </strong>{" "}
                  {health.app}
                </div>
                <div>
                  <strong style={{ color: "var(--ink-primary)" }}>
                    Status:
                  </strong>{" "}
                  {health.status}
                </div>
                <div>
                  <strong style={{ color: "var(--ink-primary)" }}>
                    Version:
                  </strong>{" "}
                  {health.version}
                </div>
                <div>
                  <strong style={{ color: "var(--ink-primary)" }}>
                    Active LLM Provider:
                  </strong>{" "}
                  {providerLabel} (
                  {providerReady ? "Configured" : "Awaiting API Key"})
                </div>
              </div>
            )}
            {healthError && (
              <p style={{ color: "var(--danger)" }}>
                Failed to connect to backend: {healthError}
              </p>
            )}
          </section>
        </div>
      </main>

      <SettingsModal
        isOpen={settingsOpen}
        initialSettings={settings}
        onClose={() => setSettingsOpen(false)}
        onSaved={(updated) => {
          setSettings(updated);
          notify(
            "success",
            `Settings saved. Active provider: ${updated.active_provider.toUpperCase()}`
          );
        }}
      />

      {toast && (
        <div
          className={`toast-banner ${toast.type}`}
          role="status"
          aria-live="polite"
        >
          <span>{toast.message}</span>
          <button
            type="button"
            className="tag-remove"
            onClick={() => setToast(null)}
            aria-label="Dismiss notification"
          >
            ✕
          </button>
        </div>
      )}
    </div>
  );
}

export default App;

import { useEffect, useRef, useState } from "react";
import {
  api,
  JobPostingItem,
  ScraperSessionStatus,
  ScraperStreamEvent,
} from "../api";
import { useAppStore } from "../store";

interface JobRadarProps {
  onNotify: (type: "success" | "error", message: string) => void;
  onTailorResume?: (job: JobPostingItem) => void;
}

const PLATFORMS = [
  { id: "linkedin", label: "LinkedIn" },
  { id: "gupy", label: "Gupy" },
] as const;

export function JobRadar({ onNotify, onTailorResume }: JobRadarProps) {
  const [query, setQuery] = useState("Python Backend");
  const [location, setLocation] = useState("São Paulo");
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([
    "linkedin",
    "gupy",
  ]);
  const [remoteOnly, setRemoteOnly] = useState(false);

  const [sessionStatus, setSessionStatus] =
    useState<ScraperSessionStatus | null>(null);
  const [activeWaitingPlatform, setActiveWaitingPlatform] = useState<string | null>(null);
  const [sessionLoadingPlatform, setSessionLoadingPlatform] = useState<string | null>(null);

  const jobs = useAppStore((s) => s.jobs);
  const setJobs = useAppStore((s) => s.setJobs);
  const fetchJobs = useAppStore((s) => s.fetchJobs);
  const logs = useAppStore((s) => s.radarLogs);
  const addRadarLog = useAppStore((s) => s.addRadarLog);
  const clearRadarLogs = useAppStore((s) => s.clearRadarLogs);
  const running = useAppStore((s) => s.scraperRunning);
  const setRunning = useAppStore((s) => s.setScraperRunning);
  const progress = useAppStore((s) => s.scraperProgress);
  const setProgress = useAppStore((s) => s.setScraperProgress);

  const [loadingJobs, setLoadingJobs] = useState(!useAppStore.getState().jobsLoaded);
  const [showArchived, setShowArchived] = useState(false);

  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(true);

  const eventSourceRef = useRef<EventSource | null>(null);
  const logContainerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!running && eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  }, [running]);

  const refreshJobs = async (force = false) => {
    try {
      await fetchJobs(force);
    } catch (err) {
      onNotify(
        "error",
        err instanceof Error ? err.message : "Failed to load discovered jobs"
      );
    } finally {
      setLoadingJobs(false);
    }
  };

  const refreshSessionStatus = async () => {
    try {
      const status = await api.getScraperSessionStatus();
      setSessionStatus(status);
      if (status.browser_open) {
        setActiveWaitingPlatform(status.active_platform || "all");
      } else {
        setActiveWaitingPlatform(null);
      }
    } catch {
      // ignore initial status fetch errors
    }
  };

  useEffect(() => {
    void refreshJobs(false);
    void refreshSessionStatus();
    return () => {
      eventSourceRef.current?.close();
    };
  }, []);

  useEffect(() => {
    if (drawerOpen && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [logs, drawerOpen]);

  const togglePlatform = (platformId: string) => {
    setSelectedPlatforms((prev) => {
      if (prev.includes(platformId)) {
        return prev.length > 1 ? prev.filter((p) => p !== platformId) : prev;
      }
      return [...prev, platformId];
    });
  };

  const handleOpenSession = async (platformId: string) => {
    setSessionLoadingPlatform(platformId);
    try {
      await api.openScraperSession(platformId);
      setActiveWaitingPlatform(platformId);
      const platLabel = PLATFORMS.find((p) => p.id === platformId)?.label || platformId;
      onNotify(
        "success",
        `Navegador aberto para ${platLabel}. Conclua o login na janela e depois clique em 'Verificar Login'.`
      );
    } catch (err) {
      onNotify(
        "error",
        err instanceof Error ? err.message : `Falha ao abrir navegador para login no ${platformId}`
      );
    } finally {
      setSessionLoadingPlatform(null);
    }
  };

  const handleVerifySession = async (platformId: string) => {
    setSessionLoadingPlatform(platformId);
    try {
      const res = await api.verifyScraperSession(platformId);
      setSessionStatus(res);
      setActiveWaitingPlatform(null);
      const platLabel = PLATFORMS.find((p) => p.id === platformId)?.label || platformId;
      onNotify(
        "success",
        `Login no ${platLabel} autenticado com sucesso! Cookies salvos para scraping.`
      );
    } catch (err) {
      onNotify(
        "error",
        err instanceof Error && err.message
          ? err.message
          : `Sessao ainda nao detectada para ${platformId}. Conclua o login e tente novamente.`
      );
    } finally {
      setSessionLoadingPlatform(null);
    }
  };

  const handleCloseSession = async () => {
    setSessionLoadingPlatform("close");
    try {
      await api.closeScraperSession();
      setActiveWaitingPlatform(null);
      await refreshSessionStatus();
      onNotify("success", "Navegador fechado.");
    } catch (err) {
      onNotify(
        "error",
        err instanceof Error ? err.message : "Falha ao fechar navegador"
      );
    } finally {
      setSessionLoadingPlatform(null);
    }
  };

  const connectStream = (runId: string) => {
    eventSourceRef.current?.close();
    const es = new EventSource(`/api/scraper/stream/${runId}`);
    eventSourceRef.current = es;

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as ScraperStreamEvent;
        addRadarLog(data);
        if (typeof data.progress === "number") {
          setProgress(data.progress);
        }
        if (
          data.status === "completed" ||
          data.status === "stopped" ||
          data.status === "failed"
        ) {
          setRunning(false);
          es.close();
          void refreshJobs(true);
          if (data.status === "completed") {
            onNotify(
              "success",
              `Scraper finished. Discovered ${data.jobs_scraped ?? 0} jobs.`
            );
          } else if (data.status === "failed") {
            onNotify("error", data.message || "Scraper run failed.");
          }
        }
      } catch {
        // ignore malformed event payload
      }
    };

    es.onerror = () => {
      setRunning(false);
      es.close();
      void refreshJobs(true);
    };
  };

  const handleRunScraper = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) {
      onNotify("error", "Enter search keywords before running the scraper.");
      return;
    }
    setRunning(true);
    setDrawerOpen(true);
    setProgress(0);
    clearRadarLogs();

    try {
      const { run_id } = await api.startScraper({
        query: query.trim(),
        location: location.trim(),
        platforms: selectedPlatforms,
        remote: remoteOnly,
      });
      setActiveRunId(run_id);
      connectStream(run_id);
    } catch (err) {
      setRunning(false);
      onNotify(
        "error",
        err instanceof Error ? err.message : "Failed to start scraper"
      );
    }
  };

  const handleStopScraper = async () => {
    try {
      await api.stopScraper(activeRunId ?? undefined);
      eventSourceRef.current?.close();
      setRunning(false);
      onNotify("success", "Scraper stopped.");
      void refreshJobs(true);
    } catch (err) {
      onNotify(
        "error",
        err instanceof Error ? err.message : "Failed to stop scraper"
      );
    }
  };

  const handleArchiveJob = async (job: JobPostingItem) => {
    const nextStatus = job.status === "archived" ? "discovered" : "archived";
    setJobs((prev) =>
      prev.map((j) => (j.id === job.id ? { ...j, status: nextStatus } : j))
    );
    try {
      await api.updateJobStatus(job.id, nextStatus);
      onNotify(
        "success",
        nextStatus === "archived"
          ? `Archived "${job.title}" at ${job.company}.`
          : `Restored "${job.title}".`
      );
    } catch {
      // local state already updated as fallback
    }
  };

  const visibleJobs = jobs.filter((j) =>
    showArchived ? j.status === "archived" : j.status !== "archived"
  );

  return (
    <div className="radar-layout">
      {/* Header with Session Status Badge & Login Trigger */}
      <div className="hub-header">
        <div>
          <h1 style={{ fontSize: "1.5rem" }}>Job Radar & Live Scraper</h1>
          <p style={{ color: "var(--ink-secondary)", fontSize: "0.9rem" }}>
            Discover positions across LinkedIn and Gupy using persistent
            anti-bot sessions and live event streaming.
          </p>
        </div>

        <div className="hub-actions" style={{ alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
          {PLATFORMS.map((p) => {
            const isPlatformConnected = sessionStatus?.platforms
              ? !!sessionStatus.platforms[p.id]
              : p.id === "linkedin"
              ? !!sessionStatus?.linkedin
              : p.id === "gupy"
              ? !!sessionStatus?.gupy
              : false;

            const isThisPlatformWaiting =
              activeWaitingPlatform === p.id ||
              (activeWaitingPlatform === "all" && !isPlatformConnected);
            const isAnyWaiting = !!activeWaitingPlatform;
            const isLoading =
              sessionLoadingPlatform === p.id ||
              (sessionLoadingPlatform === "close" && isThisPlatformWaiting);

            return (
              <div
                key={p.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.4rem",
                  padding: "0.3rem 0.65rem",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--border-subtle)",
                  backgroundColor: "var(--surface-muted)",
                }}
              >
                <span
                  className="status-dot"
                  style={{
                    backgroundColor: isPlatformConnected
                      ? "var(--success)"
                      : isThisPlatformWaiting
                      ? "var(--primary)"
                      : "var(--warning)",
                  }}
                />
                <span style={{ fontSize: "0.85rem", fontWeight: 600 }}>
                  {p.label}:
                </span>
                <span
                  style={{
                    fontSize: "0.8rem",
                    color: isPlatformConnected
                      ? "var(--success)"
                      : isThisPlatformWaiting
                      ? "var(--primary)"
                      : "var(--ink-secondary)",
                    marginRight: "0.3rem",
                  }}
                >
                  {isPlatformConnected
                    ? "Conectado"
                    : isThisPlatformWaiting
                    ? "Aguardando..."
                    : "Desconectado"}
                </span>

                {!isThisPlatformWaiting && (
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => handleOpenSession(p.id)}
                    disabled={isAnyWaiting || isLoading}
                    style={{ fontSize: "0.75rem", padding: "0.2rem 0.55rem" }}
                  >
                    {isLoading
                      ? "Abrindo..."
                      : isPlatformConnected
                      ? "Reconectar"
                      : "Login"}
                  </button>
                )}

                {isThisPlatformWaiting && (
                  <div style={{ display: "flex", gap: "0.25rem" }}>
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      onClick={() => handleVerifySession(p.id)}
                      disabled={isLoading}
                      style={{ fontSize: "0.75rem", padding: "0.2rem 0.55rem", fontWeight: 600 }}
                    >
                      {isLoading ? "Verificando..." : "Verificar"}
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={handleCloseSession}
                      disabled={isLoading}
                      style={{ fontSize: "0.75rem", padding: "0.2rem 0.45rem" }}
                    >
                      Fechar
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Search Filter Bar */}
      <section className="panel" aria-labelledby="radar-search-heading">
        <form onSubmit={handleRunScraper} className="radar-toolbar">
          <div className="field" style={{ flex: "2 1 220px" }}>
            <label className="field-label" htmlFor="radar-keywords">
              Keywords
            </label>
            <input
              id="radar-keywords"
              className="input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. Python, FastAPI, Senior Backend"
            />
          </div>

          <div className="field" style={{ flex: "1.5 1 180px" }}>
            <label className="field-label" htmlFor="radar-location">
              Location
            </label>
            <input
              id="radar-location"
              className="input"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="e.g. São Paulo, Brazil, Remote"
            />
          </div>

          <div className="field" style={{ flex: "0 0 auto" }}>
            <span className="field-label">Platforms</span>
            <div className="platform-selector" role="group" aria-label="Target platforms">
              {PLATFORMS.map((p) => {
                const active = selectedPlatforms.includes(p.id);
                return (
                  <button
                    key={p.id}
                    type="button"
                    className={`platform-chip ${active ? "active" : ""}`}
                    aria-pressed={active}
                    onClick={() => togglePlatform(p.id)}
                  >
                    {p.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div
            className="field"
            style={{ flex: "0 0 auto", justifyContent: "flex-end" }}
          >
            <label className="checkbox-pill">
              <input
                type="checkbox"
                checked={remoteOnly}
                onChange={(e) => setRemoteOnly(e.target.checked)}
              />
              <span>Remote only</span>
            </label>
          </div>

          <div
            className="field"
            style={{
              flex: "0 0 auto",
              flexDirection: "row",
              alignItems: "flex-end",
              gap: "0.5rem",
            }}
          >
            {running ? (
              <button
                type="button"
                className="btn btn-danger"
                onClick={handleStopScraper}
              >
                Stop Scraper
              </button>
            ) : (
              <button type="submit" className="btn btn-primary">
                Run Scraper
              </button>
            )}
          </div>
        </form>
      </section>

      {/* Discovered Job Grid Header */}
      <section aria-labelledby="discovered-jobs-heading">
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: "0.875rem",
            gap: "0.75rem",
            flexWrap: "wrap",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
            <h2 id="discovered-jobs-heading" className="panel-title">
              {showArchived ? "Archived Jobs" : "Discovered Jobs"} (
              {visibleJobs.length})
            </h2>
          </div>

          <div className="inline-add">
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => setShowArchived((prev) => !prev)}
            >
              {showArchived ? "Show Active Jobs" : "View Archived"}
            </button>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => void refreshJobs(true)}
            >
              Refresh
            </button>
          </div>
        </div>

        {loadingJobs ? (
          <div className="panel" aria-busy="true">
            <p style={{ color: "var(--ink-secondary)" }}>
              Loading discovered jobs...
            </p>
          </div>
        ) : visibleJobs.length === 0 ? (
          <div className="panel">
            <p style={{ color: "var(--ink-secondary)" }}>
              {showArchived
                ? "No archived jobs yet."
                : "No jobs discovered yet. Enter a search query above and click Run Scraper."}
            </p>
          </div>
        ) : (
          <div className="job-grid">
            {visibleJobs.map((job) => (
              <article key={job.id} className="job-card">
                <div className="job-card-header">
                  <div>
                    <h3 className="job-card-title">{job.title}</h3>
                    <div className="job-card-company">{job.company}</div>
                  </div>
                  <span
                    className={`platform-badge platform-${job.platform.toLowerCase()}`}
                  >
                    {job.platform}
                  </span>
                </div>

                <div className="job-card-meta">
                  <span>{job.location || "Location not specified"}</span>
                  {job.remote_type && <span>• {job.remote_type}</span>}
                </div>

                {job.raw_description && (
                  <p className="job-card-excerpt">
                    {job.raw_description.length > 160
                      ? `${job.raw_description.slice(0, 160)}...`
                      : job.raw_description}
                  </p>
                )}

                <div className="job-card-actions">
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    onClick={() => onTailorResume?.(job)}
                  >
                    Tailor Resume
                  </button>
                  {job.url ? (
                    <a
                      href={job.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="btn btn-sm"
                      style={{ textDecoration: "none" }}
                    >
                      View Post ↗
                    </a>
                  ) : (
                    <button type="button" className="btn btn-sm" disabled>
                      View Post
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => void handleArchiveJob(job)}
                  >
                    {job.status === "archived" ? "Restore" : "Archive"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      {/* Live Scraper Console Drawer */}
      <aside
        className={`console-drawer ${drawerOpen ? "open" : "collapsed"}`}
        aria-label="Live Scraper Console"
      >
        <div className="console-header">
          <div className="console-title-group">
            <span
              className="status-dot"
              style={{
                backgroundColor: running
                  ? "var(--accent)"
                  : logs.length > 0
                    ? "var(--success)"
                    : "var(--ink-placeholder)",
              }}
            />
            <strong style={{ fontSize: "0.85rem" }}>
              Live Scraper Console
            </strong>
            <span
              style={{ fontSize: "0.78rem", color: "var(--ink-secondary)" }}
            >
              {running
                ? `Running (${progress}%)`
                : logs.length > 0
                  ? `Idle (${progress}%)`
                  : "Ready"}
            </span>
          </div>

          <div className="inline-add">
            {logs.length > 0 && (
              <button
                type="button"
                className="btn btn-sm"
                onClick={clearRadarLogs}
              >
                Clear
              </button>
            )}
            <button
              type="button"
              className="btn btn-sm"
              aria-expanded={drawerOpen}
              onClick={() => setDrawerOpen((prev) => !prev)}
            >
              {drawerOpen ? "Collapse ▼" : "Expand ▲"}
            </button>
          </div>
        </div>

        {drawerOpen && (
          <div
            ref={logContainerRef}
            className="console-body"
            role="log"
            aria-live="polite"
          >
            {logs.length === 0 ? (
              <div className="console-line muted">
                $ Waiting for scraper task... Click &quot;Run Scraper&quot; to
                stream live SSE telemetry.
              </div>
            ) : (
              logs.map((entry, idx) => (
                <div key={idx} className="console-line">
                  <span className="console-progress">
                    [{String(entry.progress ?? 0).padStart(3, " ")}%]
                  </span>{" "}
                  <span>{entry.message}</span>
                </div>
              ))
            )}
          </div>
        )}
      </aside>
    </div>
  );
}

import React, { useEffect, useRef, useState } from "react";
import { api, LLMProvider, SystemSettings } from "../api";

interface SettingsModalProps {
  isOpen: boolean;
  initialSettings: SystemSettings | null;
  onClose: () => void;
  onSaved: (updated: SystemSettings) => void;
}

const PROVIDERS: { value: LLMProvider; label: string }[] = [
  { value: "gemini", label: "Google Gemini" },
  { value: "openai", label: "OpenAI" },
  { value: "claude", label: "Anthropic Claude" },
  { value: "ollama", label: "Ollama (Local)" },
];

export function SettingsModal({
  isOpen,
  initialSettings,
  onClose,
  onSaved,
}: SettingsModalProps) {
  const [form, setForm] = useState<SystemSettings>({
    active_provider: "gemini",
    gemini_api_key: "",
    openai_api_key: "",
    anthropic_api_key: "",
    ollama_base_url: "http://localhost:11434",
    scraper_headless: true,
    scraper_timeout: 30,
  });
  const [visibleKeys, setVisibleKeys] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testFeedback, setTestFeedback] = useState<{
    ok: boolean;
    message: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const selectRef = useRef<HTMLSelectElement | null>(null);

  useEffect(() => {
    if (isOpen && initialSettings) {
      setForm({
        active_provider: initialSettings.active_provider,
        gemini_api_key: initialSettings.gemini_api_key ?? "",
        openai_api_key: initialSettings.openai_api_key ?? "",
        anthropic_api_key: initialSettings.anthropic_api_key ?? "",
        ollama_base_url:
          initialSettings.ollama_base_url || "http://localhost:11434",
        scraper_headless: initialSettings.scraper_headless,
        scraper_timeout: initialSettings.scraper_timeout,
      });
      setTestFeedback(null);
      setError(null);
      setVisibleKeys({});
      setTimeout(() => selectRef.current?.focus(), 20);
    }
  }, [isOpen, initialSettings]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const toggleVisibility = (field: string) => {
    setVisibleKeys((prev) => ({ ...prev, [field]: !prev[field] }));
  };

  const handleTestConnection = async () => {
    setTesting(true);
    setTestFeedback(null);
    try {
      const health = await api.getHealth();
      const provider = form.active_provider;
      let credentialReady = false;
      if (provider === "gemini") {
        credentialReady = Boolean(form.gemini_api_key?.trim());
      } else if (provider === "openai") {
        credentialReady = Boolean(form.openai_api_key?.trim());
      } else if (provider === "claude") {
        credentialReady = Boolean(form.anthropic_api_key?.trim());
      } else if (provider === "ollama") {
        credentialReady = Boolean(form.ollama_base_url?.trim());
      }

      if (health.status === "healthy" && credentialReady) {
        setTestFeedback({
          ok: true,
          message: `Backend online (${health.version}) — ${provider.toUpperCase()} credentials configured.`,
        });
      } else {
        setTestFeedback({
          ok: false,
          message: `Backend online, but ${provider.toUpperCase()} API key / endpoint is empty.`,
        });
      }
    } catch (err) {
      setTestFeedback({
        ok: false,
        message:
          err instanceof Error ? err.message : "Failed to reach backend API.",
      });
    } finally {
      setTesting(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const updated = await api.saveSettings({
        active_provider: form.active_provider,
        gemini_api_key: form.gemini_api_key ?? "",
        openai_api_key: form.openai_api_key ?? "",
        anthropic_api_key: form.anthropic_api_key ?? "",
        ollama_base_url: form.ollama_base_url,
        scraper_headless: form.scraper_headless,
        scraper_timeout: Number(form.scraper_timeout) || 30,
      });
      onSaved(updated);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  const renderSecretField = (
    keyName: "gemini_api_key" | "openai_api_key" | "anthropic_api_key",
    label: string,
    placeholder: string
  ) => {
    const isVisible = Boolean(visibleKeys[keyName]);
    const val = form[keyName] ?? "";
    return (
      <div className="field">
        <label className="field-label" htmlFor={`setting-${keyName}`}>
          {label}
        </label>
        <div className="inline-add">
          <input
            id={`setting-${keyName}`}
            className="input"
            type={isVisible ? "text" : "password"}
            value={val}
            placeholder={placeholder}
            autoComplete="off"
            onChange={(e) =>
              setForm((prev) => ({ ...prev, [keyName]: e.target.value }))
            }
          />
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => toggleVisibility(keyName)}
            aria-label={isVisible ? `Hide ${label}` : `Show ${label}`}
          >
            {isVisible ? "Hide" : "Show"}
          </button>
        </div>
      </div>
    );
  };

  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="modal-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-modal-title"
      >
        <div className="panel-header">
          <h2 id="settings-modal-title" className="panel-title">
            System Settings & LLM Providers
          </h2>
          <button
            type="button"
            className="btn btn-sm"
            onClick={onClose}
            aria-label="Close settings modal"
          >
            ✕
          </button>
        </div>

        <form
          onSubmit={handleSubmit}
          style={{ display: "flex", flexDirection: "column", gap: "0.875rem" }}
        >
          <div className="field">
            <label className="field-label" htmlFor="setting-active-provider">
              Active LLM Provider
            </label>
            <select
              ref={selectRef}
              id="setting-active-provider"
              className="select"
              value={form.active_provider}
              onChange={(e) =>
                setForm((prev) => ({
                  ...prev,
                  active_provider: e.target.value as LLMProvider,
                }))
              }
            >
              {PROVIDERS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>

          {renderSecretField(
            "gemini_api_key",
            "Gemini API Key",
            "AIzaSy... (stored locally)"
          )}
          {renderSecretField(
            "openai_api_key",
            "OpenAI API Key",
            "sk-... (stored locally)"
          )}
          {renderSecretField(
            "anthropic_api_key",
            "Anthropic Claude API Key",
            "sk-ant-... (stored locally)"
          )}

          <div className="field">
            <label className="field-label" htmlFor="setting-ollama-url">
              Ollama Base URL
            </label>
            <input
              id="setting-ollama-url"
              className="input"
              type="url"
              value={form.ollama_base_url}
              placeholder="http://localhost:11434"
              onChange={(e) =>
                setForm((prev) => ({
                  ...prev,
                  ollama_base_url: e.target.value,
                }))
              }
            />
          </div>

          <div className="form-grid">
            <div className="field">
              <label className="field-label" htmlFor="setting-scraper-timeout">
                Scraper Timeout (seconds)
              </label>
              <input
                id="setting-scraper-timeout"
                className="input"
                type="number"
                min={1}
                max={300}
                value={form.scraper_timeout}
                onChange={(e) =>
                  setForm((prev) => ({
                    ...prev,
                    scraper_timeout: Number(e.target.value),
                  }))
                }
              />
            </div>

            <div
              className="field"
              style={{ justifyContent: "flex-end", paddingBottom: "0.35rem" }}
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
                  checked={form.scraper_headless}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      scraper_headless: e.target.checked,
                    }))
                  }
                />
                Run Scraper in Headless Mode
              </label>
            </div>
          </div>

          {testFeedback && (
            <div
              role="status"
              style={{
                padding: "0.6rem 0.75rem",
                borderRadius: "6px",
                fontSize: "0.8125rem",
                backgroundColor: testFeedback.ok
                  ? "var(--success-tint)"
                  : "var(--danger-tint)",
                border: `1px solid ${
                  testFeedback.ok ? "var(--success)" : "var(--danger)"
                }`,
              }}
            >
              {testFeedback.message}
            </div>
          )}

          {error && (
            <div
              role="alert"
              style={{
                padding: "0.6rem 0.75rem",
                borderRadius: "6px",
                fontSize: "0.8125rem",
                backgroundColor: "var(--danger-tint)",
                border: "1px solid var(--danger)",
              }}
            >
              {error}
            </div>
          )}

          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              gap: "0.5rem",
              paddingTop: "0.5rem",
              borderTop: "1px solid var(--border-subtle)",
            }}
          >
            <button
              type="button"
              className="btn"
              onClick={handleTestConnection}
              disabled={testing || saving}
            >
              {testing ? "Testing..." : "Test Connection"}
            </button>

            <div style={{ display: "flex", gap: "0.5rem" }}>
              <button
                type="button"
                className="btn"
                onClick={onClose}
                disabled={saving}
              >
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={saving}>
                {saving ? "Saving..." : "Save Settings"}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}

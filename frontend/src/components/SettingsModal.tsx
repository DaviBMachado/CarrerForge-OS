import React, { useEffect, useRef, useState } from "react";
import { api, LLMProvider, SystemSettings } from "../api";
import { AppLanguage, useTranslation } from "../i18n";

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
  { value: "openrouter", label: "OpenRouter" },
  { value: "ollama", label: "Ollama (Local)" },
];

export function SettingsModal({
  isOpen,
  initialSettings,
  onClose,
  onSaved,
}: SettingsModalProps) {
  const { language, setLanguage, t } = useTranslation();
  const [formLanguage, setFormLanguage] = useState<AppLanguage>(language);
  const [form, setForm] = useState<SystemSettings>({
    active_provider: "gemini",
    selected_model: "",
    gemini_api_key: "",
    openai_api_key: "",
    anthropic_api_key: "",
    openrouter_api_key: "",
    ollama_base_url: "http://localhost:11434",
    scraper_headless: true,
    scraper_timeout: 30,
  });
  const [visibleKeys, setVisibleKeys] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [fetchingModels, setFetchingModels] = useState(false);
  const [modelFetchMsg, setModelFetchMsg] = useState<string | null>(null);
  const [testFeedback, setTestFeedback] = useState<{
    ok: boolean;
    message: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const selectRef = useRef<HTMLSelectElement | null>(null);

  useEffect(() => {
    if (isOpen) {
      setFormLanguage(language);
    }
    if (isOpen && initialSettings) {
      setForm({
        active_provider: initialSettings.active_provider,
        selected_model: initialSettings.selected_model ?? "",
        gemini_api_key: initialSettings.gemini_api_key ?? "",
        openai_api_key: initialSettings.openai_api_key ?? "",
        anthropic_api_key: initialSettings.anthropic_api_key ?? "",
        openrouter_api_key: initialSettings.openrouter_api_key ?? "",
        ollama_base_url:
          initialSettings.ollama_base_url || "http://localhost:11434",
        scraper_headless: initialSettings.scraper_headless,
        scraper_timeout: initialSettings.scraper_timeout,
      });
      setTestFeedback(null);
      setError(null);
      setModelFetchMsg(null);
      setVisibleKeys({});
      setTimeout(() => selectRef.current?.focus(), 20);
    }
  }, [isOpen, initialSettings, language]);

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

  const handleFetchModels = async () => {
    setFetchingModels(true);
    setModelFetchMsg(null);
    try {
      const provider = form.active_provider;
      let keyToPass = "";
      if (provider === "gemini") keyToPass = form.gemini_api_key ?? "";
      else if (provider === "openai") keyToPass = form.openai_api_key ?? "";
      else if (provider === "claude") keyToPass = form.anthropic_api_key ?? "";
      else if (provider === "openrouter") keyToPass = form.openrouter_api_key ?? "";

      const res = await api.fetchModels(provider, keyToPass);
      setAvailableModels(res.models || []);
      if (res.models && res.models.length > 0) {
        if (!form.selected_model || !res.models.includes(form.selected_model)) {
          setForm((prev) => ({
            ...prev,
            selected_model: res.default_model || res.models[0],
          }));
        }
        setModelFetchMsg(`Found ${res.models.length} available models.`);
      } else {
        setModelFetchMsg("Using default standard model list.");
      }
    } catch (err) {
      setModelFetchMsg(
        err instanceof Error ? err.message : "Failed to fetch models."
      );
    } finally {
      setFetchingModels(false);
    }
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
      } else if (provider === "openrouter") {
        credentialReady = Boolean(form.openrouter_api_key?.trim());
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
      if (formLanguage !== language) {
        setLanguage(formLanguage);
      }
      onSaved(updated);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  const renderSecretField = (
    keyName:
      | "gemini_api_key"
      | "openai_api_key"
      | "anthropic_api_key"
      | "openrouter_api_key",
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
            aria-label={
              isVisible
                ? `${t("settings.hide")} ${label}`
                : `${t("settings.show")} ${label}`
            }
          >
            {isVisible ? t("settings.hide") : t("settings.show")}
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
            {t("settings.title")}
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
            <label className="field-label" htmlFor="setting-language">
              {t("settings.language")}
            </label>
            <select
              id="setting-language"
              className="select"
              value={formLanguage}
              onChange={(e) => setFormLanguage(e.target.value as AppLanguage)}
            >
              <option value="en">English (EN)</option>
              <option value="pt-BR">Português (pt-BR)</option>
            </select>
          </div>

          <div className="field">
            <label className="field-label" htmlFor="setting-active-provider">
              {t("settings.activeProvider")}
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

          <div className="field">
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: "0.25rem",
              }}
            >
              <label className="field-label" htmlFor="setting-selected-model">
                {t("settings.selectedModel")} ({form.active_provider})
              </label>
              <button
                type="button"
                className="btn btn-sm"
                onClick={handleFetchModels}
                disabled={fetchingModels}
              >
                {fetchingModels
                  ? t("settings.fetchingModels")
                  : t("settings.fetchModels")}
              </button>
            </div>
            {availableModels.length > 0 ? (
              <select
                id="setting-selected-model"
                className="select"
                value={form.selected_model || availableModels[0] || ""}
                onChange={(e) =>
                  setForm((prev) => ({
                    ...prev,
                    selected_model: e.target.value,
                  }))
                }
              >
                {availableModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id="setting-selected-model"
                className="input"
                type="text"
                placeholder="e.g. gemini-3.5-flash-lite, gpt-4o-mini"
                value={form.selected_model || ""}
                onChange={(e) =>
                  setForm((prev) => ({
                    ...prev,
                    selected_model: e.target.value,
                  }))
                }
              />
            )}
            {modelFetchMsg && (
              <span
                style={{
                  fontSize: "0.75rem",
                  color: "var(--ink-secondary)",
                  marginTop: "0.2rem",
                }}
              >
                {modelFetchMsg}
              </span>
            )}
          </div>

          {renderSecretField(
            "gemini_api_key",
            t("settings.geminiKey"),
            "AIzaSy... (stored locally)"
          )}
          {renderSecretField(
            "openai_api_key",
            t("settings.openaiKey"),
            "sk-... (stored locally)"
          )}
          {renderSecretField(
            "anthropic_api_key",
            t("settings.anthropicKey"),
            "sk-ant-... (stored locally)"
          )}
          {renderSecretField(
            "openrouter_api_key",
            t("settings.openrouterKey"),
            "sk-or-v1-... (stored locally)"
          )}

          <div className="field">
            <label className="field-label" htmlFor="setting-ollama-url">
              {t("settings.ollamaUrl")}
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
                {t("settings.timeout")}
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
                {t("settings.headless")}
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
              {testing ? t("settings.testing") : t("settings.testConnection")}
            </button>

            <div style={{ display: "flex", gap: "0.5rem" }}>
              <button
                type="button"
                className="btn"
                onClick={onClose}
                disabled={saving}
              >
                {t("settings.cancel")}
              </button>
              <button type="submit" className="btn btn-primary" disabled={saving}>
                {saving ? t("settings.saving") : t("settings.save")}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}

export type AppLanguage = "en" | "pt-BR";

export const translations = {
  en: {
    // Navigation / Tabs
    "nav.jobRadar": "Job Radar",
    "nav.tailoringStudio": "Tailoring Studio",
    "nav.masterProfile": "Master Profile",
    "nav.resumeBuilder": "General Resume Builder",
    "nav.runtimeStatus": "Runtime Status",

    // Topbar & Actions
    "topbar.settings": "Settings",
    "topbar.language": "Language",
    "topbar.llm": "LLM",
    "topbar.apiConnecting": "Connecting...",
    "topbar.apiOffline": "API Offline",
    "topbar.apiOnline": "API v{version}",

    // Settings Modal
    "settings.title": "System Settings & LLM Providers",
    "settings.language": "Interface Language",
    "settings.activeProvider": "Active LLM Provider",
    "settings.testConnection": "Test Connection",
    "settings.testing": "Testing...",
    "settings.save": "Save Settings",
    "settings.saving": "Saving...",
    "settings.cancel": "Cancel",
    "settings.headless": "Run Scraper in Headless Mode",
    "settings.timeout": "Scraper Timeout (seconds)",

    // Common & Status
    "common.save": "Save",
    "common.cancel": "Cancel",
    "common.close": "Close",
    "common.copy": "Copy",
    "common.copied": "Copied",
    "common.loading": "Loading...",
    "common.status": "Status",
    "common.service": "Service",
    "common.version": "Version",
    "common.activeLlm": "Active LLM Provider",
    "common.configured": "Configured",
    "common.awaitingKey": "Awaiting API Key",

    // Runtime Status Scraper Logs
    "runtime.panelTitle": "Monolith Runtime & Provider Status",
    "runtime.scraperLogsTitle": "Job Radar Scraper Telemetry Logs",
    "runtime.clearLogs": "Clear Telemetry Logs",
    "runtime.noLogs": "No scraper telemetry events recorded in current session.",
    "runtime.filterAll": "All Events",
    "runtime.scraperActive": "Scraper Running",
    "runtime.stopScraper": "Stop Scraper",
    "runtime.scraperStopped": "Scraper execution stopped.",
  },
  "pt-BR": {
    // Navigation / Tabs
    "nav.jobRadar": "Radar de Vagas",
    "nav.tailoringStudio": "Estúdio de Personalização",
    "nav.masterProfile": "Perfil Mestre",
    "nav.resumeBuilder": "Gerador de Currículo",
    "nav.runtimeStatus": "Status do Runtime",

    // Topbar & Actions
    "topbar.settings": "Configurações",
    "topbar.language": "Idioma",
    "topbar.llm": "LLM",
    "topbar.apiConnecting": "Conectando...",
    "topbar.apiOffline": "API Offline",
    "topbar.apiOnline": "API v{version}",

    // Settings Modal
    "settings.title": "Configurações do Sistema e Provedores LLM",
    "settings.language": "Idioma da Interface",
    "settings.activeProvider": "Provedor LLM Ativo",
    "settings.testConnection": "Testar Conexão",
    "settings.testing": "Testando...",
    "settings.save": "Salvar Configurações",
    "settings.saving": "Salvando...",
    "settings.cancel": "Cancelar",
    "settings.headless": "Executar Scraper em Modo Headless",
    "settings.timeout": "Timeout do Scraper (segundos)",

    // Common & Status
    "common.save": "Salvar",
    "common.cancel": "Cancelar",
    "common.close": "Fechar",
    "common.copy": "Copiar",
    "common.copied": "Copiado",
    "common.loading": "Carregando...",
    "common.status": "Status",
    "common.service": "Serviço",
    "common.version": "Versão",
    "common.activeLlm": "Provedor LLM Ativo",
    "common.configured": "Configurado",
    "common.awaitingKey": "Aguardando Chave de API",

    // Runtime Status Scraper Logs
    "runtime.panelTitle": "Status do Monólito & Provedores",
    "runtime.scraperLogsTitle": "Telemetria e Logs do Scraper (Radar de Vagas)",
    "runtime.clearLogs": "Limpar Logs de Telemetria",
    "runtime.noLogs": "Nenhum evento de telemetria registrado na sessão atual.",
    "runtime.filterAll": "Todos os Eventos",
    "runtime.scraperActive": "Raspagem em Andamento",
    "runtime.stopScraper": "Interromper",
    "runtime.scraperStopped": "Raspagem de vagas interrompida.",
  },
} as const;

export type TranslationKey = keyof typeof translations.en;

export function translate(
  lang: AppLanguage,
  key: TranslationKey,
  params?: Record<string, string | number>
): string {
  const dict = translations[lang] || translations.en;
  let text = (dict as Record<string, string>)[key] || translations.en[key] || key;
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      text = text.replace(new RegExp(`\\{${k}\\}`, "g"), String(v));
    }
  }
  return text;
}

import { useAppStore } from "./store";

export function useTranslation() {
  const language = useAppStore((s) => s.language);
  const setLanguage = useAppStore((s) => s.setLanguage);
  const t = (key: TranslationKey, params?: Record<string, string | number>) =>
    translate(language, key, params);
  return { language, setLanguage, t };
}

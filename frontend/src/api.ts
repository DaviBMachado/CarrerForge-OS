export type LLMProvider = "gemini" | "openai" | "claude" | "ollama" | "openrouter";

export interface BulletItem {
  text: string;
  tags: string[];
}

export interface ExperienceItem {
  id: number;
  profile_id: number;
  company: string;
  role: string;
  location: string | null;
  start_date: string | null;
  end_date: string | null;
  is_current: boolean;
  is_academic?: boolean;
  is_contract?: boolean;
  bullets_json: BulletItem[];
}

export interface ExperiencePayload {
  company: string;
  role: string;
  location?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  is_current?: boolean;
  is_academic?: boolean;
  is_contract?: boolean;
  bullets_json?: BulletItem[];
}

export interface ContactInfo {
  email?: string;
  phone?: string;
  location?: string;
  linkedin?: string;
  github?: string;
  [key: string]: string | undefined;
}

export type SkillsMatrix = Record<string, string[]>;

export interface MasterProfile {
  id: number;
  user_id: number;
  full_name: string;
  headline: string | null;
  contact_json: ContactInfo | null;
  summary_base: string | null;
  skills_json: SkillsMatrix | null;
  languages_json: string[] | null;
  experiences: ExperienceItem[];
}

export interface ProfileUpdatePayload {
  full_name?: string;
  headline?: string | null;
  contact_json?: ContactInfo | null;
  summary_base?: string | null;
  skills_json?: SkillsMatrix | null;
  languages_json?: string[] | null;
}

export interface SystemSettings {
  active_provider: LLMProvider;
  selected_model?: string | null;
  gemini_api_key: string | null;
  openai_api_key: string | null;
  anthropic_api_key: string | null;
  openrouter_api_key?: string | null;
  ollama_base_url: string;
  scraper_headless: boolean;
  scraper_timeout: number;
}

export type SettingsUpdatePayload = Partial<SystemSettings>;

export interface HealthStatus {
  status: string;
  app: string;
  version: string;
}

export type ResumeBlockType =
  | "title"
  | "contact"
  | "section"
  | "subheading"
  | "paragraph"
  | "bullet"
  | "skillline";

export interface ResumeBlock {
  type: ResumeBlockType;
  text: string;
}

export interface ResumeSection {
  title: string;
  blocks?: ResumeBlock[];
  bullets?: string[];
}

export interface ResumeSpecPayload {
  name?: string;
  title?: string;
  contact?: string | string[];
  filename_title?: string;
  sections: ResumeSection[];
}

export interface PreviewResponse {
  page_count: number;
  is_valid_2_page: boolean;
  pdf_base64: string;
}

export interface JobPostingItem {
  id: number;
  user_id: number;
  platform: string;
  external_id: string | null;
  title: string;
  company: string;
  location: string | null;
  remote_type: string | null;
  url: string | null;
  raw_description: string;
  cleaned_text: string | null;
  language: string;
  status: string;
  match_percentage?: number | null;
  created_at: string;
}

export interface ManualJobCreatePayload {
  title: string;
  company: string;
  raw_description: string;
  location?: string | null;
  remote_type?: string | null;
  url?: string | null;
  language?: string;
}

export interface ScraperSessionStatus {
  authenticated: boolean;
  linkedin: boolean;
  gupy: boolean;
  platforms?: Record<string, boolean>;
  active_platform?: string | null;
  session_dir: string;
  status?: string;
  browser_open?: boolean;
}

export interface ScraperStartPayload {
  query: string;
  location: string;
  platforms: string[];
  remote?: boolean;
  max_jobs?: number;
}

export interface ScraperStreamEvent {
  run_id: string;
  message: string;
  progress: number;
  status: string;
  jobs_scraped?: number;
  jobs_saved?: number;
}

export type TargetLanguage = "en" | "pt-br";

export interface TailoringResult {
  artifact_id: number;
  job_id: number;
  language: TargetLanguage;
  selected_persona: string;
  ats_keywords: string[];
  match_percentage: number;
  resume_spec: ResumeSpecPayload;
  resume_spec_ats?: ResumeSpecPayload | null;
  resume_spec_human?: ResumeSpecPayload | null;
  cover_letter_text: string;
}

export interface GupyRundown {
  short_bio: string;
  impact_metrics: string[];
  prioritized_skill_tags: string[];
  language: TargetLanguage;
  selected_persona: string;
  screening_qa?: SolveQuestionResponse[];
}

export interface SolveQuestionResponse {
  question: string;
  answer: string;
  language: TargetLanguage;
  grounded_experiences: string[];
}

export interface CoverLetterSpecPayload {
  title?: string;
  name?: string;
  contact?: string | string[];
  date?: string;
  recipient?: string;
  subject?: string;
  greeting?: string;
  paragraphs?: string[];
  body?: string;
  sign_off?: string;
  filename_title?: string;
}

function getAuthHeaders(): Record<string, string> {
  if (typeof localStorage === "undefined") return {};
  const token = localStorage.getItem("careerforge_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const errJson = (await res.json()) as { detail?: string };
      if (errJson.detail) detail = errJson.detail;
    } catch {
      // ignore non-json error body
    }
    throw new Error(detail);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export const api = {
  setAuthToken: (token: string | null) => {
    if (typeof localStorage === "undefined") return;
    if (token) {
      localStorage.setItem("careerforge_token", token);
    } else {
      localStorage.removeItem("careerforge_token");
    }
  },
  getAuthToken: (): string | null => {
    if (typeof localStorage === "undefined") return null;
    return localStorage.getItem("careerforge_token");
  },
  getHealth: () => request<HealthStatus>("/api/health"),
  getProfile: () => request<MasterProfile>("/api/profile"),
  updateProfile: (data: ProfileUpdatePayload) =>
    request<MasterProfile>("/api/profile", {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  createExperience: (data: ExperiencePayload) =>
    request<ExperienceItem>("/api/profile/experiences", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateExperience: (id: number, data: ExperiencePayload) =>
    request<ExperienceItem>(`/api/profile/experiences/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  deleteExperience: (id: number) =>
    request<void>(`/api/profile/experiences/${id}`, {
      method: "DELETE",
    }),
  uploadResume: async (file: File): Promise<MasterProfile> => {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch("/api/profile/upload", {
      method: "POST",
      headers: { ...getAuthHeaders() },
      body: formData,
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const errJson = (await res.json()) as { detail?: string };
        if (errJson.detail) detail = errJson.detail;
      } catch {
        // ignore non-json error body
      }
      throw new Error(detail);
    }
    return (await res.json()) as MasterProfile;
  },
  getSettings: () => request<SystemSettings>("/api/settings"),
  saveSettings: (data: SettingsUpdatePayload) =>
    request<SystemSettings>("/api/settings", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  fetchModels: (provider: string, apiKey?: string) => {
    const params = new URLSearchParams({ provider });
    if (apiKey) params.append("api_key", apiKey);
    return request<{ models: string[]; default_model: string }>(
      `/api/settings/models?${params.toString()}`
    );
  },
  getSystemLogs: (limit = 150) =>
    request<{
      logs: Array<{
        timestamp: string;
        level: string;
        logger: string;
        message: string;
      }>;
    }>(`/api/system/logs?limit=${limit}`),
  expandResumeSection: (currentText: string) =>
    request<{ expanded_text: string }>("/api/tailor/expand-section", {
      method: "POST",
      body: JSON.stringify({ current_text: currentText }),
    }),
  previewResume: (spec: ResumeSpecPayload) =>
    request<PreviewResponse>("/api/compiler/preview", {
      method: "POST",
      body: JSON.stringify(spec),
    }),
  exportResume: async (
    spec: ResumeSpecPayload,
    format: "pdf" | "docx"
  ): Promise<{ blob: Blob; filename: string }> => {
    const res = await fetch(`/api/compiler/export?format=${format}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify({ ...spec, format }),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const errJson = (await res.json()) as { detail?: string };
        if (errJson.detail) detail = errJson.detail;
      } catch {
        // ignore non-json error body
      }
      throw new Error(detail);
    }
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/i);
    const filename =
      match?.[1] || `${spec.filename_title || "Resume"}.${format}`;
    const blob = await res.blob();
    return { blob, filename };
  },
  getScraperSessionStatus: () =>
    request<ScraperSessionStatus>("/api/scraper/session/status"),
  launchScraperLogin: () =>
    request<ScraperSessionStatus>("/api/scraper/session/login", {
      method: "POST",
    }),
  openScraperSession: (platform?: string) =>
    request<{ status: string; platform?: string }>("/api/scraper/session/open", {
      method: "POST",
      body: JSON.stringify({ platform: platform ?? null }),
    }),
  verifyScraperSession: (platform?: string) =>
    request<ScraperSessionStatus>("/api/scraper/session/verify", {
      method: "POST",
      body: JSON.stringify({ platform: platform ?? null }),
    }),
  closeScraperSession: () =>
    request<{ status: string }>("/api/scraper/session/close", {
      method: "POST",
    }),
  startScraper: (payload: ScraperStartPayload) =>
    request<{ run_id: string }>("/api/scraper/start", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  stopScraper: (runId?: string) =>
    request<{ status: string; run_id: string }>("/api/scraper/stop", {
      method: "POST",
      body: JSON.stringify({ run_id: runId ?? null }),
    }),
  getScrapedJobs: () => request<JobPostingItem[]>("/api/scraper/jobs"),
  createManualJob: (payload: ManualJobCreatePayload) =>
    request<JobPostingItem>("/api/scraper/jobs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  extractJobFromUrl: (url: string) =>
    request<JobPostingItem>("/api/scraper/extract-url", {
      method: "POST",
      body: JSON.stringify({ url }),
    }),
  updateJobStatus: (jobId: number, status: string) =>
    request<JobPostingItem>(`/api/scraper/jobs/${jobId}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
  getTailoredJob: (jobId: number) =>
    request<TailoringResult>(`/api/tailor/${jobId}`),
  tailorJob: (jobId: number, mode: "ats" | "human" = "ats") =>
    request<TailoringResult>(`/api/tailor/${jobId}?mode=${mode}`, {
      method: "POST",
    }),
  getGupyRundown: (jobId: number) =>
    request<GupyRundown>(`/api/tailor/${jobId}/gupy-rundown`),
  solveQuestion: (jobId: number, question: string) =>
    request<SolveQuestionResponse>(`/api/tailor/${jobId}/solve-question`, {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
  exportCoverLetter: async (
    spec: CoverLetterSpecPayload,
    format: "pdf" | "docx"
  ): Promise<{ blob: Blob; filename: string }> => {
    const res = await fetch(`/api/compiler/cover-letter/export?format=${format}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify({ ...spec, format }),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const errJson = (await res.json()) as { detail?: string };
        if (errJson.detail) detail = errJson.detail;
      } catch {
        // ignore non-json error body
      }
      throw new Error(detail);
    }
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/i);
    const filename =
      match?.[1] || `${spec.filename_title || "Cover_Letter"}.${format}`;
    const blob = await res.blob();
    return { blob, filename };
  },
};

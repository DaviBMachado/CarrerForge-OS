import { create } from "zustand";
import {
  api,
  JobPostingItem,
  MasterProfile,
  ScraperStreamEvent,
  SystemSettings,
} from "./api";
import { AppLanguage } from "./i18n";

const LANGUAGE_STORAGE_KEY = "careerforge_language";

function getInitialLanguage(): AppLanguage {
  try {
    const saved = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (saved === "pt-BR" || saved === "en") {
      return saved;
    }
  } catch {
    // localStorage might be unavailable
  }
  return "en";
}

interface AppState {
  // i18n Language
  language: AppLanguage;
  setLanguage: (lang: AppLanguage) => void;

  // Jobs
  jobs: JobPostingItem[];
  selectedJob: JobPostingItem | null;
  jobsLoaded: boolean;
  setJobs: (
    jobs: JobPostingItem[] | ((prev: JobPostingItem[]) => JobPostingItem[])
  ) => void;
  setSelectedJob: (job: JobPostingItem | null) => void;
  fetchJobs: (force?: boolean) => Promise<JobPostingItem[]>;

  // Master Profile
  profile: MasterProfile | null;
  profileLoaded: boolean;
  setProfile: (profile: MasterProfile | null) => void;
  fetchProfile: (force?: boolean) => Promise<MasterProfile | null>;

  // System Settings
  settings: SystemSettings | null;
  settingsLoaded: boolean;
  setSettings: (settings: SystemSettings | null) => void;
  fetchSettings: (force?: boolean) => Promise<SystemSettings | null>;

  // Job Radar Stream Logs
  radarLogs: ScraperStreamEvent[];
  addRadarLog: (log: ScraperStreamEvent) => void;
  clearRadarLogs: () => void;
}

export const useAppStore = create<AppState>((set, get) => ({
  language: getInitialLanguage(),
  setLanguage: (language: AppLanguage) => {
    try {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
    } catch {
      // ignore
    }
    set({ language });
  },

  jobs: [],
  selectedJob: null,
  jobsLoaded: false,
  setJobs: (jobsOrFn) =>
    set((state) => ({
      jobs:
        typeof jobsOrFn === "function" ? jobsOrFn(state.jobs) : jobsOrFn,
      jobsLoaded: true,
    })),
  setSelectedJob: (selectedJob) => set({ selectedJob }),
  fetchJobs: async (force = false) => {
    if (!force && get().jobsLoaded) {
      return get().jobs;
    }
    const data = await api.getScrapedJobs();
    set({ jobs: data, jobsLoaded: true });
    return data;
  },

  profile: null,
  profileLoaded: false,
  setProfile: (profile) => set({ profile, profileLoaded: true }),
  fetchProfile: async (force = false) => {
    if (!force && get().profileLoaded) {
      return get().profile;
    }
    const data = await api.getProfile();
    set({ profile: data, profileLoaded: true });
    return data;
  },

  settings: null,
  settingsLoaded: false,
  setSettings: (settings) => set({ settings, settingsLoaded: true }),
  fetchSettings: async (force = false) => {
    if (!force && get().settingsLoaded) {
      return get().settings;
    }
    try {
      const data = await api.getSettings();
      set({ settings: data, settingsLoaded: true });
      return data;
    } catch {
      return null;
    }
  },

  radarLogs: [],
  addRadarLog: (log) =>
    set((state) => ({ radarLogs: [...state.radarLogs, log] })),
  clearRadarLogs: () => set({ radarLogs: [] }),
}));

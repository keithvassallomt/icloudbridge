import { create } from 'zustand';
import { devtools, persist } from 'zustand/middleware';
import type { AppConfig, StatusResponse } from '../types/api';

interface AppState {
  // Configuration
  config: AppConfig | null;
  configLoaded: boolean;
  setConfig: (config: AppConfig) => void;

  // Status
  status: StatusResponse | null;
  setStatus: (status: StatusResponse) => void;

  // UI State
  theme: 'light' | 'dark' | 'system';
  setTheme: (theme: 'light' | 'dark' | 'system') => void;

  sidebarOpen: boolean;
  setSidebarOpen: (open: boolean) => void;
  toggleSidebar: () => void;

  // First-run wizard
  isFirstRun: boolean;
  setIsFirstRun: (isFirstRun: boolean) => void;
  wizardCompleted: boolean;
  setWizardCompleted: (completed: boolean) => void;
  resetWizard: () => void;

  // WebSocket connection
  wsConnected: boolean;
  setWsConnected: (connected: boolean) => void;

  // Loading states
  isLoading: boolean;
  setIsLoading: (loading: boolean) => void;

  // Error handling
  error: string | null;
  setError: (error: string | null) => void;
  clearError: () => void;
}

export const useAppStore = create<AppState>()(
  devtools(
    persist(
      (set) => ({
        // Configuration
        config: null,
        configLoaded: false,
        setConfig: (config) => set({ config, configLoaded: true }),

        // Status
        status: null,
        setStatus: (status) => set({ status }),

        // UI State
        theme: 'system',
        setTheme: (theme) => {
          set({ theme });
          // Apply theme to document
          if (theme === 'dark') {
            document.documentElement.classList.add('dark');
          } else if (theme === 'light') {
            document.documentElement.classList.remove('dark');
          } else {
            // System preference
            const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
            if (isDark) {
              document.documentElement.classList.add('dark');
            } else {
              document.documentElement.classList.remove('dark');
            }
          }
        },

        sidebarOpen: true,
        setSidebarOpen: (open) => set({ sidebarOpen: open }),
        toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),

        // First-run wizard
        isFirstRun: true,
        setIsFirstRun: (isFirstRun) => set({ isFirstRun }),
        wizardCompleted: false,
        setWizardCompleted: (completed) => set({ wizardCompleted: completed }),
        resetWizard: () => set({ wizardCompleted: false, isFirstRun: true }),

        // WebSocket connection
        wsConnected: false,
        setWsConnected: (connected) => set({ wsConnected: connected }),

        // Loading states
        isLoading: false,
        setIsLoading: (loading) => set({ isLoading: loading }),

        // Error handling
        error: null,
        setError: (error) => set({ error }),
        clearError: () => set({ error: null }),
      }),
      {
        name: 'icloudbridge-app-storage',
        partialize: (state) => ({
          theme: state.theme,
          sidebarOpen: state.sidebarOpen,
          wizardCompleted: state.wizardCompleted,
        }),
      }
    )
  )
);

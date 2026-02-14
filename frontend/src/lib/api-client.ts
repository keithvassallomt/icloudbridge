import axios, { AxiosInstance, AxiosError } from 'axios';
import type {
  HealthResponse,
  VersionResponse,
  StatusResponse,
  AppConfig,
  ConfigValidationResponse,
  ConnectionTestResponse,
  NotesSyncRequest,
  RemindersSyncRequest,
  SyncResponse,
  SyncHistoryResponse,
  NotesFolder,
  NotesAllFoldersResponse,
  RemindersCalendar,
  RemindersStatusResponse,
  Schedule,
  ScheduleCreate,
  ScheduleUpdate,
  Setting,
  SettingUpdate,
  SetupVerificationResponse,
  BrowseFoldersResponse,
  APIError,
  PasswordsSyncResponse,
  PasswordsStatus,
  LogLevelResponse,
} from '../types/api';

class APIClient {
  private client: AxiosInstance;

  constructor(baseURL: string = '/api') {
    this.client = axios.create({
      baseURL,
      headers: {
        'Content-Type': 'application/json',
      },
    });

    // Request interceptor
    this.client.interceptors.request.use(
      (config) => {
        // Add auth token if available (future enhancement)
        const token = localStorage.getItem('auth_token');
        if (token) {
          config.headers.Authorization = `Bearer ${token}`;
        }
        return config;
      },
      (error) => Promise.reject(error)
    );

    // Response interceptor
    this.client.interceptors.response.use(
      (response) => response,
      (error: AxiosError<APIError>) => {
        // Handle common errors
        if (error.response?.status === 401) {
          // Handle unauthorized - future auth implementation
          console.error('Unauthorized access');
        }
        return Promise.reject(error);
      }
    );
  }

  // Helper method to handle errors
  private handleError(error: unknown): never {
    if (axios.isAxiosError(error)) {
      const apiError = error.response?.data as APIError;
      throw new Error(apiError?.detail || error.message);
    }
    throw error;
  }

  // Health & Status
  async health(): Promise<HealthResponse> {
    try {
      const { data } = await this.client.get<HealthResponse>('/health');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async version(): Promise<VersionResponse> {
    try {
      const { data } = await this.client.get<VersionResponse>('/version');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async status(): Promise<StatusResponse> {
    try {
      const { data } = await this.client.get<StatusResponse>('/status');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async getLogLevel(): Promise<LogLevelResponse> {
    try {
      const { data } = await this.client.get<LogLevelResponse>('/system/log-level');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async setLogLevel(level: LogLevelResponse['log_level']): Promise<LogLevelResponse> {
    try {
      const { data } = await this.client.put<LogLevelResponse>('/system/log-level', { level });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  // Configuration
  async getConfig(): Promise<AppConfig> {
    try {
      const { data } = await this.client.get<AppConfig>('/config');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async updateConfig(config: Partial<AppConfig>): Promise<AppConfig> {
    try {
      const { data } = await this.client.put<AppConfig>('/config', config);
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async validateConfig(): Promise<ConfigValidationResponse> {
    try {
      const { data } = await this.client.get<ConfigValidationResponse>('/config/validate');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async resetConfig(): Promise<{ status: string; message: string }> {
    try {
      const { data } = await this.client.post<{ status: string; message: string }>('/config/reset');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async testConnection(service: string): Promise<ConnectionTestResponse> {
    try {
      const { data } = await this.client.post<ConnectionTestResponse>('/config/test-connection', null, {
        params: { service },
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  // Notes
  async getNotesFolders(): Promise<NotesFolder[]> {
    try {
      const { data } = await this.client.get<{ folders: NotesFolder[] }>('/notes/folders');
      return data.folders;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async getAllNotesFolders(): Promise<NotesAllFoldersResponse> {
    try {
      const { data } = await this.client.get<NotesAllFoldersResponse>('/notes/folders/all');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async syncNotes(request: NotesSyncRequest = {}): Promise<SyncResponse> {
    try {
      const { data } = await this.client.post<SyncResponse>('/notes/sync', request);
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async getNotesStatus(): Promise<SyncResponse> {
    try {
      const { data } = await this.client.get<SyncResponse>('/notes/status');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async getNotesHistory(limit: number = 10): Promise<SyncHistoryResponse> {
    try {
      const { data } = await this.client.get<SyncHistoryResponse>('/notes/history', {
        params: { limit },
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async resetNotes(): Promise<{ status: string; message: string }> {
    try {
      const { data } = await this.client.post('/notes/reset');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  // Reminders
  async getRemindersCalendars(): Promise<RemindersCalendar[]> {
    try {
      const { data } = await this.client.get<{ calendars: RemindersCalendar[] }>('/reminders/calendars');
      return data.calendars;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async getCaldavCalendars(): Promise<string[]> {
    try {
      const { data } = await this.client.get<{ calendars: string[] }>('/reminders/caldav-calendars');
      return data.calendars;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async syncReminders(request: RemindersSyncRequest = {}): Promise<SyncResponse> {
    try {
      const { data } = await this.client.post<SyncResponse>('/reminders/sync', request);
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async getRemindersStatus(): Promise<RemindersStatusResponse> {
    try {
      const { data } = await this.client.get<RemindersStatusResponse>('/reminders/status');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async getRemindersHistory(limit: number = 10): Promise<SyncHistoryResponse> {
    try {
      const { data } = await this.client.get<SyncHistoryResponse>('/reminders/history', {
        params: { limit },
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async resetReminders(): Promise<{ status: string; message: string }> {
    try {
      const { data } = await this.client.post('/reminders/reset');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async setRemindersPassword(username: string, password: string): Promise<{ status: string; message: string }> {
    try {
      const { data } = await this.client.post('/reminders/password', { username, password });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async deleteRemindersPassword(): Promise<{ status: string; message: string }> {
    try {
      const { data } = await this.client.delete('/reminders/password');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  // Passwords
  async importApplePasswords(file: File): Promise<SyncResponse> {
    try {
      const formData = new FormData();
      formData.append('file', file);
      const { data } = await this.client.post<SyncResponse>('/passwords/import/apple', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async importBitwardenPasswords(file: File): Promise<SyncResponse> {
    try {
      const formData = new FormData();
      formData.append('file', file);
      const { data } = await this.client.post<SyncResponse>('/passwords/import/bitwarden', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async exportApplePasswords(): Promise<Blob> {
    try {
      const { data } = await this.client.post('/passwords/export/apple', {}, {
        responseType: 'blob',
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async exportBitwardenPasswords(): Promise<Blob> {
    try {
      const { data } = await this.client.post('/passwords/export/bitwarden', {}, {
        responseType: 'blob',
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async passwordsBidirectionalSync(
    file: File,
    options?: { simulate?: boolean; bulk?: boolean }
  ): Promise<PasswordsSyncResponse> {
    try {
      const formData = new FormData();
      formData.append('file', file);
      const { data } = await this.client.post<PasswordsSyncResponse>('/passwords/sync', formData, {
        params: {
          simulate: options?.simulate ?? false,
          bulk: options?.bulk ?? true,
        },
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async passwordsExportToProvider(
    file: File,
    options?: { simulate?: boolean; bulk?: boolean }
  ): Promise<PasswordsSyncResponse> {
    try {
      const formData = new FormData();
      formData.append('file', file);
      const { data } = await this.client.post<PasswordsSyncResponse>('/passwords/sync/export', formData, {
        params: {
          simulate: options?.simulate ?? false,
          bulk: options?.bulk ?? true,
        },
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async passwordsImportFromProvider(options?: { simulate?: boolean; bulk?: boolean }): Promise<PasswordsSyncResponse> {
    try {
      const { data } = await this.client.post<PasswordsSyncResponse>('/passwords/sync/import', null, {
        params: {
          simulate: options?.simulate ?? false,
          bulk: options?.bulk ?? true,
        },
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async getPasswordsStatus(): Promise<PasswordsStatus> {
    try {
      const { data } = await this.client.get<PasswordsStatus>('/passwords/status');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async getPasswordsHistory(limit: number = 10): Promise<SyncHistoryResponse> {
    try {
      const { data } = await this.client.get<SyncHistoryResponse>('/passwords/history', {
        params: { limit },
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async resetPasswords(): Promise<{ status: string; message: string }> {
    try {
      const { data } = await this.client.post('/passwords/reset');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async setVaultwardenCredentials(
    email: string,
    password?: string,
    clientId?: string,
    clientSecret?: string,
    url?: string,
  ): Promise<{ status: string; message: string }> {
    try {
      const { data } = await this.client.post('/passwords/vaultwarden/credentials', {
        email,
        password,
        client_id: clientId,
        client_secret: clientSecret,
        url,
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async deleteVaultwardenCredentials(email: string): Promise<{ status: string; message: string }> {
    try {
      const { data } = await this.client.delete('/passwords/vaultwarden/credentials', {
        params: { email },
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async setNextcloudCredentials(
    username: string,
    appPassword: string,
    url?: string,
  ): Promise<{ status: string; message: string }> {
    try {
      const { data } = await this.client.post('/passwords/nextcloud/credentials', {
        username,
        app_password: appPassword,
        url,
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async deleteNextcloudCredentials(username: string): Promise<{ status: string; message: string }> {
    try {
      const { data } = await this.client.delete('/passwords/nextcloud/credentials', {
        params: { username },
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  // Photos
  async syncPhotos(sources?: string[], dryRun: boolean = false, initialScan: boolean = false): Promise<any> {
    try {
      const { data } = await this.client.post('/photos/sync', {
        sources,
        dry_run: dryRun,
        initial_scan: initialScan,
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async getPhotosStatus(): Promise<any> {
    try {
      const { data } = await this.client.get('/photos/status');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async getPhotosHistory(limit: number = 10): Promise<SyncHistoryResponse> {
    try {
      const { data } = await this.client.get<SyncHistoryResponse>('/photos/history', {
        params: { limit },
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async resetPhotos(): Promise<{ status: string; message: string }> {
    try {
      const { data } = await this.client.post('/photos/reset');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  // Photo Export (Apple Photos → NextCloud)
  async exportPhotos(options?: {
    fullLibrary?: boolean;
    albumFilter?: string;
    dryRun?: boolean;
    sinceDate?: string;
  }): Promise<any> {
    try {
      const { data } = await this.client.post('/photos/export', {
        full_library: options?.fullLibrary ?? false,
        album_filter: options?.albumFilter,
        dry_run: options?.dryRun ?? false,
        since_date: options?.sinceDate,
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async getPhotosExportStatus(): Promise<any> {
    try {
      const { data } = await this.client.get('/photos/export/status');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async getPhotosExportHistory(limit: number = 10): Promise<SyncHistoryResponse> {
    try {
      const { data } = await this.client.get<SyncHistoryResponse>('/photos/export/history', {
        params: { limit },
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async setPhotosExportBaseline(): Promise<{ status: string; message: string; baseline_date: string }> {
    try {
      const { data } = await this.client.post('/photos/export/set-baseline');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async getPhotosLibraryAlbums(): Promise<{ albums: Array<{ uuid: string; name: string; count: number }> }> {
    try {
      const { data } = await this.client.get('/photos/library/albums');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async getPhotosLibraryStats(): Promise<any> {
    try {
      const { data } = await this.client.get('/photos/library/stats');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  // Schedules
  async getSchedules(service?: string, enabled?: boolean): Promise<Schedule[]> {
    try {
      const { data } = await this.client.get<Schedule[]>('/schedules', {
        params: { service, enabled },
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async getSchedule(id: number): Promise<Schedule> {
    try {
      const { data } = await this.client.get<Schedule>(`/schedules/${id}`);
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async createSchedule(schedule: ScheduleCreate): Promise<Schedule> {
    try {
      const { data } = await this.client.post<Schedule>('/schedules', schedule);
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async updateSchedule(id: number, update: ScheduleUpdate): Promise<Schedule> {
    try {
      const { data } = await this.client.put<Schedule>(`/schedules/${id}`, update);
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async deleteSchedule(id: number): Promise<{ status: string; message: string }> {
    try {
      const { data } = await this.client.delete(`/schedules/${id}`);
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async runSchedule(id: number): Promise<{ status: string; message: string }> {
    try {
      const { data } = await this.client.post(`/schedules/${id}/run`);
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async toggleSchedule(id: number): Promise<Schedule> {
    try {
      const { data } = await this.client.put<Schedule>(`/schedules/${id}/toggle`);
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  // Settings
  async getAllSettings(): Promise<Record<string, string>> {
    try {
      const { data } = await this.client.get<{ settings: Record<string, string> }>('/settings');
      return data.settings;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async getSetting(key: string): Promise<string> {
    try {
      const { data } = await this.client.get<{ key: string; value: string }>(`/settings/${key}`);
      return data.value;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async updateSettings(updates: SettingUpdate[]): Promise<{ status: string; message: string }> {
    try {
      const { data } = await this.client.put('/settings', updates);
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async updateSetting(key: string, value: string): Promise<Setting> {
    try {
      const { data } = await this.client.put<Setting>(`/settings/${key}`, { value });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async deleteSetting(key: string): Promise<{ status: string; message: string }> {
    try {
      const { data } = await this.client.delete(`/settings/${key}`);
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  // System Verification
  async verifySetup(): Promise<SetupVerificationResponse> {
    try {
      const { data } = await this.client.get<SetupVerificationResponse>('/system/verify');
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }

  async browseFolders(path: string = '~'): Promise<BrowseFoldersResponse> {
    try {
      const { data } = await this.client.get<BrowseFoldersResponse>('/system/browse-folders', {
        params: { path },
      });
      return data;
    } catch (error) {
      return this.handleError(error);
    }
  }
}

// Export singleton instance
export const apiClient = new APIClient();
export default apiClient;

// API Types matching backend models

export interface HealthResponse {
  status: string;
  timestamp: string;
}

export interface VersionResponse {
  version: string;
  python_version: string;
  platform: string;
}

export interface ServiceStatus {
  last_sync: string | null;
  next_sync: string | null;
  status: string;
  enabled: boolean;
}

export interface StatusResponse {
  notes: ServiceStatus;
  reminders: ServiceStatus;
  passwords: ServiceStatus;
  scheduler_running: boolean;
  active_schedules: number;
}

// Configuration
export interface AppConfig {
  data_dir: string;
  config_file?: string;
  icloud_username?: string;
  notes_enabled: boolean;
  notes_remote_folder?: string;
  reminders_enabled: boolean;
  reminders_sync_mode?: 'auto' | 'manual';
  reminders_caldav_url?: string;
  reminders_caldav_username?: string;
  reminders_caldav_password?: string;
  reminders_calendar_mappings?: Record<string, string>;
  reminders_use_nextcloud?: boolean;
  reminders_nextcloud_url?: string;
  passwords_enabled: boolean;
  passwords_vaultwarden_url?: string;
  passwords_vaultwarden_email?: string;
  passwords_vaultwarden_password?: string;
}

export interface ConfigValidationResponse {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

export interface ConnectionTestResponse {
  success: boolean;
  message: string;
  details?: Record<string, any>;
}

// Sync Operations
export interface NotesSyncRequest {
  mode?: 'import' | 'export' | 'bidirectional';
  folder?: string;
  dry_run?: boolean;
}

export interface RemindersSyncRequest {
  auto?: boolean;
  apple_calendar?: string;
  caldav_calendar?: string;
  dry_run?: boolean;
  skip_deletions?: boolean;
  deletion_threshold?: number;
}

export interface PasswordsSyncRequest {
  vaultwarden_url?: string;
  dry_run?: boolean;
}

export interface SyncResponse {
  status: string;
  message: string;
  stats: Record<string, any>;
  log_id: number;
}

export interface SyncHistoryResponse {
  logs: SyncLog[];
  total: number;
}

export interface SyncLog {
  id: number;
  service: string;
  operation: string;
  status: string;
  message: string;
  started_at: string;
  completed_at: string | null;
  duration_seconds: number | null;
  stats: Record<string, any>;
  error_message: string | null;
}

// Schedules
export interface Schedule {
  id: number;
  service: string;
  name: string;
  schedule_type: 'interval' | 'datetime';
  interval_minutes?: number;
  cron_expression?: string;
  config_json: Record<string, any>;
  enabled: boolean;
  last_run?: string;
  next_run?: string;
  created_at: string;
  updated_at: string;
}

export interface ScheduleCreate {
  service: string;
  name: string;
  schedule_type: 'interval' | 'datetime';
  interval_minutes?: number;
  cron_expression?: string;
  config_json: Record<string, any>;
  enabled: boolean;
}

export interface ScheduleUpdate {
  name?: string;
  enabled?: boolean;
  schedule_type?: 'interval' | 'datetime';
  interval_minutes?: number;
  cron_expression?: string;
  config_json?: Record<string, any>;
}

// Settings
export interface Setting {
  key: string;
  value: string;
}

export interface SettingUpdate {
  key: string;
  value: string;
}

// Notes specific
export interface NotesFolder {
  name: string;
  note_count: number;
}

// Reminders specific
export interface RemindersCalendar {
  name: string;
  reminder_count: number;
}

export interface RemindersStatusResponse {
  enabled: boolean;
  caldav_url: string | null;
  caldav_username: string | null;
  has_password: boolean;
  sync_mode: string | null;
  total_mappings: number;
  last_sync: SyncLog | null;
}

// WebSocket Messages
export interface WebSocketMessage {
  type: 'sync_progress' | 'log_entry' | 'schedule_run' | 'error' | 'status_update' | 'connection' | 'pong' | 'subscribed' | 'unsubscribed';
  service?: string;
  data?: any;
  timestamp: string;
  status?: string;
  message?: string;
}

export interface SyncProgressMessage {
  status: 'running' | 'success' | 'error';
  progress: number;
  message: string;
  stats?: Record<string, any>;
}

export interface LogEntryMessage {
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR';
  message: string;
}

export interface ScheduleRunMessage {
  schedule_id: number;
  schedule_name: string;
  status: 'started' | 'completed' | 'failed';
}

export interface ErrorMessage {
  error_message: string;
  error_type: 'error' | 'warning';
}

// API Error Response
export interface APIError {
  detail: string;
  status_code?: number;
}

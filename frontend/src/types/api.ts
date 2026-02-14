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
  enabled: boolean;
  status?: string;
  sync_count?: number;
  pending?: number;
  last_sync?: string | SyncLog | null;
  next_sync?: string | null;
}

export interface StatusResponse {
  notes: ServiceStatus;
  reminders: ServiceStatus;
  passwords: ServiceStatus;
  photos?: ServiceStatus;
  scheduler_running: boolean;
  active_schedules: number;
}

// Folder Mapping
export interface FolderMapping {
  markdown_folder: string;
  mode: 'import' | 'export' | 'bidirectional';
}

export interface FolderInfo {
  apple: boolean;
  markdown: boolean;
}

export interface NotesAllFoldersResponse {
  folders: Record<string, FolderInfo>;
}

// Configuration
export interface AppConfig {
  data_dir: string;
  config_file?: string;
  icloud_username?: string;
  notes_enabled: boolean;
  notes_remote_folder?: string;
  notes_folder_mappings?: Record<string, FolderMapping>;
  reminders_enabled: boolean;
  reminders_sync_mode?: 'auto' | 'manual';
  reminders_caldav_url?: string;
  reminders_caldav_username?: string;
  reminders_caldav_password?: string;
  reminders_caldav_ssl_verify_cert?: boolean | string;
  reminders_calendar_mappings?: Record<string, string>;
  reminders_use_nextcloud?: boolean;
  reminders_nextcloud_url?: string;
  passwords_enabled: boolean;
  passwords_provider?: 'vaultwarden' | 'nextcloud';
  passwords_ssl_verify_cert?: boolean | string;
  passwords_vaultwarden_url?: string;
  passwords_vaultwarden_email?: string;
  passwords_vaultwarden_password?: string;
  passwords_vaultwarden_client_id?: string;
  passwords_vaultwarden_client_secret?: string;
  passwords_nextcloud_url?: string;
  passwords_nextcloud_username?: string;
  passwords_nextcloud_app_password?: string;
  photos_enabled: boolean;
  photos_default_album?: string;
  photo_sources?: Record<string, PhotoSource>;
  // Photo sync mode and export settings
  photos_sync_mode?: 'import' | 'export' | 'bidirectional';
  photos_export_mode?: 'going_forward' | 'full_library';
  // Export folder defaults to first import source path (local folder, not WebDAV)
  photos_export_folder?: string;
  photos_export_organize_by?: 'date' | 'flat';
}

export interface PhotoSource {
  path: string;
  recursive?: boolean;
  album?: string;
  include_images?: boolean;
  include_videos?: boolean;
  metadata_sidecars?: boolean;
}

export interface ConfigValidationResponse {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

export interface ConnectionTestResponse {
  success: boolean;
  message: string;
  details?: Record<string, unknown>;
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

export interface SyncResponse {
  status: string;
  message: string;
  stats: Record<string, unknown>;
  log_id: number | null;
}

export interface PasswordImportStats {
  new: number;
  updated: number;
  duplicates: number;
  unchanged: number;
  errors: number;
  total_processed: number;
}

export interface PasswordEntryInfo {
  title: string;
  username: string;
  action?: 'create' | 'update' | 'delete';
}

export interface PasswordPushStats {
  queued: number;
  created: number;
  updated: number;
  skipped: number;
  failed: number;
  deleted: number;
  errors: string[];
  folders_created: number;
  simulate: boolean;
  import?: PasswordImportStats;
  entries?: PasswordEntryInfo[];
}

export interface PasswordPullStats {
  new_entries: number;
  updated: number;
  deleted: number;
  simulate: boolean;
  download_token?: string;
  download_filename?: string;
  download_expires_at?: string;
  entries?: PasswordEntryInfo[];
}

export interface PasswordsDownloadInfo {
  token: string;
  filename: string;
  expires_at: string;
}

export interface PasswordsSyncStats {
  push?: PasswordPushStats | null;
  pull?: PasswordPullStats | null;
  total_time: number;
  simulate: boolean;
  run_push: boolean;
  run_pull: boolean;
}

export interface PasswordsSyncResponse {
  status: string;
  simulate: boolean;
  mode: {
    push: boolean;
    pull: boolean;
  };
  stats: PasswordsSyncStats;
  download?: PasswordsDownloadInfo;
}

export interface PasswordsStatus {
  enabled: boolean;
  provider: 'vaultwarden' | 'nextcloud';
  vaultwarden_url: string | null;
  vaultwarden_email: string | null;
  nextcloud_url: string | null;
  nextcloud_username: string | null;
  has_credentials: boolean;
  has_vaultwarden_credentials: boolean;
  has_nextcloud_credentials: boolean;
  total_entries: number;
  by_source: Record<string, number>;
  last_sync: SyncLog | null;
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
  stats: Record<string, unknown>;
  error_message: string | null;
}

export interface PendingNoteInfo {
  uuid?: string;
  title?: string;
  folder?: string;
  remote_path?: string;
  reason?: string;
}

// Schedules
export interface Schedule {
  id: number;
  service: string;
  services: string[];
  name: string;
  schedule_type: 'interval' | 'datetime';
  interval_minutes?: number;
  cron_expression?: string;
  config_json: Record<string, unknown>;
  enabled: boolean;
  last_run?: string;
  next_run?: string;
  created_at: string;
  updated_at: string;
}

export interface ScheduleCreate {
  services: string[];
  name: string;
  schedule_type: 'interval' | 'datetime';
  interval_minutes?: number;
  cron_expression?: string;
  config_json: Record<string, unknown>;
  enabled: boolean;
}

export interface ScheduleUpdate {
  name?: string;
  enabled?: boolean;
  schedule_type?: 'interval' | 'datetime';
  interval_minutes?: number;
  cron_expression?: string;
  config_json?: Record<string, unknown>;
  services?: string[];
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

export type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL';

export interface LogLevelResponse {
  log_level: LogLevel;
}

// WebSocket Messages
export interface WebSocketMessage {
  type: 'sync_progress' | 'log_entry' | 'schedule_run' | 'error' | 'status_update' | 'connection' | 'pong' | 'subscribed' | 'unsubscribed';
  service?: string;
  data?: unknown;
  timestamp: string;
  status?: string;
  message?: string;
}

export interface SyncProgressMessage {
  status: 'running' | 'success' | 'error';
  progress: number;
  message: string;
  stats?: Record<string, unknown>;
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

// Setup Verification
export interface ShortcutStatus {
  name: string;
  installed: boolean;
  url: string;
}

export interface FullDiskAccessStatus {
  has_access: boolean;
  python_path: string;
  notes_db_path?: string;
}

export interface NotesFolderStatus {
  exists: boolean;
  writable: boolean;
  path?: string;
}

export interface SetupVerificationResponse {
  shortcuts: ShortcutStatus[];
  full_disk_access: FullDiskAccessStatus;
  notes_folder: NotesFolderStatus;
  is_localhost: boolean;
  all_ready: boolean;
}

// Folder Browser
export interface FolderItem {
  name: string;
  path: string;
}

export interface BrowseFoldersResponse {
  current_path: string;
  parent_path: string | null;
  folders: FolderItem[];
  is_home: boolean;
  error?: string;
}

// API Error Response
export interface APIError {
  detail: string;
  status_code?: number;
}

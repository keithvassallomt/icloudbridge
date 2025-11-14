import { useEffect, useState, useCallback, useMemo, useRef, useContext } from 'react';
import { Settings as SettingsIcon, RefreshCw, Save, Trash2, FileText, Calendar, Key, Image, Download, Shield, AlertTriangle, ExternalLink, CheckCircle, Loader2, Database } from 'lucide-react';
import { UNSAFE_NavigationContext, useBeforeUnload } from 'react-router-dom';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Progress } from '@/components/ui/progress';
import { FolderBrowserDialog } from '@/components/FolderBrowserDialog';
import { useAppStore } from '@/store/app-store';
import { useSyncStore } from '@/store/sync-store';
import apiClient from '@/lib/api-client';
import type { AppConfig, PasswordsStatus, SetupVerificationResponse } from '@/types/api';

type PasswordProvider = 'vaultwarden' | 'nextcloud';

const PASSWORD_PROVIDERS: { value: PasswordProvider; label: string; helper: string }[] = [
  { value: 'vaultwarden', label: 'Bitwarden / Vaultwarden (recommended)', helper: 'Full feature support including OTP' },
  { value: 'nextcloud', label: 'Nextcloud Passwords', helper: 'Basic sync without OTP/passkey support' },
];

const TRANSIENT_KEYS = new Set(['passwords_vaultwarden_password', 'passwords_nextcloud_app_password']);

const stripTransientFields = (value: unknown): unknown => {
  if (Array.isArray(value)) {
    return value.map(stripTransientFields);
  }
  if (value && typeof value === 'object') {
    const result: Record<string, unknown> = {};
    Object.entries(value as Record<string, unknown>).forEach(([key, val]) => {
      if (TRANSIENT_KEYS.has(key) || val === undefined) {
        return;
      }
      result[key] = stripTransientFields(val);
    });
    return result;
  }
  return value;
};

const sortObject = (value: unknown): unknown => {
  if (Array.isArray(value)) {
    return value.map(sortObject);
  }
  if (value && typeof value === 'object') {
    return Object.keys(value as Record<string, unknown>)
      .sort()
      .reduce<Record<string, unknown>>((acc, key) => {
        acc[key] = sortObject((value as Record<string, unknown>)[key]);
        return acc;
      }, {});
  }
  return value;
};

const sanitizeConfigSnapshot = (value: unknown): unknown => sortObject(stripTransientFields(value ?? {}));

type NavigationBlockerState = 'blocked' | 'unblocked';

interface NavigationBlocker {
  state: NavigationBlockerState;
  proceed: () => void;
  reset: () => void;
}

type NavigatorWithBlock = {
  block?: (blocker: (tx: { retry: () => void }) => void) => () => void;
};

const useNavigationBlocker = (when: boolean): NavigationBlocker => {
  const navigationContext = useContext(UNSAFE_NavigationContext);
  const navigator = (navigationContext?.navigator ?? null) as NavigatorWithBlock | null;
  const [state, setState] = useState<NavigationBlockerState>('unblocked');
  const transitionRef = useRef<{ retry: () => void } | null>(null);

  useEffect(() => {
    if (!navigator?.block) {
      return;
    }

    if (!when) {
      transitionRef.current = null;
      setState('unblocked');
      return;
    }

    const unblock = navigator.block((transition) => {
      transitionRef.current = transition;
      setState('blocked');
    });

    return () => {
      unblock();
      transitionRef.current = null;
      setState('unblocked');
    };
  }, [navigator, when]);

  const proceed = useCallback(() => {
    const transition = transitionRef.current;
    if (transition) {
      transitionRef.current = null;
      setState('unblocked');
      transition.retry();
    }
  }, []);

  const reset = useCallback(() => {
    transitionRef.current = null;
    setState('unblocked');
  }, []);

  return useMemo(() => ({ state, proceed, reset }), [state, proceed, reset]);
};

export default function Settings() {
  const { config, setConfig, setIsFirstRun, resetWizard } = useAppStore();
  const { activeSyncs } = useSyncStore();
  const [formData, setFormData] = useState<Partial<AppConfig>>({});
  const [loading, setLoading] = useState(false);
  const [resetting, setResetting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [showFolderBrowser, setShowFolderBrowser] = useState(false);
  const [showPhotosFolderBrowser, setShowPhotosFolderBrowser] = useState(false);
  const [verification, setVerification] = useState<SetupVerificationResponse | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [passwordStatus, setPasswordStatus] = useState<PasswordsStatus | null>(null);
  const [passwordStatusLoading, setPasswordStatusLoading] = useState(false);
  const [vaultCredsLoading, setVaultCredsLoading] = useState(false);
  const [nextcloudCredsLoading, setNextcloudCredsLoading] = useState(false);
  const [showInitialScanModal, setShowInitialScanModal] = useState(false);
  const [initialScanProgress, setInitialScanProgress] = useState(0);
  const [initialScanMessage, setInitialScanMessage] = useState('');
  const [initialScanComplete, setInitialScanComplete] = useState(false);
  const [pendingSavePayload, setPendingSavePayload] = useState<Partial<AppConfig> | null>(null);
  const [showUnsavedPrompt, setShowUnsavedPrompt] = useState(false);
  const [afterSaveAction, setAfterSaveAction] = useState<(() => void) | null>(null);

  const sanitizedConfig = useMemo(() => sanitizeConfigSnapshot(config ? { ...config, passwords_vaultwarden_password: '', passwords_nextcloud_app_password: '' } : {}), [config]);
  const sanitizedForm = useMemo(() => sanitizeConfigSnapshot(formData), [formData]);
  const hasUnsavedChanges = Boolean(config) && JSON.stringify(sanitizedForm) !== JSON.stringify(sanitizedConfig);
  const navigationBlocker = useNavigationBlocker(hasUnsavedChanges);
  const { state: blockerState, proceed: proceedNavigation, reset: resetNavigation } = navigationBlocker;

  const loadPasswordStatus = useCallback(async () => {
    try {
      setPasswordStatusLoading(true);
      const status = await apiClient.getPasswordsStatus();
      setPasswordStatus(status);
    } catch (err) {
      console.error('Failed to load passwords status:', err);
    } finally {
      setPasswordStatusLoading(false);
    }
  }, []);

  const loadConfig = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setSuccess(null);
      const data = await apiClient.getConfig();
      setConfig(data);
      setFormData({
        ...data,
        passwords_provider: (data.passwords_provider as PasswordProvider) ?? 'vaultwarden',
        passwords_vaultwarden_password: '',
        passwords_nextcloud_app_password: '',
      });
      await loadPasswordStatus();
    } catch (err) {
      setError(formatError(err, 'Failed to load configuration'));
    } finally {
      setLoading(false);
    }
  }, [setConfig, loadPasswordStatus]);

  useEffect(() => {
    loadConfig();
  }, [loadConfig]);

  useEffect(() => {
    if (config) {
      setFormData({
        ...config,
        passwords_provider: (config.passwords_provider as PasswordProvider) ?? 'vaultwarden',
        passwords_vaultwarden_password: '',
        passwords_nextcloud_app_password: '',
      });
    }
  }, [config]);

  useEffect(() => {
    if (blockerState === 'blocked') {
      setShowUnsavedPrompt(true);
    }
  }, [blockerState]);

  useEffect(() => {
    if (!hasUnsavedChanges) {
      setShowUnsavedPrompt(false);
      resetNavigation();
    }
  }, [hasUnsavedChanges, resetNavigation]);

  useBeforeUnload(
    useCallback((event) => {
      if (!hasUnsavedChanges) {
        return;
      }
      event.preventDefault();
      event.returnValue = '';
    }, [hasUnsavedChanges])
  );

  const passwordProvider: PasswordProvider = (formData.passwords_provider as PasswordProvider) ?? 'vaultwarden';

  // Load verification when Notes is enabled
  useEffect(() => {
    if (formData.notes_enabled) {
      loadVerification();
    }
  }, [formData.notes_enabled]);

  const loadVerification = async () => {
    try {
      setVerifying(true);
      const result = await apiClient.verifySetup();
      setVerification(result);
    } catch (err) {
      console.error('Failed to load verification:', err);
    } finally {
      setVerifying(false);
    }
  };

  const formatError = (err: unknown, fallback: string) => {
    if (err instanceof Error) {
      return err.message;
    }
    if (typeof err === 'string') {
      return err;
    }
    if (err && typeof err === 'object') {
      const detail = (err as { detail?: unknown }).detail;
      if (typeof detail === 'string') {
        return detail;
      }
      try {
        return JSON.stringify(err);
      } catch {
        return fallback;
      }
    }
    return fallback;
  };

  const handleReset = useCallback(() => {
    if (config) {
      setFormData({
        ...config,
        passwords_provider: (config.passwords_provider as PasswordProvider) ?? 'vaultwarden',
        passwords_vaultwarden_password: '',
        passwords_nextcloud_app_password: '',
      });
      setError(null);
      setSuccess(null);
    }
  }, [config]);

  const handleServiceReset = async (service: 'notes' | 'reminders' | 'passwords' | 'photos') => {
    const serviceNames = {
      notes: 'Notes',
      reminders: 'Reminders',
      passwords: 'Passwords',
      photos: 'Photos'
    };

    const serviceName = serviceNames[service];

    if (!confirm(`Are you sure you want to reset ${serviceName}?\n\nThis will:\n- Delete the ${serviceName} database\n- Clear ${serviceName} sync history\n- Delete ${serviceName} keychain passwords\n- Reset ${serviceName} settings\n\nThis action cannot be undone!`)) {
      return;
    }

    try {
      setResetting(service);
      setError(null);

      if (service === 'notes') {
        await apiClient.resetNotes();
      } else if (service === 'reminders') {
        await apiClient.resetReminders();
      } else if (service === 'passwords') {
        await apiClient.resetPasswords();
      } else if (service === 'photos') {
        await apiClient.resetPhotos();
      }

      // Update config to disable the service
      const updatedFormData = { ...formData };
      if (service === 'notes') {
        updatedFormData.notes_enabled = false;
        updatedFormData.notes_remote_folder = '';
      } else if (service === 'reminders') {
        updatedFormData.reminders_enabled = false;
        updatedFormData.reminders_caldav_url = '';
        updatedFormData.reminders_caldav_username = '';
        updatedFormData.reminders_caldav_password = '';
        updatedFormData.reminders_use_nextcloud = true;
        updatedFormData.reminders_nextcloud_url = '';
      } else if (service === 'passwords') {
        updatedFormData.passwords_enabled = false;
        updatedFormData.passwords_provider = 'vaultwarden';
        updatedFormData.passwords_vaultwarden_url = '';
        updatedFormData.passwords_vaultwarden_email = '';
        delete (updatedFormData as Record<string, unknown>).passwords_vaultwarden_password;
        updatedFormData.passwords_nextcloud_url = '';
        updatedFormData.passwords_nextcloud_username = '';
        delete (updatedFormData as Record<string, unknown>).passwords_nextcloud_app_password;
      } else if (service === 'photos') {
        updatedFormData.photos_enabled = false;
        // Remove instead of setting to empty string to use backend default
        delete (updatedFormData as Record<string, unknown>).photos_default_album;
        updatedFormData.photo_sources = {};
      }

      // Save the updated config
      const updated = await apiClient.updateConfig(updatedFormData);
      setConfig(updated);
      setFormData({ ...updated, passwords_vaultwarden_password: '' });

      if (service === 'passwords') {
        await loadPasswordStatus();
      }

      setSuccess(`${serviceName} reset successfully`);
    } catch (err) {
      setError(formatError(err, `Failed to reset ${serviceName}`));
    } finally {
      setResetting(null);
    }
  };

  const handleResetConfiguration = async () => {
    if (!confirm('Are you sure you want to completely reset all configuration?\n\nThis will:\n- Delete all databases (notes, reminders, passwords)\n- Delete all sync history and state\n- Delete all passwords from macOS Keychain\n- Delete the configuration file\n- Delete the data directory\n\nNote: Your synced markdown files will NOT be deleted.\n\nThis action cannot be undone!')) {
      return;
    }

    try {
      setLoading(true);
      setError(null);

      // Call the comprehensive reset endpoint
      await apiClient.resetConfig();

      // Reset wizard state in Zustand
      resetWizard();
      setIsFirstRun(true);

      // Reload the page to show the wizard
      window.location.reload();
    } catch (err) {
      setError(formatError(err, 'Failed to reset configuration'));
      setLoading(false);
    }
  };

  const handleSaveVaultwardenCredentials = async () => {
    if (!formData.passwords_vaultwarden_email || !formData.passwords_vaultwarden_password) {
      setError('Enter both email and password to save VaultWarden credentials.');
      return;
    }

    try {
      setVaultCredsLoading(true);
      setError(null);
      setSuccess(null);
      await apiClient.setVaultwardenCredentials(
        formData.passwords_vaultwarden_email,
        formData.passwords_vaultwarden_password,
        undefined,
        undefined,
        formData.passwords_vaultwarden_url,
      );
      setFormData((prev) => ({ ...prev, passwords_vaultwarden_password: '' }));
      await loadPasswordStatus();
      setSuccess('VaultWarden credentials saved securely.');
    } catch (err) {
      setError(formatError(err, 'Failed to save VaultWarden credentials'));
    } finally {
      setVaultCredsLoading(false);
    }
  };

  const handleSaveNextcloudCredentials = async () => {
    if (!formData.passwords_nextcloud_username || !formData.passwords_nextcloud_app_password) {
      setError('Enter both username and app password to save Nextcloud credentials.');
      return;
    }

    try {
      setNextcloudCredsLoading(true);
      setError(null);
      setSuccess(null);
      await apiClient.setNextcloudCredentials(
        formData.passwords_nextcloud_username,
        formData.passwords_nextcloud_app_password,
        formData.passwords_nextcloud_url,
      );
      setFormData((prev) => ({ ...prev, passwords_nextcloud_app_password: '' }));
      await loadPasswordStatus();
      setSuccess('Nextcloud credentials saved securely.');
    } catch (err) {
      setError(formatError(err, 'Failed to save Nextcloud credentials'));
    } finally {
      setNextcloudCredsLoading(false);
    }
  };

  const handleDeleteNextcloudCredentials = async () => {
    if (!passwordStatus?.has_nextcloud_credentials) {
      setError('No Nextcloud credentials are stored.');
      return;
    }

    const usernameToDelete = formData.passwords_nextcloud_username || passwordStatus?.nextcloud_username || '';
    if (!usernameToDelete) {
      setError('Enter the Nextcloud username before deleting credentials.');
      return;
    }

    if (!confirm(`Delete stored Nextcloud credentials for ${usernameToDelete}?`)) {
      return;
    }

    try {
      setNextcloudCredsLoading(true);
      setError(null);
      setSuccess(null);
      await apiClient.deleteNextcloudCredentials(usernameToDelete);
      await loadPasswordStatus();
      setSuccess('Nextcloud credentials removed from keychain.');
    } catch (err) {
      setError(formatError(err, 'Failed to delete Nextcloud credentials'));
    } finally {
      setNextcloudCredsLoading(false);
    }
  };

  const handleDeleteVaultwardenCredentials = async () => {
    if (!passwordStatus?.has_vaultwarden_credentials) {
      setError('No VaultWarden credentials are stored.');
      return;
    }

    if (!formData.passwords_vaultwarden_email && !passwordStatus.vaultwarden_email) {
      setError('Enter the VaultWarden email before deleting credentials.');
      return;
    }

    const emailToDelete = formData.passwords_vaultwarden_email || passwordStatus.vaultwarden_email || '';

    if (!confirm(`Delete stored VaultWarden credentials for ${emailToDelete}?`)) {
      return;
    }

    try {
      setVaultCredsLoading(true);
      setError(null);
      setSuccess(null);
      await apiClient.deleteVaultwardenCredentials(emailToDelete);
      await loadPasswordStatus();
      setSuccess('VaultWarden credentials removed from keychain.');
    } catch (err) {
      setError(formatError(err, 'Failed to delete VaultWarden credentials'));
    } finally {
      setVaultCredsLoading(false);
    }
  };

  const photosSettingsChanged = useCallback(() => {
    if (!config) return false;

    const wasEnabled = config.photos_enabled;
    const isEnabled = formData.photos_enabled;
    const albumChanged = config.photos_default_album !== formData.photos_default_album;
    const oldSources = JSON.stringify(config.photo_sources || {});
    const newSources = JSON.stringify(formData.photo_sources || {});
    const sourcesChanged = oldSources !== newSources;

    return (!wasEnabled && isEnabled) || (isEnabled && (albumChanged || sourcesChanged));
  }, [config, formData.photos_default_album, formData.photo_sources, formData.photos_enabled]);

  const saveConfiguration = useCallback(
    async (onComplete?: () => void): Promise<boolean> => {
      try {
        setLoading(true);
        setError(null);
        setSuccess(null);

        const payload: Partial<AppConfig> = {
          ...formData,
          passwords_provider: passwordProvider,
        };
        delete (payload as Record<string, unknown>).passwords_vaultwarden_password;
        delete (payload as Record<string, unknown>).passwords_nextcloud_app_password;

        if (photosSettingsChanged()) {
          if (onComplete) {
            setAfterSaveAction(() => onComplete);
          } else {
            setAfterSaveAction(null);
          }
          setPendingSavePayload(payload);
          setShowInitialScanModal(true);
          setInitialScanProgress(0);
          setInitialScanMessage('Preparing initial scan...');
          setInitialScanComplete(false);
          setLoading(false);
          return false;
        }

        const updated = await apiClient.updateConfig(payload);
        setConfig(updated);
        setFormData({
          ...updated,
          passwords_provider: (updated.passwords_provider as PasswordProvider) ?? 'vaultwarden',
          passwords_vaultwarden_password: '',
          passwords_nextcloud_app_password: '',
        });
        setPendingSavePayload(null);
        setAfterSaveAction(null);
        setSuccess('Configuration saved successfully');
        onComplete?.();
        return true;
      } catch (err) {
        setError(formatError(err, 'Failed to save configuration'));
        return false;
      } finally {
        setLoading(false);
      }
    },
    [formData, passwordProvider, photosSettingsChanged, setConfig]
  );

  const handleSaveClick = useCallback(() => {
    void saveConfiguration();
  }, [saveConfiguration]);

  const handleDiscardChanges = useCallback(() => {
    handleReset();
    if (blockerState === 'blocked') {
      proceedNavigation();
    }
    setShowUnsavedPrompt(false);
  }, [blockerState, handleReset, proceedNavigation]);

  const handleStayOnPage = useCallback(() => {
    resetNavigation();
    setShowUnsavedPrompt(false);
  }, [resetNavigation]);

  const handleConfirmSave = useCallback(async () => {
    if (blockerState !== 'blocked') {
      const immediate = await saveConfiguration();
      if (immediate) {
        setShowUnsavedPrompt(false);
      }
      return;
    }

    const proceedAfterSave = () => {
      proceedNavigation();
    };

    const immediate = await saveConfiguration(proceedAfterSave);
    if (immediate) {
      setShowUnsavedPrompt(false);
      proceedAfterSave();
    } else if (pendingSavePayload) {
      setShowUnsavedPrompt(false);
    }
  }, [blockerState, pendingSavePayload, proceedNavigation, saveConfiguration]);

  const performInitialScan = useCallback(async () => {
    if (!pendingSavePayload) return;

    try {
      setInitialScanProgress(0);
      setInitialScanMessage('Preparing...');

      // Small delay to show 0% state
      await new Promise(resolve => setTimeout(resolve, 100));

      setInitialScanProgress(5);
      setInitialScanMessage('Saving configuration...');

      // First save the configuration so backend knows where to scan
      const updated = await apiClient.updateConfig(pendingSavePayload);
      setConfig(updated);
      setFormData({ ...updated, passwords_vaultwarden_password: '' });

      setInitialScanProgress(10);
      setInitialScanMessage('Starting initial scan...');

      // Run initial scan (builds database without importing to Photos)
      await apiClient.syncPhotos(undefined, false, true);

      // WebSocket updates will handle progress from 10-100%
      // When scan completes, mark as done
      setInitialScanComplete(true);
      setSuccess('Configuration saved and initial scan completed');

      if (afterSaveAction) {
        afterSaveAction();
        setAfterSaveAction(null);
      }

      // Close modal after a short delay
      setTimeout(() => {
        setShowInitialScanModal(false);
        setPendingSavePayload(null);
        setInitialScanComplete(false);
      }, 1500);
    } catch (err) {
      setError(formatError(err, 'Initial scan failed'));
      setShowInitialScanModal(false);
      setPendingSavePayload(null);
      setAfterSaveAction(null);
    }
  }, [afterSaveAction, pendingSavePayload, setConfig]);

  // Trigger initial scan when modal opens
  useEffect(() => {
    if (showInitialScanModal && pendingSavePayload && !initialScanComplete) {
      performInitialScan();
    }
  }, [showInitialScanModal, pendingSavePayload, initialScanComplete, performInitialScan]);

  // Watch for WebSocket progress updates during initial scan
  useEffect(() => {
    if (showInitialScanModal) {
      const photoSync = activeSyncs.get('photos');
      if (photoSync) {
        setInitialScanProgress(photoSync.progress || 0);
        setInitialScanMessage(photoSync.message || 'Scanning...');
      }
    }
  }, [showInitialScanModal, activeSyncs]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <SettingsIcon className="w-8 h-8" />
            Settings
          </h1>
          <p className="text-muted-foreground">
            Configure iCloudBridge sync services
          </p>
        </div>
        <Button onClick={loadConfig} variant="outline" disabled={loading}>
          <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      {/* Alerts */}
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {success && (
        <Alert variant="success">
          <AlertTitle>Success</AlertTitle>
          <AlertDescription>{success}</AlertDescription>
        </Alert>
      )}

      {/* Data Storage */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Database className="w-6 h-6 text-primary" />
            <div>
              <CardTitle>Data Storage</CardTitle>
              <CardDescription>Configure where iCloudBridge stores its data</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="data-dir">Data Directory</Label>
            <Input
              id="data-dir"
              placeholder="~/.icloudbridge"
              value={formData.data_dir || ''}
              onChange={(e) => setFormData({ ...formData, data_dir: e.target.value })}
            />
            <p className="text-xs text-muted-foreground">
              Where to store sync data and databases
            </p>
          </div>
        </CardContent>
      </Card>

      {/* Notes Settings */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <FileText className="w-6 h-6 text-primary" />
              <div>
                <CardTitle>Notes Sync</CardTitle>
                <CardDescription>Sync Apple Notes with markdown files</CardDescription>
              </div>
            </div>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => handleServiceReset('notes')}
              disabled={resetting === 'notes' || loading}
            >
              {resetting === 'notes' ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Resetting...
                </>
              ) : (
                <>
                  <Trash2 className="w-4 h-4 mr-2" />
                  Reset Notes
                </>
              )}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Localhost Warning */}
          {verification && !verification.is_localhost && (
            <Alert variant="warning" className="border-orange-500 bg-orange-50">
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                <strong>Remote Access Detected</strong>
                <p className="mt-1">
                  Shortcut installation and permission settings must be configured on the same Mac where iCloudBridge is running.
                  You can skip these steps for now and set them up later when accessing from that machine.
                </p>
              </AlertDescription>
            </Alert>
          )}

          <div className="flex items-center justify-between p-4 border rounded-lg">
            <div>
              <Label>Enable Notes Sync</Label>
              <p className="text-sm text-muted-foreground">
                Sync your Apple Notes to markdown
              </p>
            </div>
            <Switch
              checked={formData.notes_enabled || false}
              onCheckedChange={(checked) =>
                setFormData({ ...formData, notes_enabled: checked })
              }
            />
          </div>

          {formData.notes_enabled && (
            <>
              <div className="space-y-2">
                <Label htmlFor="notes-folder">Notes Folder</Label>
                <div className="flex gap-2">
                  <Input
                    id="notes-folder"
                    placeholder="~/Documents/Notes"
                    value={formData.notes_remote_folder || ''}
                    onChange={(e) =>
                      setFormData({ ...formData, notes_remote_folder: e.target.value })
                    }
                    className="flex-1"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setShowFolderBrowser(true)}
                  >
                    Browse
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Directory on your macOS sync machine where markdown files will be stored
                </p>
              </div>

              {/* Shortcuts Installation Section */}
              <div className="p-4 border rounded-lg space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Download className="w-5 h-5 text-primary" />
                    <Label className="text-base">Install Apple Shortcuts</Label>
                  </div>
                  {verifying && <Loader2 className="w-4 h-4 animate-spin" />}
                </div>
                <p className="text-sm text-muted-foreground">
                  Three shortcuts are required for rich notes support (images, tables, formatting).
                </p>

                <div className="space-y-2">
                  {verification?.shortcuts.map((shortcut) => (
                    <div key={shortcut.name} className="flex items-center justify-between p-3 bg-muted rounded-md">
                      <div className="flex items-center gap-2 flex-1">
                        <div className="relative">
                          <img
                            src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23007AFF'%3E%3Cpath d='M6 6h12v2H6zm0 4h12v2H6zm0 4h12v2H6z'/%3E%3C/svg%3E"
                            alt="Shortcuts"
                            className="w-6 h-6"
                          />
                          {shortcut.installed && (
                            <CheckCircle className="w-3 h-3 text-green-500 absolute -top-1 -right-1 bg-white rounded-full" />
                          )}
                        </div>
                        <span className="text-sm font-medium">{shortcut.name}</span>
                      </div>
                      <Button
                        size="sm"
                        variant={shortcut.installed ? "outline" : "default"}
                        disabled={shortcut.installed}
                        onClick={() => window.open(shortcut.url, '_blank')}
                      >
                        {shortcut.installed ? (
                          <>
                            <CheckCircle className="w-4 h-4 mr-1" />
                            Installed
                          </>
                        ) : (
                          <>
                            <ExternalLink className="w-4 h-4 mr-1" />
                            Install
                          </>
                        )}
                      </Button>
                    </div>
                  ))}
                </div>

                <Button
                  size="sm"
                  variant="ghost"
                  onClick={loadVerification}
                  className="w-full"
                >
                  Refresh Status
                </Button>
              </div>

              {/* Full Disk Access Section */}
              <div className="p-4 border rounded-lg space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Shield className="w-5 h-5 text-primary" />
                    <Label className="text-base">Full Disk Access</Label>
                  </div>
                  {verification?.full_disk_access.has_access ? (
                    <CheckCircle className="w-5 h-5 text-green-500" />
                  ) : (
                    <AlertTriangle className="w-5 h-5 text-orange-500" />
                  )}
                </div>
                <p className="text-sm text-muted-foreground">
                  Python needs Full Disk Access to read your Notes database.
                </p>

                {verification && (
                  <div className="space-y-2">
                    <div className="p-3 bg-muted rounded-md text-sm">
                      <div className="font-medium mb-1">Python Path:</div>
                      <code className="text-xs break-all">{verification.full_disk_access.python_path}</code>
                    </div>

                    {!verification.full_disk_access.has_access && (
                      <div className="space-y-2">
                        <p className="text-sm">To grant Full Disk Access:</p>
                        <ol className="text-sm text-muted-foreground space-y-1 ml-4 list-decimal">
                          <li>Open System Settings → Privacy & Security → Full Disk Access</li>
                          <li>Click the lock icon to make changes</li>
                          <li>Click the + button</li>
                          <li>Navigate to and select the Python executable above</li>
                          <li>Restart this application</li>
                        </ol>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            window.open('x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles');
                          }}
                          className="w-full"
                        >
                          <ExternalLink className="w-4 h-4 mr-2" />
                          Open System Settings
                        </Button>
                      </div>
                    )}
                  </div>
                )}

                <Button
                  size="sm"
                  variant="ghost"
                  onClick={loadVerification}
                  className="w-full"
                >
                  Check Access
                </Button>
              </div>
            </>
          )}

          <Alert>
            <AlertDescription>
              Notes sync stores a copy of your Apple Notes as markdown in a folder.
              You can sync with cloud storage services like Nextcloud by pointing the notes folder to your synced directory.
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>

      {/* Reminders Settings */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Calendar className="w-6 h-6 text-primary" />
              <div>
                <CardTitle>Reminders Sync</CardTitle>
                <CardDescription>Sync Apple Reminders via CalDAV</CardDescription>
              </div>
            </div>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => handleServiceReset('reminders')}
              disabled={resetting === 'reminders' || loading}
            >
              {resetting === 'reminders' ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Resetting...
                </>
              ) : (
                <>
                  <Trash2 className="w-4 h-4 mr-2" />
                  Reset Reminders
                </>
              )}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between p-4 border rounded-lg">
            <div>
              <Label>Enable Reminders Sync</Label>
              <p className="text-sm text-muted-foreground">
                Sync with CalDAV servers like Nextcloud or iCloud
              </p>
            </div>
            <Switch
              checked={formData.reminders_enabled || false}
              onCheckedChange={(checked) =>
                setFormData({ ...formData, reminders_enabled: checked })
              }
            />
          </div>

          {formData.reminders_enabled && (
            <>
              <div className="flex items-center space-x-2">
                <input
                  type="checkbox"
                  id="use-nextcloud"
                  checked={formData.reminders_use_nextcloud ?? true}
                  onChange={(e) => {
                    const useNextcloud = e.target.checked;
                    setFormData({
                      ...formData,
                      reminders_use_nextcloud: useNextcloud,
                      reminders_caldav_url: '',
                    });
                  }}
                  className="h-4 w-4 rounded border-gray-300"
                />
                <Label htmlFor="use-nextcloud" className="text-sm font-normal cursor-pointer">
                  Use Nextcloud
                </Label>
              </div>

              {formData.reminders_use_nextcloud !== false && (
                <div className="space-y-2">
                  <Label htmlFor="nextcloud-url">Nextcloud URL</Label>
                  <Input
                    id="nextcloud-url"
                    type="url"
                    placeholder="https://nextcloud.example.org"
                    value={formData.reminders_nextcloud_url || ''}
                    onChange={(e) => {
                      const nextcloudUrl = e.target.value;
                      const caldavUrl = nextcloudUrl ? `${nextcloudUrl.replace(/\/$/, '')}/remote.php/dav` : '';
                      setFormData({
                        ...formData,
                        reminders_nextcloud_url: nextcloudUrl,
                        reminders_caldav_url: caldavUrl,
                      });
                    }}
                  />
                </div>
              )}

              <div className="space-y-2">
                <Label htmlFor="caldav-username">Username</Label>
                <Input
                  id="caldav-username"
                  placeholder="username"
                  value={formData.reminders_caldav_username || ''}
                  onChange={(e) =>
                    setFormData({
                      ...formData,
                      reminders_caldav_username: e.target.value,
                    })
                  }
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="caldav-password">Password</Label>
                <Input
                  id="caldav-password"
                  type="password"
                  placeholder="Password"
                  value={formData.reminders_caldav_password || ''}
                  onChange={(e) =>
                    setFormData({
                      ...formData,
                      reminders_caldav_password: e.target.value,
                    })
                  }
                />
                <p className="text-xs text-muted-foreground">
                  {formData.reminders_use_nextcloud !== false
                    ? 'Your Nextcloud password or app password'
                    : 'For iCloud, use an app-specific password from appleid.apple.com'}
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="caldav-url">CalDAV URL</Label>
                <Input
                  id="caldav-url"
                  type="url"
                  placeholder="https://caldav.icloud.com"
                  value={formData.reminders_caldav_url || ''}
                  onChange={(e) =>
                    setFormData({
                      ...formData,
                      reminders_caldav_url: e.target.value,
                    })
                  }
                  disabled={formData.reminders_use_nextcloud !== false}
                />
                <p className="text-xs text-muted-foreground">
                  {formData.reminders_use_nextcloud !== false
                    ? 'Auto-filled from Nextcloud URL'
                    : 'Full CalDAV server URL'}
                </p>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Passwords Settings */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Key className="w-6 h-6 text-primary" />
              <div>
                <CardTitle>Passwords Sync</CardTitle>
                <CardDescription>Sync passwords with Bitwarden / VaultWarden or Nextcloud Passwords</CardDescription>
              </div>
            </div>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => handleServiceReset('passwords')}
              disabled={resetting === 'passwords' || loading}
            >
              {resetting === 'passwords' ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Resetting...
                </>
              ) : (
                <>
                  <Trash2 className="w-4 h-4 mr-2" />
                  Reset Passwords
                </>
              )}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>Password Provider</Label>
            <div className="grid gap-2 md:grid-cols-2">
              {PASSWORD_PROVIDERS.map((option) => (
                <Button
                  key={option.value}
                  type="button"
                  variant={passwordProvider === option.value ? 'default' : 'outline'}
                  className="justify-start text-left"
                  onClick={() => setFormData({ ...formData, passwords_provider: option.value })}
                >
                  <div>
                    <p className="text-sm font-medium">{option.label}</p>
                    <p className="text-xs text-muted-foreground">{option.helper}</p>
                  </div>
                </Button>
              ))}
            </div>
          </div>

          <div className="flex items-center justify-between p-4 border rounded-lg">
            <div>
              <Label>Enable Passwords Sync</Label>
              <p className="text-sm text-muted-foreground">
                Sync passwords with Bitwarden / VaultWarden or Nextcloud Passwords
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                {passwordStatusLoading
                  ? 'Checking credentials…'
                  : passwordProvider === 'nextcloud'
                  ? passwordStatus?.has_nextcloud_credentials
                    ? 'Nextcloud credentials stored securely in macOS Keychain.'
                    : 'No Nextcloud credentials stored yet.'
                  : passwordStatus?.has_vaultwarden_credentials
                  ? 'VaultWarden credentials stored securely in macOS Keychain.'
                  : 'No VaultWarden credentials stored yet.'}
              </p>
            </div>
            <Switch
              checked={formData.passwords_enabled || false}
              onCheckedChange={(checked) =>
                setFormData({ ...formData, passwords_enabled: checked })
              }
            />
          </div>

          {formData.passwords_enabled && passwordProvider === 'nextcloud' && (
            <>
              <Alert variant="warning" className="border-yellow-500 bg-yellow-50">
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>
                  Nextcloud Passwords app does not support one-time passwords (OTP) or passkeys. For the best experience, we recommend using Bitwarden or Vaultwarden.
                </AlertDescription>
              </Alert>

              <div className="space-y-2">
                <Label htmlFor="nc-url">Nextcloud URL</Label>
                <Input
                  id="nc-url"
                  type="url"
                  placeholder="https://nextcloud.example.org"
                  value={formData.passwords_nextcloud_url || ''}
                  onChange={(e) =>
                    setFormData({ ...formData, passwords_nextcloud_url: e.target.value })
                  }
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="nc-username">Nextcloud Username</Label>
                <Input
                  id="nc-username"
                  placeholder="nextcloud-user"
                  value={formData.passwords_nextcloud_username || ''}
                  onChange={(e) =>
                    setFormData({ ...formData, passwords_nextcloud_username: e.target.value })
                  }
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="nc-password">Nextcloud App Password</Label>
                <Input
                  id="nc-password"
                  type="password"
                  placeholder="App password"
                  value={formData.passwords_nextcloud_app_password || ''}
                  onChange={(e) =>
                    setFormData({ ...formData, passwords_nextcloud_app_password: e.target.value })
                  }
                />
                <p className="text-xs text-muted-foreground">
                  Generate an app password from your Nextcloud security settings.
                </p>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  onClick={handleSaveNextcloudCredentials}
                  disabled={
                    nextcloudCredsLoading ||
                    !formData.passwords_nextcloud_username ||
                    !formData.passwords_nextcloud_app_password
                  }
                >
                  {nextcloudCredsLoading ? (
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  ) : (
                    <CheckCircle className="w-4 h-4 mr-2" />
                  )}
                  Save Credentials
                </Button>
                <Button
                  variant="ghost"
                  onClick={handleDeleteNextcloudCredentials}
                  disabled={nextcloudCredsLoading || !passwordStatus?.has_nextcloud_credentials}
                >
                  <Trash2 className="w-4 h-4 mr-2" />
                  Delete Stored Credentials
                </Button>
              </div>
            </>
          )}

          {formData.passwords_enabled && passwordProvider === 'vaultwarden' && (
            <>
              <div className="space-y-2">
                <Label htmlFor="vw-url">Bitwarden/Vaultwarden URL</Label>
                <Input
                  id="vw-url"
                  type="url"
                  placeholder="https://vault.bitwarden.com"
                  value={formData.passwords_vaultwarden_url || ''}
                  onChange={(e) =>
                    setFormData({ ...formData, passwords_vaultwarden_url: e.target.value })
                  }
                  onFocus={(e) => e.target.setAttribute('list', 'bitwarden-urls')}
                />
                <datalist id="bitwarden-urls">
                  <option value="https://vault.bitwarden.com" />
                  <option value="https://vault.bitwarden.eu" />
                </datalist>
              </div>

              <div className="space-y-2">
                <Label htmlFor="vw-email">Bitwarden/Vaultwarden Email</Label>
                <Input
                  id="vw-email"
                  type="email"
                  placeholder="your@email.com"
                  value={formData.passwords_vaultwarden_email || ''}
                  onChange={(e) =>
                    setFormData({ ...formData, passwords_vaultwarden_email: e.target.value })
                  }
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="vw-password">Bitwarden/Vaultwarden Password</Label>
                <Input
                  id="vw-password"
                  type="password"
                  placeholder="Your master password"
                  value={formData.passwords_vaultwarden_password || ''}
                  onChange={(e) =>
                    setFormData({ ...formData, passwords_vaultwarden_password: e.target.value })
                  }
                />
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    onClick={handleSaveVaultwardenCredentials}
                    disabled={
                      vaultCredsLoading ||
                      !formData.passwords_vaultwarden_email ||
                      !formData.passwords_vaultwarden_password
                    }
                  >
                    {vaultCredsLoading ? (
                      <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    ) : (
                      <CheckCircle className="w-4 h-4 mr-2" />
                    )}
                    Save Credentials
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={handleDeleteVaultwardenCredentials}
                    disabled={vaultCredsLoading || !passwordStatus?.has_vaultwarden_credentials}
                  >
                    <Trash2 className="w-4 h-4 mr-2" />
                    Delete Stored Credentials
                  </Button>
                </div>
              </div>
            </>
          )}

          <Alert>
            <AlertDescription>
              iCloudBridge does not store your passwords - these remain encrypted in your chosen provider.
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>

      {/* Photos Settings */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Image className="w-6 h-6 text-primary" />
              <div>
                <CardTitle>Photos Sync</CardTitle>
                <CardDescription>Import photos and videos from local folders to Apple Photos</CardDescription>
              </div>
            </div>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => handleServiceReset('photos')}
              disabled={resetting === 'photos' || loading}
            >
              {resetting === 'photos' ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Resetting...
                </>
              ) : (
                <>
                  <Trash2 className="w-4 h-4 mr-2" />
                  Reset Photos
                </>
              )}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between p-4 border rounded-lg">
            <div>
              <Label>Enable Photos Sync</Label>
              <p className="text-sm text-muted-foreground">
                Automatically import photos from local folders
              </p>
            </div>
            <Switch
              checked={formData.photos_enabled || false}
              onCheckedChange={(checked) =>
                setFormData({ ...formData, photos_enabled: checked })
              }
            />
          </div>

          {formData.photos_enabled && (
            <>
              <div className="space-y-2">
                <Label htmlFor="photos-folder">Photos Source Folder</Label>
                <div className="flex gap-2">
                  <Input
                    id="photos-folder"
                    placeholder="~/Pictures/Photos"
                    value={
                      formData.photo_sources?.default?.path ||
                      ''
                    }
                    onChange={(e) => {
                      const newPath = e.target.value;
                      setFormData({
                        ...formData,
                        photo_sources: {
                          ...(formData.photo_sources || {}),
                          default: {
                            ...(formData.photo_sources?.default || {}),
                            path: newPath,
                            recursive: true,
                            include_images: true,
                            include_videos: true,
                            metadata_sidecars: true,
                          },
                        },
                      });
                    }}
                    className="flex-1"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setShowPhotosFolderBrowser(true)}
                  >
                    Browse
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Local folder containing photos and videos to import
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="photos-album">Default Album Name</Label>
                <Input
                  id="photos-album"
                  placeholder="iCloudBridge Imports"
                  value={formData.photos_default_album || ''}
                  onChange={(e) =>
                    setFormData({ ...formData, photos_default_album: e.target.value })
                  }
                />
                <p className="text-xs text-muted-foreground">
                  Name of the album in Apple Photos where imported photos will be stored
                </p>
              </div>
            </>
          )}

          <Alert>
            <AlertDescription>
              Photos are imported to Apple Photos using content hash deduplication. Files can be renamed or moved without re-importing.
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>

      {/* Advanced Settings */}
      <Card>
        <CardHeader>
          <CardTitle>Advanced Settings</CardTitle>
          <CardDescription>Configuration file path and danger zone</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="config-file">Configuration File</Label>
            <Input
              id="config-file"
              placeholder="/path/to/config.toml"
              value={formData.config_file || ''}
              onChange={(e) => setFormData({ ...formData, config_file: e.target.value })}
            />
            <p className="text-xs text-muted-foreground">
              Optional path to TOML configuration file
            </p>
          </div>

          <div className="pt-4 border-t">
            <div className="space-y-3">
              <div>
                <Label className="text-destructive">Danger Zone</Label>
                <p className="text-sm text-muted-foreground mt-1">
                  Completely reset iCloudBridge by deleting all databases, sync history,
                  keychain passwords, configuration files, and the data directory.
                  Your synced markdown files will NOT be deleted.
                </p>
              </div>
              <Button
                onClick={handleResetConfiguration}
                variant="destructive"
                disabled={loading}
              >
                <Trash2 className="w-4 h-4 mr-2" />
                Complete Reset
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Save Button */}
      <div className="flex justify-end gap-2">
        <Button onClick={handleReset} variant="outline" disabled={loading}>
          Reset
        </Button>
        <Button onClick={handleSaveClick} disabled={loading}>
          {loading ? (
            <>
              <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
              Saving...
            </>
          ) : (
            <>
              <Save className="w-4 h-4 mr-2" />
              Save Configuration
            </>
          )}
        </Button>
      </div>

      <FolderBrowserDialog
        open={showFolderBrowser}
        onOpenChange={setShowFolderBrowser}
        onSelect={(path) => setFormData({ ...formData, notes_remote_folder: path })}
        initialPath={formData.notes_remote_folder || '~'}
        title="Select Notes Folder"
        description="Choose where to store your Notes as markdown files"
      />

      <FolderBrowserDialog
        open={showPhotosFolderBrowser}
        onOpenChange={setShowPhotosFolderBrowser}
        onSelect={(path) =>
          setFormData({
            ...formData,
            photo_sources: {
              ...(formData.photo_sources || {}),
              default: {
                ...(formData.photo_sources?.default || {}),
                path,
                recursive: true,
                include_images: true,
                include_videos: true,
                metadata_sidecars: true,
              },
            },
          })
        }
        initialPath={formData.photo_sources?.default?.path || '~/Pictures'}
        title="Select Photos Source Folder"
        description="Choose a folder containing photos and videos to import"
      />

      {/* Initial Scan Modal */}
      <Dialog open={showInitialScanModal} onOpenChange={() => {}}>
        <DialogContent
          className="sm:max-w-md"
          hideCloseButton={true}
          onInteractOutside={(e) => e.preventDefault()}
          onEscapeKeyDown={(e) => e.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>Initial Photos Scan</DialogTitle>
            <DialogDescription>
              Scanning your photos folder to build the sync database. This is required before saving your configuration.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">{initialScanMessage}</span>
                <span className="font-medium">{initialScanProgress}%</span>
              </div>
              <Progress value={initialScanProgress} />
            </div>
            {initialScanComplete && (
              <div className="flex items-center gap-2 text-sm text-green-600">
                <CheckCircle className="w-4 h-4" />
                <span>Scan complete! Saving configuration...</span>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={showUnsavedPrompt} onOpenChange={() => {}}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Leave without saving?</DialogTitle>
            <DialogDescription>
              You have unsaved changes on the Settings page. Choose an action before navigating away.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="flex flex-col gap-2 sm:flex-row sm:justify-end">
            <Button variant="ghost" onClick={handleStayOnPage}>
              Stay on page
            </Button>
            <Button variant="outline" onClick={handleDiscardChanges}>
              Ignore changes
            </Button>
            <Button onClick={handleConfirmSave} disabled={loading}>
              {loading ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Saving...
                </>
              ) : (
                'Save & continue'
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

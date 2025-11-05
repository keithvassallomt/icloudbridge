import { useEffect, useState } from 'react';
import { Settings as SettingsIcon, RefreshCw, Save, CheckCircle, XCircle, Trash2 } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { useAppStore } from '@/store/app-store';
import apiClient from '@/lib/api-client';
import type { AppConfig } from '@/types/api';

export default function Settings() {
  const { config, setConfig, setIsFirstRun, resetWizard } = useAppStore();
  const [formData, setFormData] = useState<Partial<AppConfig>>({});
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [validating, setValidating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [validationResult, setValidationResult] = useState<any>(null);
  const [connectionTest, setConnectionTest] = useState<any>(null);

  useEffect(() => {
    loadConfig();
  }, []);

  useEffect(() => {
    if (config) {
      setFormData(config);
    }
  }, [config]);

  const loadConfig = async () => {
    try {
      setLoading(true);
      setError(null);
      setSuccess(null);
      const data = await apiClient.getConfig();
      setConfig(data);
      setFormData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load configuration');
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    // Reset form data to current config
    if (config) {
      setFormData(config);
      setError(null);
      setSuccess(null);
      setValidationResult(null);
      setConnectionTest(null);
    }
  };

  const handleResetConfiguration = async () => {
    if (!confirm('Are you sure you want to reset all configuration? This will clear all settings and restart the setup wizard. This action cannot be undone.')) {
      return;
    }

    try {
      setLoading(true);
      setError(null);

      // Disable all services and clear their settings to trigger first-run wizard
      await apiClient.updateConfig({
        notes_enabled: false,
        notes_folder: '',
        reminders_enabled: false,
        reminders_caldav_url: '',
        reminders_caldav_username: '',
        passwords_enabled: false,
        passwords_vaultwarden_url: '',
        passwords_vaultwarden_email: '',
      });

      // Reset wizard state in Zustand
      resetWizard();
      setIsFirstRun(true);

      // Reload the page to show the wizard
      window.location.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reset configuration');
      setLoading(false);
    }
  };

  const handleSave = async () => {
    try {
      setLoading(true);
      setError(null);
      setSuccess(null);

      const updated = await apiClient.updateConfig(formData);
      setConfig(updated);
      setSuccess('Configuration saved successfully');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save configuration');
    } finally {
      setLoading(false);
    }
  };

  const handleValidate = async () => {
    try {
      setValidating(true);
      setValidationResult(null);

      const result = await apiClient.validateConfig();
      setValidationResult(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Validation failed');
    } finally {
      setValidating(false);
    }
  };

  const handleTestConnection = async () => {
    try {
      setTesting(true);
      setConnectionTest(null);

      const result = await apiClient.testConnection();
      setConnectionTest(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Connection test failed');
    } finally {
      setTesting(false);
    }
  };

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

      {/* Validation Result */}
      {validationResult && (
        <Alert variant={validationResult.valid ? 'success' : 'destructive'}>
          <div className="flex items-start gap-2">
            {validationResult.valid ? (
              <CheckCircle className="w-5 h-5 mt-0.5" />
            ) : (
              <XCircle className="w-5 h-5 mt-0.5" />
            )}
            <div className="flex-1">
              <AlertTitle>
                {validationResult.valid ? 'Configuration Valid' : 'Configuration Invalid'}
              </AlertTitle>
              {validationResult.errors?.length > 0 && (
                <AlertDescription>
                  <ul className="list-disc list-inside mt-2">
                    {validationResult.errors.map((err: string, idx: number) => (
                      <li key={idx}>{err}</li>
                    ))}
                  </ul>
                </AlertDescription>
              )}
              {validationResult.warnings?.length > 0 && (
                <AlertDescription>
                  <p className="font-medium mt-2">Warnings:</p>
                  <ul className="list-disc list-inside">
                    {validationResult.warnings.map((warn: string, idx: number) => (
                      <li key={idx}>{warn}</li>
                    ))}
                  </ul>
                </AlertDescription>
              )}
            </div>
          </div>
        </Alert>
      )}

      {/* Connection Test Result */}
      {connectionTest && (
        <Alert variant={connectionTest.success ? 'success' : 'destructive'}>
          <div className="flex items-start gap-2">
            {connectionTest.success ? (
              <CheckCircle className="w-5 h-5 mt-0.5" />
            ) : (
              <XCircle className="w-5 h-5 mt-0.5" />
            )}
            <div className="flex-1">
              <AlertTitle>Connection Test</AlertTitle>
              <AlertDescription>{connectionTest.message}</AlertDescription>
            </div>
          </div>
        </Alert>
      )}

      {/* iCloud Settings */}
      <Card>
        <CardHeader>
          <CardTitle>iCloud Configuration</CardTitle>
          <CardDescription>Your iCloud account settings</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="icloud-username">iCloud Username</Label>
            <Input
              id="icloud-username"
              type="email"
              placeholder="user@icloud.com"
              value={formData.icloud_username || ''}
              onChange={(e) => setFormData({ ...formData, icloud_username: e.target.value })}
            />
          </div>

          <div className="flex gap-2">
            <Button onClick={handleTestConnection} variant="outline" disabled={testing}>
              {testing ? (
                <>
                  <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                  Testing...
                </>
              ) : (
                'Test Connection'
              )}
            </Button>
            <Button onClick={handleValidate} variant="outline" disabled={validating}>
              {validating ? (
                <>
                  <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                  Validating...
                </>
              ) : (
                'Validate Config'
              )}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Notes Settings */}
      <Card>
        <CardHeader>
          <CardTitle>Notes Sync</CardTitle>
          <CardDescription>Apple Notes synchronization settings</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label>Enable Notes Sync</Label>
              <p className="text-sm text-muted-foreground">
                Sync Apple Notes with markdown files
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
            <div className="space-y-2">
              <Label htmlFor="notes-folder">Notes Folder</Label>
              <Input
                id="notes-folder"
                placeholder="/path/to/notes"
                value={formData.notes_folder || ''}
                onChange={(e) => setFormData({ ...formData, notes_folder: e.target.value })}
              />
              <p className="text-xs text-muted-foreground">
                Directory where markdown files will be stored
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Reminders Settings */}
      <Card>
        <CardHeader>
          <CardTitle>Reminders Sync</CardTitle>
          <CardDescription>Apple Reminders synchronization settings</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label>Enable Reminders Sync</Label>
              <p className="text-sm text-muted-foreground">
                Sync Apple Reminders with CalDAV
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
              <div className="space-y-2">
                <Label>Sync Mode</Label>
                <select
                  className="w-full h-10 px-3 rounded-md border border-input bg-background"
                  value={formData.reminders_sync_mode || 'auto'}
                  onChange={(e) =>
                    setFormData({ ...formData, reminders_sync_mode: e.target.value as any })
                  }
                >
                  <option value="auto">Auto (iCloud)</option>
                  <option value="manual">Manual (CalDAV URL)</option>
                </select>
              </div>

              {formData.reminders_sync_mode === 'manual' && (
                <div className="space-y-2">
                  <Label htmlFor="caldav-url">CalDAV Server URL</Label>
                  <Input
                    id="caldav-url"
                    type="url"
                    placeholder="https://caldav.example.com"
                    value={formData.reminders_caldav_url || ''}
                    onChange={(e) =>
                      setFormData({ ...formData, reminders_caldav_url: e.target.value })
                    }
                  />
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* Passwords Settings */}
      <Card>
        <CardHeader>
          <CardTitle>Passwords Sync</CardTitle>
          <CardDescription>Password synchronization settings</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label>Enable Passwords Sync</Label>
              <p className="text-sm text-muted-foreground">
                Sync passwords with VaultWarden
              </p>
            </div>
            <Switch
              checked={formData.passwords_enabled || false}
              onCheckedChange={(checked) =>
                setFormData({ ...formData, passwords_enabled: checked })
              }
            />
          </div>

          {formData.passwords_enabled && (
            <div className="space-y-2">
              <Label htmlFor="vw-url">VaultWarden URL</Label>
              <Input
                id="vw-url"
                type="url"
                placeholder="https://vault.example.com"
                value={formData.passwords_vaultwarden_url || ''}
                onChange={(e) =>
                  setFormData({ ...formData, passwords_vaultwarden_url: e.target.value })
                }
              />
            </div>
          )}
        </CardContent>
      </Card>

      {/* Advanced Settings */}
      <Card>
        <CardHeader>
          <CardTitle>Advanced Settings</CardTitle>
          <CardDescription>Data directory and configuration file paths</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="data-dir">Data Directory</Label>
            <Input
              id="data-dir"
              placeholder="/path/to/data"
              value={formData.data_dir || ''}
              onChange={(e) => setFormData({ ...formData, data_dir: e.target.value })}
            />
            <p className="text-xs text-muted-foreground">
              Directory for storing databases and sync state
            </p>
          </div>

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
                  Permanently reset all configuration and restart the setup wizard
                </p>
              </div>
              <Button
                onClick={handleResetConfiguration}
                variant="destructive"
                disabled={loading}
              >
                <Trash2 className="w-4 h-4 mr-2" />
                Reset All Configuration
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
        <Button onClick={handleSave} disabled={loading}>
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
    </div>
  );
}

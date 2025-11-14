import { useState, useEffect } from 'react';
import { CheckCircle, ArrowRight, ArrowLeft, Loader2, FileText, Calendar, Key, Download, Shield, AlertTriangle, ExternalLink, Image as ImageIcon } from 'lucide-react';
import confetti from 'canvas-confetti';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Progress } from '@/components/ui/progress';
import { FolderBrowserDialog } from '@/components/FolderBrowserDialog';
import { useAppStore } from '@/store/app-store';
import apiClient from '@/lib/api-client';
import type { AppConfig, ConnectionTestResponse, SetupVerificationResponse } from '@/types/api';

const STEPS = [
  { id: 'welcome', title: 'Welcome', description: 'Get started with iCloudBridge' },
  { id: 'data-storage', title: 'Data Storage', description: 'Configure data directory' },
  { id: 'notes', title: 'Notes', description: 'Apple Notes sync settings' },
  { id: 'reminders', title: 'Reminders', description: 'Apple Reminders sync settings' },
  { id: 'passwords', title: 'Passwords', description: 'Password sync settings' },
  { id: 'photos', title: 'Photos', description: 'Photo sync settings' },
  { id: 'test', title: 'Test', description: 'Test your configuration' },
  { id: 'complete', title: 'Complete', description: 'Ready to sync!' },
];

type PasswordProvider = 'vaultwarden' | 'nextcloud';

export default function FirstRunWizard() {
  const { isFirstRun, setIsFirstRun, setWizardCompleted, setConfig } = useAppStore();
  const [currentStep, setCurrentStep] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<ConnectionTestResponse | null>(null);
  const [verification, setVerification] = useState<SetupVerificationResponse | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [showFolderBrowser, setShowFolderBrowser] = useState(false);
  const [showPhotosFolderBrowser, setShowPhotosFolderBrowser] = useState(false);

  // Form data
  const [formData, setFormData] = useState<Partial<AppConfig>>({
    notes_enabled: true,
    notes_remote_folder: '~/Documents/Notes',
    reminders_enabled: false,
    reminders_use_nextcloud: true,
    reminders_nextcloud_url: '',
    reminders_caldav_url: '',
    reminders_caldav_username: '',
    reminders_caldav_password: '',
    passwords_enabled: false,
    passwords_provider: 'vaultwarden',
    passwords_vaultwarden_url: '',
    passwords_vaultwarden_email: '',
    passwords_vaultwarden_password: '',
    passwords_nextcloud_url: '',
    passwords_nextcloud_username: '',
    passwords_nextcloud_app_password: '',
    photos_enabled: false,
    photos_default_album: 'iCloudBridge Imports',
    photo_sources: {
      default: {
        path: '~/Pictures',
        recursive: true,
        include_images: true,
        include_videos: true,
        metadata_sidecars: true,
      },
    },
    data_dir: '~/.icloudbridge',
  });

  const passwordProvider: PasswordProvider = (formData.passwords_provider as PasswordProvider) ?? 'vaultwarden';

  // Trigger confetti when reaching the complete step
  useEffect(() => {
    if (currentStep === STEPS.length - 1) {
      // Fire confetti
      const duration = 3 * 1000;
      const animationEnd = Date.now() + duration;
      const defaults = { startVelocity: 30, spread: 360, ticks: 60, zIndex: 9999 };

      function randomInRange(min: number, max: number) {
        return Math.random() * (max - min) + min;
      }

      const interval = window.setInterval(function() {
        const timeLeft = animationEnd - Date.now();

        if (timeLeft <= 0) {
          return clearInterval(interval);
        }

        const particleCount = 50 * (timeLeft / duration);
        confetti({
          ...defaults,
          particleCount,
          origin: { x: randomInRange(0.1, 0.3), y: Math.random() - 0.2 }
        });
        confetti({
          ...defaults,
          particleCount,
          origin: { x: randomInRange(0.7, 0.9), y: Math.random() - 0.2 }
        });
      }, 250);

      return () => clearInterval(interval);
    }
  }, [currentStep]);

  // Verify setup when on Notes step
  useEffect(() => {
    const notesStepIndex = STEPS.findIndex(s => s.id === 'notes');
    if (currentStep === notesStepIndex && formData.notes_enabled) {
      loadVerification();
    }
  }, [currentStep, formData.notes_enabled]);

  const loadVerification = async () => {
    try {
      setVerifying(true);
      const result = await apiClient.verifySetup();
      setVerification(result);
    } catch (err) {
      console.error('Failed to verify setup:', err);
    } finally {
      setVerifying(false);
    }
  };

  const handleNext = () => {
    if (currentStep < STEPS.length - 1) {
      setCurrentStep(currentStep + 1);
      setError(null);
    }
  };

  const handleBack = () => {
    if (currentStep > 0) {
      setCurrentStep(currentStep - 1);
      setError(null);
    }
  };

  const handleTestConnection = async () => {
    try {
      setLoading(true);
      setError(null);
      setTestResult(null);

      // Build config update with only enabled services' credentials
      const configUpdate: Partial<AppConfig> = {
        notes_enabled: formData.notes_enabled,
        notes_remote_folder: formData.notes_remote_folder,
        data_dir: formData.data_dir,
      };

      // Only include reminders config if enabled
      if (formData.reminders_enabled) {
        configUpdate.reminders_enabled = true;
        configUpdate.reminders_caldav_url = formData.reminders_caldav_url;
        configUpdate.reminders_caldav_username = formData.reminders_caldav_username;
        configUpdate.reminders_caldav_password = formData.reminders_caldav_password;
        configUpdate.reminders_use_nextcloud = formData.reminders_use_nextcloud;
        configUpdate.reminders_nextcloud_url = formData.reminders_nextcloud_url;
      } else {
        configUpdate.reminders_enabled = false;
      }

      // Only include passwords config if enabled
      if (formData.passwords_enabled) {
        configUpdate.passwords_enabled = true;
        configUpdate.passwords_provider = passwordProvider;
        if (passwordProvider === 'nextcloud') {
          configUpdate.passwords_nextcloud_url = formData.passwords_nextcloud_url;
          configUpdate.passwords_nextcloud_username = formData.passwords_nextcloud_username;
          configUpdate.passwords_nextcloud_app_password = formData.passwords_nextcloud_app_password;
        } else {
          configUpdate.passwords_vaultwarden_url = formData.passwords_vaultwarden_url;
          configUpdate.passwords_vaultwarden_email = formData.passwords_vaultwarden_email;
          configUpdate.passwords_vaultwarden_password = formData.passwords_vaultwarden_password;
        }
      } else {
        configUpdate.passwords_enabled = false;
      }

      if (formData.photos_enabled) {
        configUpdate.photos_enabled = true;
        configUpdate.photos_default_album = formData.photos_default_album;
        configUpdate.photo_sources = formData.photo_sources;
      } else {
        configUpdate.photos_enabled = false;
      }

      // Save credentials for enabled services
      console.log('Sending config update:', JSON.stringify(configUpdate, null, 2));
      await apiClient.updateConfig(configUpdate);

      // Determine which services to test based on what's enabled
      const servicesToTest: string[] = [];
      if (formData.reminders_enabled) {
        servicesToTest.push('reminders');
      }
      if (formData.passwords_enabled) {
        servicesToTest.push('passwords');
      }

      if (servicesToTest.length === 0) {
        setError('Please enable at least one service (Reminders or Passwords) to test connection');
        return;
      }

      // Test each enabled service
      const results = await Promise.all(
        servicesToTest.map(async (service) => {
          const result = await apiClient.testConnection(service);
          return { service, ...result };
        })
      );

      // Check if all tests passed
      const allSuccess = results.every(r => r.success);
      const passwordServiceLabel = passwordProvider === 'nextcloud' ? 'Nextcloud Passwords' : 'VaultWarden (Passwords)';
      const messages = results.map(r => {
        const serviceName = r.service === 'reminders' ? 'CalDAV (Reminders)' : passwordServiceLabel;
        return `${serviceName}: ${r.message}`;
      }).join('\n');

      setTestResult({
        success: allSuccess,
        message: messages,
      });

      if (allSuccess) {
        // Auto-advance after successful test
        setTimeout(() => {
          handleNext();
        }, 1500);
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : String(err);
      setError(errorMessage);
      setTestResult({ success: false, message: errorMessage });
    } finally {
      setLoading(false);
    }
  };

  const handleComplete = async () => {
    try {
      setLoading(true);
      setError(null);

      // Build config update with only enabled services' credentials
      const configUpdate: Partial<AppConfig> = {
        notes_enabled: formData.notes_enabled,
        notes_remote_folder: formData.notes_remote_folder,
        data_dir: formData.data_dir,
      };

      // Only include reminders config if enabled
      if (formData.reminders_enabled) {
        configUpdate.reminders_enabled = true;
        configUpdate.reminders_caldav_url = formData.reminders_caldav_url;
        configUpdate.reminders_caldav_username = formData.reminders_caldav_username;
        configUpdate.reminders_caldav_password = formData.reminders_caldav_password;
        configUpdate.reminders_use_nextcloud = formData.reminders_use_nextcloud;
        configUpdate.reminders_nextcloud_url = formData.reminders_nextcloud_url;
      } else {
        configUpdate.reminders_enabled = false;
      }

      // Only include passwords config if enabled
      if (formData.passwords_enabled) {
        configUpdate.passwords_enabled = true;
        configUpdate.passwords_provider = passwordProvider;
        if (passwordProvider === 'nextcloud') {
          configUpdate.passwords_nextcloud_url = formData.passwords_nextcloud_url;
          configUpdate.passwords_nextcloud_username = formData.passwords_nextcloud_username;
          configUpdate.passwords_nextcloud_app_password = formData.passwords_nextcloud_app_password;
        } else {
          configUpdate.passwords_vaultwarden_url = formData.passwords_vaultwarden_url;
          configUpdate.passwords_vaultwarden_email = formData.passwords_vaultwarden_email;
          configUpdate.passwords_vaultwarden_password = formData.passwords_vaultwarden_password;
        }
      } else {
        configUpdate.passwords_enabled = false;
      }

      if (formData.photos_enabled) {
        configUpdate.photos_enabled = true;
        configUpdate.photos_default_album = formData.photos_default_album;
        configUpdate.photo_sources = formData.photo_sources;
      } else {
        configUpdate.photos_enabled = false;
      }

      // Save configuration - backend will automatically store passwords in keyring
      console.log('Saving configuration...');
      const config = await apiClient.updateConfig(configUpdate);
      console.log('Configuration saved successfully:', config);
      setConfig(config);

      console.log('All settings saved successfully');

      // Mark wizard as complete
      setWizardCompleted(true);
      setIsFirstRun(false);
    } catch (err) {
      console.error('Failed to save configuration:', err);
      const errorMessage = err instanceof Error ? err.message : 'Failed to save configuration';
      setError(errorMessage);
      // Add more detailed logging
      if (err && typeof err === 'object' && 'response' in err) {
        const responseError = err as { response?: { data?: unknown } };
        console.error('Response data:', responseError.response?.data);
      }
    } finally {
      setLoading(false);
    }
  };

  const renderStepContent = () => {
    switch (STEPS[currentStep].id) {
      case 'welcome':
        return (
          <div className="space-y-6 py-4">
            <div className="text-center space-y-4">
              <div className="w-16 h-16 bg-primary rounded-full flex items-center justify-center mx-auto">
                <span className="text-primary-foreground text-2xl font-bold">iC</span>
              </div>
              <div>
                <h3 className="text-2xl font-bold">Welcome to iCloudBridge!</h3>
                <p className="text-muted-foreground mt-2">
                  Sync your Apple Notes, Reminders, Photos, and Passwords across devices
                </p>
              </div>
            </div>

            <div className="space-y-3">
              <div className="flex items-start gap-3 p-3 border rounded-lg">
                <FileText className="w-5 h-5 text-primary mt-0.5" />
                <div>
                  <p className="font-medium">Notes Sync</p>
                  <p className="text-sm text-muted-foreground">
                    Sync Apple Notes with markdown files
                  </p>
                </div>
              </div>

              <div className="flex items-start gap-3 p-3 border rounded-lg">
                <Calendar className="w-5 h-5 text-primary mt-0.5" />
                <div>
                  <p className="font-medium">Reminders Sync</p>
                  <p className="text-sm text-muted-foreground">
                    Sync Apple Reminders via CalDAV
                  </p>
                </div>
              </div>

              <div className="flex items-start gap-3 p-3 border rounded-lg">
                <Key className="w-5 h-5 text-primary mt-0.5" />
                <div>
                  <p className="font-medium">Passwords Sync</p>
                  <p className="text-sm text-muted-foreground">
                    Sync passwords with Bitwarden / VaultWarden or Nextcloud Passwords
                  </p>
                </div>
              </div>

              <div className="flex items-start gap-3 p-3 border rounded-lg">
                <ImageIcon className="w-5 h-5 text-primary mt-0.5" />
                <div>
                  <p className="font-medium">Photo Sync</p>
                  <p className="text-sm text-muted-foreground">
                    Import local photos and videos directly to Apple Photos
                  </p>
                </div>
              </div>
            </div>

            <p className="text-sm text-muted-foreground text-center">
              This wizard will help you configure iCloudBridge in just a few steps
            </p>
          </div>
        );

      case 'data-storage':
        return (
          <div className="space-y-4 py-4">
            <div>
              <h3 className="text-lg font-semibold mb-2">Data Storage</h3>
              <p className="text-sm text-muted-foreground">
                Configure where iCloudBridge stores its data
              </p>
            </div>

            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="data-dir">Data Directory</Label>
                <Input
                  id="data-dir"
                  placeholder="~/.icloudbridge"
                  value={formData.data_dir}
                  onChange={(e) => setFormData({ ...formData, data_dir: e.target.value })}
                />
                <p className="text-xs text-muted-foreground">
                  Where to store sync data and databases
                </p>
              </div>
            </div>

            <Alert>
              <AlertDescription>
                iCloudBridge uses macOS native APIs to access your Apple Notes and Reminders.
                You'll be prompted by macOS for permission when needed. All passwords are stored securely in your system keychain.
              </AlertDescription>
            </Alert>
          </div>
        );

      case 'notes':
        return (
          <div className="space-y-4 py-4">
            <div className="flex items-center gap-3 mb-4">
              <FileText className="w-8 h-8 text-primary" />
              <div>
                <h3 className="text-lg font-semibold">Notes Sync</h3>
                <p className="text-sm text-muted-foreground">
                  Sync Apple Notes with markdown files
                </p>
              </div>
            </div>

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

            <div className="space-y-4">
              <div className="flex items-center justify-between p-4 border rounded-lg">
                <div>
                  <Label>Enable Notes Sync</Label>
                  <p className="text-sm text-muted-foreground">
                    Sync your Apple Notes to markdown
                  </p>
                </div>
                <Switch
                  checked={formData.notes_enabled}
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
                        value={formData.notes_remote_folder}
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
                                // Try to open System Preferences directly to FDA settings
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
            </div>

            <Alert>
              <AlertDescription>
                Notes sync stores a copy of your Apple Notes as markdown in a folder.
                You can sync with cloud storage services like Nextcloud by pointing the notes folder to your synced directory.
              </AlertDescription>
            </Alert>
          </div>
        );

      case 'reminders':
        return (
          <div className="space-y-4 py-4">
            <div className="flex items-center gap-3 mb-4">
              <Calendar className="w-8 h-8 text-primary" />
              <div>
                <h3 className="text-lg font-semibold">Reminders Sync</h3>
                <p className="text-sm text-muted-foreground">
                  Sync Apple Reminders via CalDAV
                </p>
              </div>
            </div>

            <div className="space-y-4">
              <div className="flex items-center justify-between p-4 border rounded-lg">
                <div>
                  <Label>Enable Reminders Sync</Label>
                  <p className="text-sm text-muted-foreground">
                    Sync with CalDAV servers like Nextcloud or iCloud
                  </p>
                </div>
                <Switch
                  checked={formData.reminders_enabled}
                  onCheckedChange={(checked) =>
                    setFormData({ ...formData, reminders_enabled: checked })
                  }
                />
              </div>

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
                  disabled={!formData.reminders_enabled}
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
                    disabled={!formData.reminders_enabled}
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
                  disabled={!formData.reminders_enabled}
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
                  disabled={!formData.reminders_enabled}
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
                  disabled={!formData.reminders_enabled || formData.reminders_use_nextcloud !== false}
                />
                <p className="text-xs text-muted-foreground">
                  {formData.reminders_use_nextcloud !== false
                    ? 'Auto-filled from Nextcloud URL'
                    : 'Full CalDAV server URL'}
                </p>
              </div>
            </div>
          </div>
        );

      case 'passwords':
        return (
          <div className="space-y-4 py-4">
            <div className="flex items-center gap-3 mb-4">
              <Key className="w-8 h-8 text-primary" />
              <div>
                <h3 className="text-lg font-semibold">Passwords Sync</h3>
                <p className="text-sm text-muted-foreground">
                  Sync passwords with Bitwarden / VaultWarden or Nextcloud Passwords
                </p>
              </div>
            </div>

            <div className="space-y-4">
              <div className="space-y-2">
                <Label>Password Provider</Label>
                <div className="grid gap-2">
                  <Button
                    type="button"
                    variant={passwordProvider === 'vaultwarden' ? 'default' : 'outline'}
                    className="justify-start"
                    onClick={() => setFormData({ ...formData, passwords_provider: 'vaultwarden' })}
                  >
                    Bitwarden / Vaultwarden (recommended)
                  </Button>
                  <Button
                    type="button"
                    variant={passwordProvider === 'nextcloud' ? 'default' : 'outline'}
                    className="justify-start"
                    onClick={() => setFormData({ ...formData, passwords_provider: 'nextcloud' })}
                  >
                    Nextcloud Passwords
                  </Button>
                </div>
              </div>

              <div className="flex items-center justify-between p-4 border rounded-lg">
                <div>
                  <Label>Enable Passwords Sync</Label>
                  <p className="text-sm text-muted-foreground">
                    {passwordProvider === 'nextcloud'
                      ? 'Sync passwords with your Nextcloud Passwords app'
                      : 'Sync passwords with Bitwarden or Vaultwarden'}
                  </p>
                </div>
                <Switch
                  checked={formData.passwords_enabled ?? false}
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
                      Nextcloud Passwords app does not support one-time passwords (OTP) or passkeys. For the best experience, we recommend Bitwarden/Vaultwarden.
                    </AlertDescription>
                  </Alert>

                  <div className="space-y-2">
                    <Label htmlFor="wizard-nc-url">Nextcloud URL</Label>
                    <Input
                      id="wizard-nc-url"
                      type="url"
                      placeholder="https://nextcloud.example.org"
                      value={formData.passwords_nextcloud_url || ''}
                      onChange={(e) =>
                        setFormData({ ...formData, passwords_nextcloud_url: e.target.value })
                      }
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="wizard-nc-username">Nextcloud Username</Label>
                    <Input
                      id="wizard-nc-username"
                      placeholder="nextcloud-user"
                      value={formData.passwords_nextcloud_username || ''}
                      onChange={(e) =>
                        setFormData({ ...formData, passwords_nextcloud_username: e.target.value })
                      }
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="wizard-nc-password">Nextcloud App Password</Label>
                    <Input
                      id="wizard-nc-password"
                      type="password"
                      placeholder="App password"
                      value={formData.passwords_nextcloud_app_password || ''}
                      onChange={(e) =>
                        setFormData({ ...formData, passwords_nextcloud_app_password: e.target.value })
                      }
                    />
                    <p className="text-xs text-muted-foreground">
                      NOT your Nextcloud password! You can create an app password in your Nextcloud account settings under 'Security'.
                    </p>
                  </div>
                </>
              )}

              {formData.passwords_enabled && passwordProvider === 'vaultwarden' && (
                <>
                  <div className="space-y-2">
                    <Label htmlFor="wizard-vw-url">Bitwarden/Vaultwarden URL</Label>
                    <Input
                      id="wizard-vw-url"
                      type="url"
                      placeholder="https://vault.bitwarden.com"
                      value={formData.passwords_vaultwarden_url || ''}
                      onChange={(e) =>
                        setFormData({ ...formData, passwords_vaultwarden_url: e.target.value })
                      }
                      onFocus={(e) => e.target.setAttribute('list', 'wizard-bitwarden-urls')}
                    />
                    <datalist id="wizard-bitwarden-urls">
                      <option value="https://vault.bitwarden.com" />
                      <option value="https://vault.bitwarden.eu" />
                    </datalist>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="wizard-vw-email">Bitwarden/Vaultwarden Email</Label>
                    <Input
                      id="wizard-vw-email"
                      type="email"
                      placeholder="your@email.com"
                      value={formData.passwords_vaultwarden_email || ''}
                      onChange={(e) =>
                        setFormData({ ...formData, passwords_vaultwarden_email: e.target.value })
                      }
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="wizard-vw-password">Bitwarden/Vaultwarden Password</Label>
                    <Input
                      id="wizard-vw-password"
                      type="password"
                      placeholder="Master password"
                      value={formData.passwords_vaultwarden_password || ''}
                      onChange={(e) =>
                        setFormData({ ...formData, passwords_vaultwarden_password: e.target.value })
                      }
                    />
                  </div>
                </>
              )}
            </div>

            <Alert>
              <AlertDescription>
                iCloudBridge never stores passwords directly — credentials remain in your chosen provider.
              </AlertDescription>
            </Alert>
          </div>
        );

      case 'photos':
        return (
          <div className="space-y-4 py-4">
            <div className="flex items-center gap-3 mb-4">
              <ImageIcon className="w-8 h-8 text-primary" />
              <div>
                <h3 className="text-lg font-semibold">Photos Sync</h3>
                <p className="text-sm text-muted-foreground">
                  Import photos and videos from local folders
                </p>
              </div>
            </div>

            <div className="flex items-center justify-between p-4 border rounded-lg">
              <div>
                <Label>Enable Photos Sync</Label>
                <p className="text-sm text-muted-foreground">
                  Automatically import media from your Mac to Apple Photos
                </p>
              </div>
              <Switch
                checked={formData.photos_enabled ?? false}
                onCheckedChange={(checked) =>
                  setFormData({ ...formData, photos_enabled: checked })
                }
              />
            </div>

            {formData.photos_enabled && (
              <>
                <div className="space-y-2">
                  <Label htmlFor="wizard-photo-folder">Photos Source Folder</Label>
                  <div className="flex gap-2">
                    <Input
                      id="wizard-photo-folder"
                      placeholder="~/Pictures"
                      value={formData.photo_sources?.default?.path || ''}
                      onChange={(e) => {
                        const nextPath = e.target.value;
                        setFormData({
                          ...formData,
                          photo_sources: {
                            ...(formData.photo_sources || {}),
                            default: {
                              ...(formData.photo_sources?.default || {}),
                              path: nextPath,
                              recursive: true,
                              include_images: true,
                              include_videos: true,
                              metadata_sidecars: true,
                            },
                          },
                        });
                      }}
                    />
                    <Button variant="outline" onClick={() => setShowPhotosFolderBrowser(true)}>
                      Browse
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Local folder containing photos/videos to ingest.
                  </p>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="wizard-photo-album">Default Album</Label>
                  <Input
                    id="wizard-photo-album"
                    placeholder="iCloudBridge Imports"
                    value={formData.photos_default_album || ''}
                    onChange={(e) =>
                      setFormData({ ...formData, photos_default_album: e.target.value })
                    }
                  />
                </div>

                <Alert>
                  <AlertDescription>
                    Files are deduplicated using hashes, so you can safely re-run syncs even if media moves between folders.
                  </AlertDescription>
                </Alert>
              </>
            )}
          </div>
        );

      case 'test': {
        // Determine what services need testing
        const needsTest = formData.reminders_enabled || formData.passwords_enabled;
        let testService = '';
      if (formData.reminders_enabled && formData.passwords_enabled) {
        testService = `CalDAV (Reminders) and ${passwordProvider === 'nextcloud' ? 'Nextcloud Passwords' : 'VaultWarden (Passwords)'}`;
      } else if (formData.reminders_enabled) {
        testService = 'CalDAV (Reminders)';
      } else if (formData.passwords_enabled) {
        testService = passwordProvider === 'nextcloud' ? 'Nextcloud Passwords' : 'VaultWarden (Passwords)';
      }

        return (
          <div className="space-y-4 py-4">
            {needsTest ? (
              <>
                <div>
                  <h3 className="text-lg font-semibold mb-2">Test Connection</h3>
                  <p className="text-sm text-muted-foreground">
                    Let's verify your {testService} connection
                  </p>
                </div>

                {!testResult && !loading && (
                  <div className="text-center py-8">
                    <p className="text-muted-foreground mb-4">
                      Click the button below to test your configuration
                    </p>
                    <Button onClick={handleTestConnection} size="lg">
                      Test Connection
                    </Button>
                  </div>
                )}
              </>
            ) : (
              <>
                <div>
                  <h3 className="text-lg font-semibold mb-2">Notes Configuration</h3>
                  <p className="text-sm text-muted-foreground">
                    Your Notes sync is configured and ready
                  </p>
                </div>
                <Alert>
                  <AlertDescription>
                    Notes sync uses AppleScript to access your local Notes.app - no connection test needed.
                    You can proceed to complete the setup.
                  </AlertDescription>
                </Alert>
              </>
            )}

            {loading && (
              <div className="text-center py-8">
                <Loader2 className="w-8 h-8 animate-spin mx-auto mb-4 text-primary" />
                <p className="text-muted-foreground">Testing connection...</p>
              </div>
            )}

            {testResult && (
              <Alert
                variant={testResult.success ? 'success' : 'destructive'}
                className="mt-4"
              >
                <div className="flex items-start gap-2">
                  {testResult.success ? (
                    <CheckCircle className="w-5 h-5 mt-0.5" />
                  ) : (
                    <div className="w-5 h-5 mt-0.5" />
                  )}
                  <div className="flex-1">
                    <p className="font-medium">
                      {testResult.success ? 'Connection Successful!' : 'Connection Failed'}
                    </p>
                    <AlertDescription className="mt-1">
                      {testResult.message}
                    </AlertDescription>
                  </div>
                </div>
              </Alert>
            )}

            {error && (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
          </div>
        );
      }

      case 'complete':
        return (
          <div className="space-y-6 py-4">
            <div className="text-center space-y-4">
              <div className="w-16 h-16 bg-green-500 rounded-full flex items-center justify-center mx-auto">
                <CheckCircle className="w-8 h-8 text-white" />
              </div>
              <div>
                <h3 className="text-2xl font-bold">You're All Set!</h3>
                <p className="text-muted-foreground mt-2">
                  iCloudBridge is now configured and ready to use
                </p>
              </div>
            </div>

            <div className="space-y-2 text-sm">
              <p className="font-medium">What's next?</p>
              <ul className="list-disc list-inside space-y-1 text-muted-foreground">
                <li>Visit the Dashboard to see your sync status</li>
                <li>Trigger your first sync from the service pages</li>
                <li>Set up automated schedules in the Schedules page</li>
                <li>Customize your settings anytime in the Settings page</li>
              </ul>
            </div>

            <Alert>
              <AlertDescription>
                You can always change these settings later in the Settings page.
              </AlertDescription>
            </Alert>
          </div>
        );

      default:
        return null;
    }
  };

  if (!isFirstRun) return null;

  return (
    <>
    <Dialog open={isFirstRun} onOpenChange={() => {}}>
      <DialogContent className="sm:max-w-[600px] max-h-[90vh] overflow-y-auto" onInteractOutside={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle>{STEPS[currentStep].title}</DialogTitle>
          <DialogDescription>{STEPS[currentStep].description}</DialogDescription>
        </DialogHeader>

        {/* Progress indicator */}
        <div className="space-y-2">
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>
              Step {currentStep + 1} of {STEPS.length}
            </span>
            <span>{Math.round(((currentStep + 1) / STEPS.length) * 100)}%</span>
          </div>
          <Progress value={((currentStep + 1) / STEPS.length) * 100} />
        </div>

        {/* Step content */}
        {renderStepContent()}

        {/* Error display */}
        {error && currentStep !== STEPS.length - 2 && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {/* Footer */}
        <DialogFooter>
          <div className="flex justify-between w-full">
            <Button
              variant="outline"
              onClick={handleBack}
              disabled={currentStep === 0 || loading}
            >
              <ArrowLeft className="w-4 h-4 mr-2" />
              Back
            </Button>

            {currentStep < STEPS.length - 1 ? (
              <Button
                onClick={
                  currentStep === STEPS.length - 2 ? handleTestConnection : handleNext
                }
                disabled={
                  loading ||
                  (currentStep === STEPS.length - 2 && !testResult?.success)
                }
              >
                {loading ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    Testing...
                  </>
                ) : currentStep === STEPS.length - 2 ? (
                  'Test Connection'
                ) : (
                  <>
                    Next
                    <ArrowRight className="w-4 h-4 ml-2" />
                  </>
                )}
              </Button>
            ) : (
              <Button onClick={handleComplete} disabled={loading}>
                {loading ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    Saving...
                  </>
                ) : (
                  'Get Started'
                )}
              </Button>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>

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
    </>
  );
}

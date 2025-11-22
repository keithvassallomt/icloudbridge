import { useState, useEffect, useMemo, useCallback } from 'react';
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
import { useSyncStore } from '@/store/sync-store';
import apiClient from '@/lib/api-client';
import type { AppConfig, ConnectionTestResponse, SetupVerificationResponse } from '@/types/api';

const ANALYZE_REGEX = /Analyzing file (\d+) of (\d+)/i;
const FOUND_REGEX = /Found (\d+) files/i;
const COMPLETE_REGEX = /Initial scan complete - (\d+) files discovered/i;

const extractScanStats = (message?: string): { processed: number; total: number } | null => {
  if (!message) return null;
  const analyzing = message.match(ANALYZE_REGEX);
  if (analyzing) {
    return { processed: parseInt(analyzing[1], 10), total: parseInt(analyzing[2], 10) };
  }
  const found = message.match(FOUND_REGEX);
  if (found) {
    const total = parseInt(found[1], 10);
    return { processed: 0, total };
  }
  const complete = message.match(COMPLETE_REGEX);
  if (complete) {
    const total = parseInt(complete[1], 10);
    return { processed: total, total };
  }
  return null;
};

const STEPS = [
  { id: 'welcome', title: 'Welcome', description: 'Get started with iCloudBridge' },
  { id: 'data-storage', title: 'Data Storage', description: 'Configure data directory' },
  { id: 'notes', title: 'Notes', description: 'Apple Notes sync settings' },
  { id: 'reminders', title: 'Reminders', description: 'Apple Reminders sync settings' },
  { id: 'passwords', title: 'Passwords', description: 'Password sync settings' },
  { id: 'photos', title: 'Photos', description: 'Photo sync settings' },
  { id: 'test', title: 'Test', description: 'Test your configuration' },
  { id: 'photos-scan', title: 'Initial Photo Scan', description: 'Index your photo library' },
  { id: 'complete', title: 'Complete', description: 'Ready to sync!' },
];

type PasswordProvider = 'vaultwarden' | 'nextcloud';

export default function FirstRunWizard() {
  const { isFirstRun, setIsFirstRun, setWizardCompleted, setConfig } = useAppStore();
  const { activeSyncs } = useSyncStore();
  const [currentStep, setCurrentStep] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<ConnectionTestResponse | null>(null);
  const [verification, setVerification] = useState<SetupVerificationResponse | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [showFolderBrowser, setShowFolderBrowser] = useState(false);
  const [showPhotosFolderBrowser, setShowPhotosFolderBrowser] = useState(false);
  const [loadingMessage, setLoadingMessage] = useState<string | null>(null);
  const [initialScanStarted, setInitialScanStarted] = useState(false);
  const [initialScanComplete, setInitialScanComplete] = useState(false);
  const [initialScanProgress, setInitialScanProgress] = useState(0);
  const [initialScanMessage, setInitialScanMessage] = useState('Waiting to start...');
  const [initialScanStats, setInitialScanStats] = useState<{ processed: number; total: number } | null>(null);
  const [initialScanError, setInitialScanError] = useState<string | null>(null);
  const [scanConfigSignature, setScanConfigSignature] = useState<string | null>(null);

  // Form data
  const [formData, setFormData] = useState<Partial<AppConfig>>({
    notes_enabled: false,
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
  const needsConnectionTest = useMemo(
    () => Boolean(formData.reminders_enabled || formData.passwords_enabled),
    [formData.reminders_enabled, formData.passwords_enabled]
  );
  const requiresPhotoScan = useMemo(() => Boolean(formData.photos_enabled), [formData.photos_enabled]);
  const photoConfigSignature = useMemo(
    () =>
      JSON.stringify({
        enabled: formData.photos_enabled ?? false,
        sources: formData.photo_sources,
        album: formData.photos_default_album,
      }),
    [formData.photos_enabled, formData.photo_sources, formData.photos_default_album]
  );
  const currentStepId = STEPS[currentStep].id;

  // When the wizard is re-opened (e.g., after a full reset), start from the beginning
  useEffect(() => {
    if (isFirstRun) {
      setCurrentStep(0);
      setError(null);
      setTestResult(null);
      setVerification(null);
      setInitialScanStarted(false);
      setInitialScanComplete(false);
      setInitialScanProgress(0);
      setInitialScanMessage('Waiting to start...');
      setInitialScanStats(null);
      setInitialScanError(null);
    }
  }, [isFirstRun]);

  const buildConfigPayload = useCallback((): Partial<AppConfig> => {
    const configUpdate: Partial<AppConfig> = {
      notes_enabled: formData.notes_enabled ?? false,
      notes_remote_folder: formData.notes_remote_folder,
      data_dir: formData.data_dir,
    };

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

    return configUpdate;
  }, [formData, passwordProvider]);

  const startInitialScan = useCallback(async () => {
    if (!requiresPhotoScan) {
      return;
    }

    try {
      setInitialScanError(null);
      setInitialScanStarted(true);
      setInitialScanComplete(false);
      setInitialScanProgress(5);
      setInitialScanMessage('Saving configuration...');
      setInitialScanStats(null);

      const configUpdate = buildConfigPayload();
      const updated = await apiClient.updateConfig(configUpdate);
      setConfig(updated);

      setInitialScanProgress(10);
      setInitialScanMessage('Starting initial scan...');

      const response = await apiClient.syncPhotos(undefined, false, true);
      const stats = response?.stats ?? {};
      const discovered = typeof stats.discovered === 'number' ? stats.discovered : undefined;
      if (typeof discovered === 'number') {
        setInitialScanStats({ processed: discovered, total: discovered });
      }
      setInitialScanComplete(true);
      setInitialScanProgress(100);
      setInitialScanMessage('Initial scan complete');
      setScanConfigSignature(photoConfigSignature);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Initial scan failed';
      setInitialScanError(message);
      setInitialScanStarted(false);
    }
  }, [buildConfigPayload, requiresPhotoScan, setConfig, photoConfigSignature]);
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
    if (currentStepId === 'notes' && formData.notes_enabled) {
      loadVerification();
    }
  }, [currentStepId, formData.notes_enabled]);

  useEffect(() => {
    if (currentStepId === 'test' && !needsConnectionTest) {
      setCurrentStep((prev) => Math.min(prev + 1, STEPS.length - 1));
      setError(null);
      setTestResult(null);
    }
  }, [currentStepId, needsConnectionTest]);

  useEffect(() => {
    if (currentStepId !== 'photos-scan') {
      return;
    }

    if (!requiresPhotoScan) {
      setCurrentStep((prev) => Math.min(prev + 1, STEPS.length - 1));
      return;
    }

    if (!initialScanStarted && !initialScanComplete && !initialScanError) {
      void startInitialScan();
    }
  }, [
    currentStepId,
    requiresPhotoScan,
    initialScanStarted,
    initialScanComplete,
    initialScanError,
    startInitialScan,
  ]);

  useEffect(() => {
    if (!requiresPhotoScan) {
      return;
    }
    const photoSync = activeSyncs.get('photos');
    if (photoSync) {
      setInitialScanProgress(photoSync.progress ?? 0);
      setInitialScanMessage(photoSync.message || 'Scanning...');
      const stats = extractScanStats(photoSync.message);
      if (stats) {
        setInitialScanStats(stats);
      }
    }
  }, [activeSyncs, requiresPhotoScan]);

  useEffect(() => {
    if (!requiresPhotoScan) {
      setInitialScanStarted(false);
      setInitialScanComplete(false);
      setInitialScanProgress(0);
      setInitialScanStats(null);
      setInitialScanMessage('Waiting to start...');
      setInitialScanError(null);
      setScanConfigSignature(null);
      return;
    }

    if (scanConfigSignature && photoConfigSignature !== scanConfigSignature) {
      setInitialScanStarted(false);
      setInitialScanComplete(false);
      setInitialScanProgress(0);
      setInitialScanStats(null);
      setInitialScanMessage('Waiting to start...');
      setInitialScanError(null);
      setScanConfigSignature(null);
    }
  }, [requiresPhotoScan, photoConfigSignature, scanConfigSignature]);

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
      setLoadingMessage('Testing connection...');
      setError(null);
      setTestResult(null);

      const configUpdate = buildConfigPayload();

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
      setLoadingMessage(null);
    }
  };

  const handleComplete = async () => {
    try {
      setLoading(true);
      setLoadingMessage('Saving configuration...');
      setError(null);

      const configUpdate = buildConfigPayload();

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
      setLoadingMessage(null);
    }
  };

  const processedCount = initialScanStats?.processed ?? null;
  const totalCount = initialScanStats?.total ?? null;
  const remainingCount =
    processedCount !== null && totalCount !== null ? Math.max(totalCount - processedCount, 0) : null;
  const isLastStep = currentStepId === 'complete';
  const isTestStepActive = currentStepId === 'test' && needsConnectionTest;
  const isPhotoScanStep = currentStepId === 'photos-scan' && requiresPhotoScan;
  const nextButtonDisabled =
    loading || (isPhotoScanStep && (!initialScanComplete || !!initialScanError));
  const backButtonDisabled =
    currentStep === 0 || loading || (isPhotoScanStep && initialScanStarted && !initialScanComplete);
  const nextButtonLabel = isTestStepActive
    ? 'Test Connection'
    : isPhotoScanStep
      ? initialScanComplete
        ? 'Continue'
        : 'Scanning...'
      : 'Next';

  const renderStepContent = () => {
    switch (STEPS[currentStep].id) {
      case 'welcome':
        return (
          <div className="space-y-6 py-4">
            <div className="text-center space-y-4">
              <div className="mx-auto">
                <img
                  src="/icloudbridge_dark_650.png"
                  alt="iCloudBridge"
                  className="h-16 w-auto mx-auto dark:hidden"
                />
                <img
                  src="/icloudbridge_light_650.png"
                  alt="iCloudBridge"
                  className="h-16 w-auto mx-auto hidden dark:block"
                />
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
                  checked={formData.notes_enabled ?? false}
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
                      iCloudBridge needs Full Disk Access to read your Notes database.
                    </p>

                    {verification && !verification.full_disk_access.has_access && (
                      <div className="space-y-2">
                        <p className="text-sm">To grant Full Disk Access:</p>
                        <ol className="text-sm text-muted-foreground space-y-1 ml-4 list-decimal">
                          <li>Open System Settings → Privacy & Security → Full Disk Access</li>
                          <li>Click the lock icon to make changes</li>
                          <li>Drag the iCloudBridge app to the list, or click + to add it</li>
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
        const needsTest = needsConnectionTest;
        let testService = '';
        if (formData.reminders_enabled && formData.passwords_enabled) {
          testService = `CalDAV (Reminders) and ${
            passwordProvider === 'nextcloud' ? 'Nextcloud Passwords' : 'VaultWarden (Passwords)'
          }`;
        } else if (formData.reminders_enabled) {
          testService = 'CalDAV (Reminders)';
        } else if (formData.passwords_enabled) {
          testService =
            passwordProvider === 'nextcloud' ? 'Nextcloud Passwords' : 'VaultWarden (Passwords)';
        }

        const noTestServices = [];
        if (formData.notes_enabled) noTestServices.push('Notes');
        if (formData.photos_enabled) noTestServices.push('Photos');
        const noTestLabel =
          noTestServices.length > 0 ? noTestServices.join(' & ') : 'Selected services';

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
                  <h3 className="text-lg font-semibold mb-2">
                    {noTestLabel} Configuration
                  </h3>
                  <p className="text-sm text-muted-foreground">
                    {noTestLabel} {noTestServices.length > 1 ? 'are' : 'is'} configured and ready.
                  </p>
                </div>
                <Alert>
                  <AlertDescription>
                    {noTestServices.length > 0
                      ? `${noTestLabel} ${noTestServices.length > 1 ? 'do' : 'does'} not require a connection test.`
                      : 'No connection test is required for the selected services.'}
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

      case 'photos-scan': {
        if (!requiresPhotoScan) {
          return null;
        }

        return (
          <div className="space-y-4 py-4">
            <div className="flex items-center gap-3">
              <ImageIcon className="w-8 h-8 text-primary" />
              <div>
                <h3 className="text-lg font-semibold">Initial Photo Scan</h3>
                <p className="text-sm text-muted-foreground">
                  We're indexing your photo sources so future imports are fast.
                </p>
              </div>
            </div>

            <Alert>
              <AlertDescription>
                Keep this window open while we scan. This only runs once unless you change your photo settings.
              </AlertDescription>
            </Alert>

            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">{initialScanMessage}</span>
                <span className="font-medium">{initialScanProgress}%</span>
              </div>
              <Progress value={initialScanProgress} />
            </div>

            <div className="grid grid-cols-2 gap-3 text-sm">
              <div className="border rounded-lg p-3">
                <p className="text-xs text-muted-foreground uppercase tracking-wide">Scanned</p>
                <p className="text-lg font-semibold">
                  {processedCount !== null ? processedCount : 'Calculating...'}
                </p>
              </div>
              <div className="border rounded-lg p-3">
                <p className="text-xs text-muted-foreground uppercase tracking-wide">Remaining</p>
                <p className="text-lg font-semibold">
                  {remainingCount !== null
                    ? remainingCount
                    : totalCount !== null
                      ? Math.max(totalCount - (processedCount ?? 0), 0)
                      : 'Calculating...'}
                </p>
              </div>
            </div>

            {initialScanError && (
              <Alert variant="destructive">
                <AlertDescription className="space-y-3">
                  <p>{initialScanError}</p>
                  <Button variant="outline" size="sm" onClick={() => startInitialScan()}>
                    Retry scan
                  </Button>
                </AlertDescription>
              </Alert>
            )}

            {initialScanComplete && (
              <Alert variant="success">
                <AlertDescription>
                  Initial photo scan complete. You can continue to finish setup.
                </AlertDescription>
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
              disabled={backButtonDisabled}
            >
              <ArrowLeft className="w-4 h-4 mr-2" />
              Back
            </Button>

            {!isLastStep ? (
              <Button
                onClick={isTestStepActive ? handleTestConnection : handleNext}
                disabled={nextButtonDisabled}
              >
                {loading ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    {loadingMessage || 'Processing...'}
                  </>
                ) : (
                  <>
                    {nextButtonLabel}
                    {!isTestStepActive && !isPhotoScanStep && (
                      <ArrowRight className="w-4 h-4 ml-2" />
                    )}
                  </>
                )}
              </Button>
            ) : (
              <Button onClick={handleComplete} disabled={loading}>
                {loading ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    {loadingMessage || 'Processing...'}
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

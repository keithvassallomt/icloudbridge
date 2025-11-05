import { useState } from 'react';
import { CheckCircle, ArrowRight, ArrowLeft, Loader2, FileText, Calendar, Key } from 'lucide-react';
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
import { useAppStore } from '@/store/app-store';
import apiClient from '@/lib/api-client';
import type { AppConfig } from '@/types/api';

const STEPS = [
  { id: 'welcome', title: 'Welcome', description: 'Get started with iCloudBridge' },
  { id: 'icloud', title: 'iCloud', description: 'Configure your iCloud account' },
  { id: 'services', title: 'Services', description: 'Choose services to sync' },
  { id: 'test', title: 'Test', description: 'Test your configuration' },
  { id: 'complete', title: 'Complete', description: 'Ready to sync!' },
];

export default function FirstRunWizard() {
  const { isFirstRun, setIsFirstRun, setWizardCompleted, setConfig } = useAppStore();
  const [currentStep, setCurrentStep] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<any>(null);

  // Form data
  const [formData, setFormData] = useState<Partial<AppConfig>>({
    notes_enabled: true,
    notes_folder: '~/Documents/Notes',
    reminders_enabled: false,
    reminders_use_nextcloud: true,
    reminders_nextcloud_url: '',
    reminders_caldav_url: '',
    reminders_caldav_username: '',
    reminders_caldav_password: '',
    passwords_enabled: false,
    passwords_vaultwarden_url: '',
    passwords_vaultwarden_email: '',
    passwords_vaultwarden_password: '',
    data_dir: '~/.icloudbridge',
  });

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
        notes_folder: formData.notes_folder,
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
        configUpdate.passwords_vaultwarden_url = formData.passwords_vaultwarden_url;
        configUpdate.passwords_vaultwarden_email = formData.passwords_vaultwarden_email;
        configUpdate.passwords_vaultwarden_password = formData.passwords_vaultwarden_password;
      } else {
        configUpdate.passwords_enabled = false;
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
      const messages = results.map(r => {
        const serviceName = r.service === 'reminders' ? 'CalDAV (Reminders)' : 'VaultWarden (Passwords)';
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
        notes_folder: formData.notes_folder,
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
        configUpdate.passwords_vaultwarden_url = formData.passwords_vaultwarden_url;
        configUpdate.passwords_vaultwarden_email = formData.passwords_vaultwarden_email;
        configUpdate.passwords_vaultwarden_password = formData.passwords_vaultwarden_password;
      } else {
        configUpdate.passwords_enabled = false;
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
        console.error('Response data:', (err as any).response?.data);
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
                  Sync your Apple Notes, Reminders, and Passwords across devices
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
                    Sync passwords with VaultWarden
                  </p>
                </div>
              </div>
            </div>

            <p className="text-sm text-muted-foreground text-center">
              This wizard will help you configure iCloudBridge in just a few steps
            </p>
          </div>
        );

      case 'icloud':
        return (
          <div className="space-y-4 py-4">
            <div>
              <h3 className="text-lg font-semibold mb-2">General Configuration</h3>
              <p className="text-sm text-muted-foreground">
                Configure data storage location
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

      case 'services':
        return (
          <div className="space-y-4 py-4">
            <div>
              <h3 className="text-lg font-semibold mb-2">Choose Services</h3>
              <p className="text-sm text-muted-foreground">
                Select which services you want to sync
              </p>
            </div>

            <div className="space-y-4">
              {/* Notes */}
              <div className="border rounded-lg p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <FileText className="w-5 h-5 text-primary" />
                    <div>
                      <p className="font-medium">Notes</p>
                      <p className="text-sm text-muted-foreground">
                        Sync Apple Notes with markdown files
                      </p>
                    </div>
                  </div>
                  <Switch
                    checked={formData.notes_enabled}
                    onCheckedChange={(checked) =>
                      setFormData({ ...formData, notes_enabled: checked })
                    }
                  />
                </div>

                {formData.notes_enabled && (
                  <div className="space-y-2 pl-8">
                    <Label htmlFor="notes-folder">Notes Folder</Label>
                    <div className="flex gap-2">
                      <Input
                        id="notes-folder"
                        placeholder="~/Documents/Notes"
                        value={formData.notes_folder}
                        onChange={(e) =>
                          setFormData({ ...formData, notes_folder: e.target.value })
                        }
                        className="flex-1"
                      />
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Enter the full path to where you want to store your notes (e.g., /Users/yourname/Documents/Notes)
                    </p>
                  </div>
                )}
              </div>

              {/* Reminders */}
              <div className="border rounded-lg p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <Calendar className="w-5 h-5 text-primary" />
                    <div>
                      <p className="font-medium">Reminders</p>
                      <p className="text-sm text-muted-foreground">
                        Sync Apple Reminders via CalDAV
                      </p>
                    </div>
                  </div>
                  <Switch
                    checked={formData.reminders_enabled}
                    onCheckedChange={(checked) =>
                      setFormData({ ...formData, reminders_enabled: checked })
                    }
                  />
                </div>

                {formData.reminders_enabled && (
                  <div className="space-y-3 pl-8">
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
                            // Clear URL when toggling
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
                  </div>
                )}
              </div>

              {/* Passwords */}
              <div className="border rounded-lg p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <Key className="w-5 h-5 text-primary" />
                    <div>
                      <p className="font-medium">Passwords</p>
                      <p className="text-sm text-muted-foreground">
                        Sync passwords with VaultWarden
                      </p>
                    </div>
                  </div>
                  <Switch
                    checked={formData.passwords_enabled}
                    onCheckedChange={(checked) =>
                      setFormData({ ...formData, passwords_enabled: checked })
                    }
                  />
                </div>

                {formData.passwords_enabled && (
                  <div className="space-y-3 pl-8">
                    <div className="space-y-2">
                      <Label htmlFor="vw-url">VaultWarden URL</Label>
                      <Input
                        id="vw-url"
                        type="url"
                        placeholder="https://vault.example.com"
                        value={formData.passwords_vaultwarden_url || ''}
                        onChange={(e) =>
                          setFormData({
                            ...formData,
                            passwords_vaultwarden_url: e.target.value,
                          })
                        }
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="vw-email">VaultWarden Email</Label>
                      <Input
                        id="vw-email"
                        type="email"
                        placeholder="your@email.com"
                        value={formData.passwords_vaultwarden_email || ''}
                        onChange={(e) =>
                          setFormData({
                            ...formData,
                            passwords_vaultwarden_email: e.target.value,
                          })
                        }
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="vw-password">VaultWarden Password</Label>
                      <Input
                        id="vw-password"
                        type="password"
                        placeholder="Your master password"
                        value={formData.passwords_vaultwarden_password || ''}
                        onChange={(e) =>
                          setFormData({
                            ...formData,
                            passwords_vaultwarden_password: e.target.value,
                          })
                        }
                      />
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        );

      case 'test':
        // Determine what services need testing
        const needsTest = formData.reminders_enabled || formData.passwords_enabled;
        let testService = '';
        if (formData.reminders_enabled && formData.passwords_enabled) {
          testService = 'CalDAV (Reminders) and VaultWarden (Passwords)';
        } else if (formData.reminders_enabled) {
          testService = 'CalDAV (Reminders)';
        } else if (formData.passwords_enabled) {
          testService = 'VaultWarden (Passwords)';
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
  );
}

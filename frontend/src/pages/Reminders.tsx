import { useEffect, useState } from 'react';
import { Calendar, RefreshCw, Trash2, Lock, PlayCircle, ChevronDown, Save, Plus, ArrowDown, ArrowUp, Info } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Autocomplete, type AutocompleteOption } from '@/components/ui/autocomplete';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import apiClient from '@/lib/api-client';
import { useSyncStore } from '@/store/sync-store';
import type { RemindersCalendar, SyncLog } from '@/types/api';

// Helper type for reminder items
interface ReminderItem {
  title: string;
  completed: boolean;
  due_date: string | null;
  is_recurring: boolean;
}

// Helper function to format due dates in a human-readable way
function formatDueDate(dueDateStr: string | null): string {
  if (!dueDateStr) return '';

  const dueDate = new Date(dueDateStr);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const dueDateOnly = new Date(dueDate);
  dueDateOnly.setHours(0, 0, 0, 0);

  const diffTime = dueDateOnly.getTime() - today.getTime();
  const diffDays = Math.round(diffTime / (1000 * 60 * 60 * 24));

  if (diffDays < 0) {
    return diffDays === -1 ? 'overdue by 1 day' : `overdue by ${Math.abs(diffDays)} days`;
  } else if (diffDays === 0) {
    return 'today';
  } else if (diffDays === 1) {
    return 'tomorrow';
  } else if (diffDays <= 7) {
    return `in ${diffDays} days`;
  } else {
    return dueDate.toLocaleDateString();
  }
}

// Helper component for Badge with Tooltip showing reminder details
function BadgeWithTooltip({
  children,
  items,
  className,
}: {
  children: React.ReactNode;
  items?: ReminderItem[];
  className?: string;
}) {
  // If no items or empty array, render badge without tooltip
  if (!items || items.length === 0) {
    return <Badge className={className}>{children}</Badge>;
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge className={className}>{children}</Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-sm max-h-60 overflow-y-auto">
        <div className="space-y-1">
          <p className="font-semibold text-xs mb-2">Affected reminders:</p>
          <ul className="space-y-1">
            {items.map((item, index) => (
              <li key={index} className="text-xs flex items-start gap-1.5">
                <span className="text-base leading-none mt-0.5">
                  {item.completed ? '●' : '○'}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="truncate">
                    {item.title}
                    {item.is_recurring && <span className="ml-1">↻</span>}
                  </div>
                  {item.due_date && (
                    <div className="text-muted-foreground text-[10px] mt-0.5">
                      {formatDueDate(item.due_date)}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

export default function Reminders() {
  const [lists, setLists] = useState<RemindersCalendar[]>([]);
  const [history, setHistory] = useState<SyncLog[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [simulating, setSimulating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [simulationResult, setSimulationResult] = useState<any | null>(null);
  const [mode, setMode] = useState<'auto' | 'manual'>('auto');
  const [listMappings, setListMappings] = useState<Record<string, string>>({});
  const [caldavCalendars, setCaldavCalendars] = useState<string[]>([]);
  const [caldavUrl, setCaldavUrl] = useState('');
  const [caldavUsername, setCaldavUsername] = useState('');
  const [caldavPassword, setCaldavPassword] = useState('');
  const [hasPassword, setHasPassword] = useState(false);
  const [showCredentialsForm, setShowCredentialsForm] = useState(false);
  const [showMappings, setShowMappings] = useState(false);
  const [showCredentialsPanel, setShowCredentialsPanel] = useState(false);

  const { activeSyncs } = useSyncStore();
  const activeSync = activeSyncs.get('reminders');

  // Check if credentials are fully configured
  const isConfigured = !!(caldavUrl && caldavUsername && hasPassword);

  useEffect(() => {
    loadData();
    loadConfig();
    checkPassword();
  }, []);

  const loadConfig = async () => {
    try {
      const config = await apiClient.getConfig();
      setCaldavUrl(config.reminders_caldav_url || '');
      setCaldavUsername(config.reminders_caldav_username || '');
      setMode(config.reminders_sync_mode || 'auto');
      setListMappings(config.reminders_calendar_mappings || {});
    } catch (err) {
      console.error('Failed to load config:', err);
    }
  };

  const checkPassword = async () => {
    try {
      const status = await apiClient.getRemindersStatus();
      setHasPassword(status.has_password || false);
    } catch (err) {
      console.error('Failed to check password:', err);
      setHasPassword(false);
    }
  };

  const loadData = async () => {
    try {
      setLoading(true);
      const [listsData, historyData, caldavCals] = await Promise.all([
        apiClient.getRemindersCalendars(),
        apiClient.getRemindersHistory(10),
        apiClient.getCaldavCalendars().catch(() => []), // Don't fail if CalDAV not configured
      ]);
      setLists(listsData);
      setHistory(historyData.logs);
      setCaldavCalendars(caldavCals);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load data');
    } finally {
      setLoading(false);
    }
  };

  const handleSync = async (dryRun: boolean = false) => {
    try {
      if (dryRun) {
        setSimulating(true);
        setSimulationResult(null);
      } else {
        setSyncing(true);
      }
      setError(null);
      setSuccess(null);

      const result = await apiClient.syncReminders({
        auto: mode === 'auto',
        dry_run: dryRun,
      });

      if (dryRun) {
        setSimulationResult(result);
        setSuccess('Simulation completed - see results below');
      } else {
        setSuccess(`Sync completed: ${result.message}`);
        await loadData();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : (dryRun ? 'Simulation failed' : 'Sync failed'));
    } finally {
      if (dryRun) {
        setSimulating(false);
      } else {
        setSyncing(false);
      }
    }
  };

  const handleSimulateSync = () => handleSync(true);

  const handleSaveCredentials = async () => {
    try {
      setLoading(true);
      setError(null);
      setSuccess(null);

      // Validate inputs
      if (!caldavUrl || !caldavUsername) {
        setError('CalDAV URL and username are required');
        setLoading(false);
        return;
      }

      // Update config with URL and username
      await apiClient.updateConfig({
        reminders_caldav_url: caldavUrl,
        reminders_caldav_username: caldavUsername,
      });

      // Save password if provided
      if (caldavPassword) {
        await apiClient.setRemindersPassword(caldavUsername, caldavPassword);
        setSuccess('CalDAV credentials saved successfully');
        setCaldavPassword(''); // Clear password field for security
        setHasPassword(true);
      } else {
        setSuccess('CalDAV URL and username saved successfully');
      }

      setShowCredentialsForm(false);
      setShowCredentialsPanel(false);
      await loadConfig(); // Reload config to show updated values
      await loadData(); // Reload data now that we have credentials
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save credentials');
    } finally {
      setLoading(false);
    }
  };

  const handleSaveMappings = async () => {
    try {
      setLoading(true);
      setError(null);
      setSuccess(null);

      // Filter out empty mappings
      const filteredMappings = Object.fromEntries(
        Object.entries(listMappings).filter(([_, caldav]) => caldav && caldav.trim())
      );

      await apiClient.updateConfig({
        reminders_sync_mode: mode,
        reminders_calendar_mappings: filteredMappings,
      });

      setSuccess('Calendar mappings saved successfully');
      await loadConfig();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save mappings');
    } finally {
      setLoading(false);
    }
  };

  const handleDeletePassword = async () => {
    if (!caldavUsername) {
      setError('No username configured');
      return;
    }

    if (!confirm('Are you sure you want to delete the stored CalDAV password?')) {
      return;
    }

    try {
      setLoading(true);
      await apiClient.deleteRemindersPassword();
      setSuccess('CalDAV password deleted successfully');
      setHasPassword(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete password');
    } finally {
      setLoading(false);
    }
  };

  const handleReset = async () => {
    if (!confirm('Are you sure you want to reset Reminders sync? This will clear all sync state.')) {
      return;
    }

    try {
      setLoading(true);
      await apiClient.resetReminders();
      setSuccess('Reminders sync reset successfully');
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Reset failed');
    } finally {
      setLoading(false);
    }
  };

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleString();
  };

  // Get all calendar options for autocomplete (existing + allow custom)
  const getCalendarOptions = (): AutocompleteOption[] => {
    return caldavCalendars.map(cal => ({ value: cal, label: cal }));
  };

  // Get unmapped CalDAV calendars (calendars that exist remotely but have no Apple list)
  // This filters out any CalDAV calendar that is already mapped to an Apple list
  // Example: If A->X, then X won't appear as a separate row since it's already mapped
  const getUnmappedCaldavCalendars = () => {
    const mappedCalendars = new Set(Object.values(listMappings));
    return caldavCalendars.filter(cal => !mappedCalendars.has(cal));
  };

  const renderCredentialsPanel = () => (
    <Card>
      <CardHeader>
        <CardTitle>CalDAV Credentials</CardTitle>
        <CardDescription>Configure CalDAV server connection details</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!showCredentialsForm ? (
          <div className="space-y-4">
            {/* Current credentials display */}
            {caldavUrl && caldavUsername ? (
              <div className="space-y-2">
                <div className="text-sm">
                  <span className="font-medium">Server:</span> {caldavUrl}
                </div>
                <div className="text-sm">
                  <span className="font-medium">Username:</span> {caldavUsername}
                </div>
                <div className="text-sm">
                  <span className="font-medium">Password:</span> {hasPassword ? '••••••••' : 'Not configured'}
                </div>
              </div>
            ) : (
              <div className="text-sm text-muted-foreground">
                No CalDAV credentials configured
              </div>
            )}

            <div className="flex gap-2">
              <Button onClick={() => setShowCredentialsForm(true)} className="flex-1">
                <Lock className="w-4 h-4 mr-2" />
                {caldavUrl && caldavUsername ? 'Update Credentials' : 'Configure Credentials'}
              </Button>
              {caldavUsername && hasPassword && (
                <Button onClick={handleDeletePassword} variant="outline">
                  Delete Password
                </Button>
              )}
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="caldav-url">CalDAV Server URL</Label>
              <Input
                id="caldav-url"
                type="url"
                placeholder="https://nextcloud.example.com/remote.php/dav"
                value={caldavUrl}
                onChange={(e) => setCaldavUrl(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Full URL to your CalDAV server
              </p>
            </div>

            <div className="space-y-2">
              <Label htmlFor="caldav-username">CalDAV Username</Label>
              <Input
                id="caldav-username"
                type="text"
                placeholder="username"
                value={caldavUsername}
                onChange={(e) => setCaldavUsername(e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="caldav-password">CalDAV Password</Label>
              <Input
                id="caldav-password"
                type="password"
                placeholder={hasPassword ? "Leave blank to keep existing" : "Enter password"}
                value={caldavPassword}
                onChange={(e) => setCaldavPassword(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                {hasPassword ? "Leave blank to keep existing password" : "Password is required"}
              </p>
            </div>

            <div className="flex gap-2">
              <Button
                onClick={handleSaveCredentials}
                disabled={!caldavUrl || !caldavUsername || (!hasPassword && !caldavPassword) || loading}
              >
                Save Credentials
              </Button>
              <Button
                onClick={() => {
                  setShowCredentialsForm(false);
                  if (isConfigured) {
                    setShowCredentialsPanel(false);
                  }
                  setCaldavPassword('');
                  loadConfig(); // Reset form to saved values
                }}
                variant="outline"
              >
                Cancel
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <Calendar className="w-8 h-8" />
            Reminders Sync
          </h1>
          <p className="text-muted-foreground">
            Sync Apple Reminders with CalDAV
          </p>
        </div>
        <Button onClick={loadData} variant="outline" disabled={loading}>
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

      {/* Show credentials panel at top (expanded) if not configured */}
      {!isConfigured && renderCredentialsPanel()}

      {/* Only show other panels if configured */}
      {isConfigured && (
        <>
          {/* Active Sync Progress */}
          {activeSync && (
            <Card>
              <CardHeader>
                <CardTitle>Sync in Progress</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <div className="flex justify-between text-sm mb-2">
                    <span>{activeSync.message}</span>
                    <span>{activeSync.progress}%</span>
                  </div>
                  <Progress value={activeSync.progress} />
                </div>
                {activeSync.stats && Object.keys(activeSync.stats).length > 0 && (
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    {Object.entries(activeSync.stats).map(([key, value]) => (
                      <div key={key} className="flex justify-between">
                        <span className="text-muted-foreground">{key}:</span>
                        <span className="font-medium">{String(value)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* Sync Controls */}
          <Card>
            <CardHeader>
              <CardTitle>Sync Configuration</CardTitle>
              <CardDescription>Configure and run reminders synchronization</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>Sync Mode</Label>
                <div className="flex gap-2">
                  <Button
                    variant={mode === 'auto' ? 'default' : 'outline'}
                    onClick={() => setMode('auto')}
                    className="flex-1"
                  >
                    Auto
                  </Button>
                  <Button
                    variant={mode === 'manual' ? 'default' : 'outline'}
                    onClick={() => setMode('manual')}
                    className="flex-1"
                  >
                    Manual
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  {mode === 'auto' && 'Automatically map all Apple Reminders lists to CalDAV calendars with matching names. "Reminders" → "tasks" by default.'}
                  {mode === 'manual' && 'Manually configure which Apple Reminders lists sync to which CalDAV calendars. Only mapped lists will sync.'}
                </p>
              </div>

              <div className="flex gap-2">
                <Button
                  onClick={() => handleSync(false)}
                  disabled={syncing || simulating || !!activeSync}
                  className="flex-1"
                >
                  {syncing || activeSync ? (
                    <>
                      <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                      Syncing...
                    </>
                  ) : (
                    <>
                      <RefreshCw className="w-4 h-4 mr-2" />
                      Run Sync
                    </>
                  )}
                </Button>
                <Button
                  onClick={handleSimulateSync}
                  disabled={syncing || simulating || !!activeSync}
                  variant="outline"
                  className="flex-1"
                >
                  {simulating ? (
                    <>
                      <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                      Simulating...
                    </>
                  ) : (
                    <>
                      <PlayCircle className="w-4 h-4 mr-2" />
                      Simulate Sync
                    </>
                  )}
                </Button>
                <Button
                  onClick={handleReset}
                  variant="destructive"
                  disabled={loading || syncing || simulating}
                >
                  <Trash2 className="w-4 h-4 mr-2" />
                  Reset
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* Simulation Results */}
          {simulationResult && (
            <Card>
              <CardHeader>
                <CardTitle>Simulation Results</CardTitle>
                <CardDescription>Preview of what would happen during sync</CardDescription>
              </CardHeader>
              <CardContent>
                <TooltipProvider>
                  <div className="space-y-4">
                  {simulationResult.message && (
                    <div className="text-sm text-muted-foreground">{simulationResult.message}</div>
                  )}
                  {simulationResult.stats && (() => {
                    const stats = simulationResult.stats;

                    // Check if this is auto mode with per_calendar stats
                    if (stats.per_calendar) {
                      const perCalendarStats = stats.per_calendar;

                      // Check if there are any changes across all calendars
                      const hasChanges = Object.values(perCalendarStats).some((calStats: any) =>
                        (calStats.created_local || 0) + (calStats.created_remote || 0) +
                        (calStats.updated_local || 0) + (calStats.updated_remote || 0) +
                        (calStats.deleted_local || 0) + (calStats.deleted_remote || 0) > 0
                      );

                      if (!hasChanges) {
                        return (
                          <div className="flex items-center gap-2">
                            <Info className="h-4 w-4 text-blue-500" />
                            <Badge variant="secondary" className="bg-blue-100 text-blue-700 hover:bg-blue-100">
                              No changes detected
                            </Badge>
                          </div>
                        );
                      }

                      // Render per-calendar stats
                      return (
                        <div className="space-y-4">
                          {Object.entries(perCalendarStats).map(([calendarPair, calStats]: [string, any]) => {
                            const hasCalendarChanges =
                              (calStats.created_local || 0) + (calStats.created_remote || 0) +
                              (calStats.updated_local || 0) + (calStats.updated_remote || 0) +
                              (calStats.deleted_local || 0) + (calStats.deleted_remote || 0) > 0;

                            if (!hasCalendarChanges) return null;

                            return (
                              <div key={calendarPair} className="space-y-2">
                                <div className="text-sm font-medium text-muted-foreground">{calendarPair}</div>
                                <div className="flex flex-wrap gap-2">
                                  {(calStats.created_local || 0) > 0 && (
                                    <BadgeWithTooltip
                                      className="bg-green-100 text-green-700 hover:bg-green-100 gap-1.5"
                                      items={calStats.created_local_items}
                                    >
                                      <Plus className="h-3 w-3" />
                                      New in Apple Reminders: {calStats.created_local}
                                    </BadgeWithTooltip>
                                  )}
                                  {(calStats.created_remote || 0) > 0 && (
                                    <BadgeWithTooltip
                                      className="bg-green-100 text-green-700 hover:bg-green-100 gap-1.5"
                                      items={calStats.created_remote_items}
                                    >
                                      <Plus className="h-3 w-3" />
                                      New in CalDAV: {calStats.created_remote}
                                    </BadgeWithTooltip>
                                  )}
                                  {(calStats.updated_local || 0) > 0 && (
                                    <BadgeWithTooltip
                                      className="bg-yellow-100 text-yellow-700 hover:bg-yellow-100 gap-1.5"
                                      items={calStats.updated_local_items}
                                    >
                                      <ArrowDown className="h-3 w-3" />
                                      Updated in Apple Reminders: {calStats.updated_local}
                                    </BadgeWithTooltip>
                                  )}
                                  {(calStats.updated_remote || 0) > 0 && (
                                    <BadgeWithTooltip
                                      className="bg-yellow-100 text-yellow-700 hover:bg-yellow-100 gap-1.5"
                                      items={calStats.updated_remote_items}
                                    >
                                      <ArrowUp className="h-3 w-3" />
                                      Updated in CalDAV: {calStats.updated_remote}
                                    </BadgeWithTooltip>
                                  )}
                                  {(calStats.deleted_local || 0) > 0 && (
                                    <BadgeWithTooltip
                                      className="bg-red-100 text-red-700 hover:bg-red-100 gap-1.5"
                                      items={calStats.deleted_local_items}
                                    >
                                      <Trash2 className="h-3 w-3" />
                                      Deleted from Apple Reminders: {calStats.deleted_local}
                                    </BadgeWithTooltip>
                                  )}
                                  {(calStats.deleted_remote || 0) > 0 && (
                                    <BadgeWithTooltip
                                      className="bg-red-100 text-red-700 hover:bg-red-100 gap-1.5"
                                      items={calStats.deleted_remote_items}
                                    >
                                      <Trash2 className="h-3 w-3" />
                                      Deleted from CalDAV: {calStats.deleted_remote}
                                    </BadgeWithTooltip>
                                  )}
                                  {(calStats.unchanged || 0) > 0 && (
                                    <Badge variant="secondary" className="gap-1.5">
                                      <Info className="h-3 w-3" />
                                      Unchanged: {calStats.unchanged}
                                    </Badge>
                                  )}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      );
                    }

                    // Manual mode or old format - check if all numeric values are zero
                    const allZero = Object.values(stats).every((val) =>
                      typeof val === 'number' ? val === 0 : true
                    );

                    if (allZero) {
                      return (
                        <div className="flex items-center gap-2">
                          <Info className="h-4 w-4 text-blue-500" />
                          <Badge variant="secondary" className="bg-blue-100 text-blue-700 hover:bg-blue-100">
                            No changes detected
                          </Badge>
                        </div>
                      );
                    }

                    // Render badges for non-zero values (manual mode)
                    return (
                      <div className="flex flex-wrap gap-2">
                        {/* Created/New items */}
                        {(stats.created_local || 0) > 0 && (
                          <BadgeWithTooltip
                            className="bg-green-100 text-green-700 hover:bg-green-100 gap-1.5"
                            items={stats.created_local_items}
                          >
                            <Plus className="h-3 w-3" />
                            New in Apple Reminders: {stats.created_local}
                          </BadgeWithTooltip>
                        )}
                        {(stats.created_remote || 0) > 0 && (
                          <BadgeWithTooltip
                            className="bg-green-100 text-green-700 hover:bg-green-100 gap-1.5"
                            items={stats.created_remote_items}
                          >
                            <Plus className="h-3 w-3" />
                            New in CalDAV: {stats.created_remote}
                          </BadgeWithTooltip>
                        )}
                        {(stats.total_created || 0) > 0 && (
                          <Badge className="bg-green-100 text-green-700 hover:bg-green-100 gap-1.5">
                            <Plus className="h-3 w-3" />
                            Created: {stats.total_created}
                          </Badge>
                        )}

                        {/* Updated items */}
                        {(stats.updated_local || 0) > 0 && (
                          <BadgeWithTooltip
                            className="bg-yellow-100 text-yellow-700 hover:bg-yellow-100 gap-1.5"
                            items={stats.updated_local_items}
                          >
                            <ArrowDown className="h-3 w-3" />
                            Updated in Apple Reminders: {stats.updated_local}
                          </BadgeWithTooltip>
                        )}
                        {(stats.updated_remote || 0) > 0 && (
                          <BadgeWithTooltip
                            className="bg-yellow-100 text-yellow-700 hover:bg-yellow-100 gap-1.5"
                            items={stats.updated_remote_items}
                          >
                            <ArrowUp className="h-3 w-3" />
                            Updated in CalDAV: {stats.updated_remote}
                          </BadgeWithTooltip>
                        )}
                        {(stats.total_updated || 0) > 0 && (
                          <Badge className="bg-yellow-100 text-yellow-700 hover:bg-yellow-100 gap-1.5">
                            <ArrowDown className="h-3 w-3" />
                            Updated: {stats.total_updated}
                          </Badge>
                        )}

                        {/* Deleted items */}
                        {(stats.deleted_local || 0) > 0 && (
                          <BadgeWithTooltip
                            className="bg-red-100 text-red-700 hover:bg-red-100 gap-1.5"
                            items={stats.deleted_local_items}
                          >
                            <Trash2 className="h-3 w-3" />
                            Deleted from Apple Reminders: {stats.deleted_local}
                          </BadgeWithTooltip>
                        )}
                        {(stats.deleted_remote || 0) > 0 && (
                          <BadgeWithTooltip
                            className="bg-red-100 text-red-700 hover:bg-red-100 gap-1.5"
                            items={stats.deleted_remote_items}
                          >
                            <Trash2 className="h-3 w-3" />
                            Deleted from CalDAV: {stats.deleted_remote}
                          </BadgeWithTooltip>
                        )}
                        {(stats.total_deleted || 0) > 0 && (
                          <Badge className="bg-red-100 text-red-700 hover:bg-red-100 gap-1.5">
                            <Trash2 className="h-3 w-3" />
                            Deleted: {stats.total_deleted}
                          </Badge>
                        )}

                        {/* Would delete items (dry run) */}
                        {(stats.would_delete_local || 0) > 0 && (
                          <BadgeWithTooltip
                            className="bg-red-100 text-red-700 hover:bg-red-100 gap-1.5"
                            items={stats.would_delete_local_items}
                          >
                            <Trash2 className="h-3 w-3" />
                            Would delete from Apple Reminders: {stats.would_delete_local}
                          </BadgeWithTooltip>
                        )}
                        {(stats.would_delete_remote || 0) > 0 && (
                          <BadgeWithTooltip
                            className="bg-red-100 text-red-700 hover:bg-red-100 gap-1.5"
                            items={stats.would_delete_remote_items}
                          >
                            <Trash2 className="h-3 w-3" />
                            Would delete from CalDAV: {stats.would_delete_remote}
                          </BadgeWithTooltip>
                        )}

                        {/* Unchanged count - only show if there are changes */}
                        {(stats.unchanged || 0) > 0 && (
                          <Badge variant="secondary" className="gap-1.5">
                            <Info className="h-3 w-3" />
                            Unchanged: {stats.unchanged}
                          </Badge>
                        )}
                      </div>
                    );
                  })()}
                  <Button
                    onClick={() => setSimulationResult(null)}
                    variant="outline"
                    size="sm"
                  >
                    Dismiss
                  </Button>
                </div>
                </TooltipProvider>
              </CardContent>
            </Card>
          )}

          {/* Calendar Mappings */}
          <Card>
            <CardHeader>
              <CardTitle>Calendar Mappings</CardTitle>
              <CardDescription>
                {mode === 'auto'
                  ? 'View automatic calendar mappings (read-only)'
                  : 'Configure which Apple Reminders lists and CalDAV calendars sync together'}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {mode === 'auto' ? (
                <Collapsible open={showMappings} onOpenChange={setShowMappings}>
                  <CollapsibleTrigger asChild>
                    <Button variant="outline" className="w-full flex justify-between items-center">
                      <span>View Automatic Mappings</span>
                      <ChevronDown className={`h-4 w-4 transition-transform ${showMappings ? 'rotate-180' : ''}`} />
                    </Button>
                  </CollapsibleTrigger>
                  <CollapsibleContent className="pt-4">
                    {loading ? (
                      <div className="text-center text-muted-foreground">Loading...</div>
                    ) : lists.length === 0 ? (
                      <div className="text-center text-muted-foreground">No lists found</div>
                    ) : (
                      <div className="overflow-x-auto">
                        <table className="w-full">
                          <thead>
                            <tr className="border-b">
                              <th className="text-left p-3 font-medium">Apple Reminders List</th>
                              <th className="text-left p-3 font-medium">CalDAV Calendar</th>
                            </tr>
                          </thead>
                          <tbody>
                            {lists.map((list) => {
                              const caldavName = list.name === 'Reminders' ? 'tasks' : list.name;
                              return (
                                <tr key={list.name} className="border-b">
                                  <td className="p-3">
                                    <div className="flex items-center gap-2">
                                      <Calendar className="w-4 h-4 text-muted-foreground" />
                                      <span className="font-medium">{list.name}</span>
                                      <Badge variant="outline" className="text-xs">
                                        {list.reminder_count} reminders
                                      </Badge>
                                    </div>
                                  </td>
                                  <td className="p-3">
                                    <span className="text-muted-foreground">{caldavName}</span>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </CollapsibleContent>
                </Collapsible>
              ) : (
                <div className="space-y-4">
                  {loading ? (
                    <div className="text-center text-muted-foreground">Loading...</div>
                  ) : lists.length === 0 && getUnmappedCaldavCalendars().length === 0 ? (
                    <div className="text-center text-muted-foreground">No lists found</div>
                  ) : (
                    <>
                      <div className="overflow-x-auto">
                        <table className="w-full">
                          <thead>
                            <tr className="border-b">
                              <th className="text-left p-3 font-medium">Source</th>
                              <th className="text-left p-3 font-medium">Destination</th>
                            </tr>
                          </thead>
                          <tbody>
                            {/* Apple Lists → CalDAV Calendars */}
                            {lists.map((list) => {
                              const currentMapping = listMappings[list.name] || '';
                              const isNewCalendar = currentMapping && !caldavCalendars.includes(currentMapping);

                              return (
                                <tr key={`apple-${list.name}`} className="border-b">
                                  <td className="p-3">
                                    <div className="flex items-center gap-2">
                                      <Calendar className="w-4 h-4 text-muted-foreground" />
                                      <span className="font-medium">{list.name}</span>
                                      <Badge variant="outline" className="text-xs">
                                        {list.reminder_count} reminders
                                      </Badge>
                                      <Badge variant="secondary" className="text-xs">
                                        Apple
                                      </Badge>
                                    </div>
                                  </td>
                                  <td className="p-3">
                                    <div className="space-y-1">
                                      <Autocomplete
                                        options={getCalendarOptions()}
                                        value={currentMapping}
                                        onValueChange={(value) => {
                                          setListMappings({
                                            ...listMappings,
                                            [list.name]: value,
                                          });
                                        }}
                                        placeholder="Select or type calendar name..."
                                        emptyText="No calendars found. Type to create new."
                                        searchPlaceholder="Search or create calendar..."
                                        allowCustom={true}
                                      />
                                      {isNewCalendar && (
                                        <p className="text-xs text-amber-600 dark:text-amber-400">
                                          ⚠️ Calendar "{currentMapping}" will be created on CalDAV
                                        </p>
                                      )}
                                    </div>
                                  </td>
                                </tr>
                              );
                            })}

                            {/* Unmapped CalDAV Calendars → Create Apple Lists */}
                            {getUnmappedCaldavCalendars().map((calendar) => {
                              const currentMapping = Object.keys(listMappings).find(
                                key => listMappings[key] === calendar
                              ) || '';
                              const isNewList = currentMapping && !lists.find(l => l.name === currentMapping);

                              return (
                                <tr key={`caldav-${calendar}`} className="border-b bg-muted/30">
                                  <td className="p-3">
                                    <div className="flex items-center gap-2">
                                      <Calendar className="w-4 h-4 text-muted-foreground" />
                                      <span className="font-medium">{calendar}</span>
                                      <Badge variant="secondary" className="text-xs">
                                        CalDAV
                                      </Badge>
                                    </div>
                                  </td>
                                  <td className="p-3">
                                    <div className="space-y-1">
                                      <Autocomplete
                                        options={lists.map(l => ({ value: l.name, label: l.name }))}
                                        value={currentMapping}
                                        onValueChange={(value) => {
                                          // Remove any existing mapping to this calendar
                                          const newMappings = { ...listMappings };
                                          Object.keys(newMappings).forEach(key => {
                                            if (newMappings[key] === calendar) {
                                              delete newMappings[key];
                                            }
                                          });
                                          // Add new mapping
                                          if (value) {
                                            newMappings[value] = calendar;
                                          }
                                          setListMappings(newMappings);
                                        }}
                                        placeholder="Select or type list name..."
                                        emptyText="No lists found. Type to create new."
                                        searchPlaceholder="Search or create Apple list..."
                                        allowCustom={true}
                                      />
                                      {isNewList && (
                                        <p className="text-xs text-amber-600 dark:text-amber-400">
                                          ⚠️ Apple Reminders list "{currentMapping}" will be created
                                        </p>
                                      )}
                                    </div>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                      <Button onClick={handleSaveMappings} disabled={loading}>
                        <Save className="w-4 h-4 mr-2" />
                        Save Mappings
                      </Button>
                    </>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Sync History */}
          <Card>
            <CardHeader>
              <CardTitle>Sync History</CardTitle>
              <CardDescription>Recent sync operations</CardDescription>
            </CardHeader>
            <CardContent>
              {history.length === 0 ? (
                <div className="text-center text-muted-foreground">No sync history</div>
              ) : (
                <div className="space-y-3">
                  {history.map((log) => (
                    <div key={log.id} className="border-l-2 pl-4 py-2 space-y-1" style={{
                      borderColor: log.status === 'completed' ? 'rgb(34 197 94)' :
                                   log.status === 'failed' ? 'rgb(239 68 68)' :
                                   'rgb(59 130 246)'
                    }}>
                      <div className="flex items-center justify-between">
                        <span className="font-medium">{log.operation}</span>
                        <Badge variant={log.status === 'completed' ? 'success' : log.status === 'failed' ? 'destructive' : 'default'}>
                          {log.status}
                        </Badge>
                      </div>
                      <p className="text-sm text-muted-foreground">{log.message}</p>
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>{formatDate(log.started_at)}</span>
                        {log.duration_seconds && (
                          <span>{log.duration_seconds}s</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Credentials panel at bottom (collapsed) when configured */}
          <Collapsible open={showCredentialsPanel} onOpenChange={setShowCredentialsPanel}>
            <CollapsibleTrigger asChild>
              <Button variant="outline" className="w-full flex justify-between items-center">
                <span>CalDAV Credentials</span>
                <ChevronDown className={`h-4 w-4 transition-transform ${showCredentialsPanel ? 'rotate-180' : ''}`} />
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent className="pt-4">
              {renderCredentialsPanel()}
            </CollapsibleContent>
          </Collapsible>
        </>
      )}
    </div>
  );
}

import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { RefreshCw, Activity, Calendar, Key, FileText, AlertTriangle, ExternalLink, Image } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import type { BadgeProps } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { useAppStore } from '@/store/app-store';
import { useSyncStore } from '@/store/sync-store';
import apiClient from '@/lib/api-client';
import type { ServiceStatus, SetupVerificationResponse } from '@/types/api';

export default function Dashboard() {
  const { status, setStatus, wsConnected, config, configLoaded, setConfig } = useAppStore();
  const { activeSyncs, logs } = useSyncStore();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [verification, setVerification] = useState<SetupVerificationResponse | null>(null);
  const [showSetupWarning, setShowSetupWarning] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiClient.status();
      setStatus(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load status');
    } finally {
      setLoading(false);
    }
  }, [setStatus]);

  const lastSyncTimestamp = useCallback((service?: ServiceStatus) => {
    if (!service?.last_sync) return null;
    if (typeof service.last_sync === 'string') {
      return service.last_sync;
    }
    return service.last_sync.started_at ?? null;
  }, []);

  const lastSyncMessage = useCallback((service?: ServiceStatus) => {
    if (!service?.last_sync || typeof service.last_sync === 'string') {
      return null;
    }
    const message = service.last_sync.message || null;
    if (!message) {
      return null;
    }
    if (service === status?.reminders && /Synced 0 calendar\(s\)/i.test(message)) {
      return message.replace(/Synced 0 calendar\(s\)/i, 'No changes detected');
    }
    return message;
  }, [status?.reminders]);

  const extractPendingCount = (service?: ServiceStatus) => {
    if (!service?.last_sync || typeof service.last_sync === 'string') {
      return 0;
    }
    const stats = service.last_sync.stats as { pending_local_notes?: unknown[] } | undefined;
    const pending = stats?.pending_local_notes;
    return Array.isArray(pending) ? pending.length : 0;
  };

  const notesEnabled = useMemo(() => {
    if (configLoaded && typeof config?.notes_enabled === 'boolean') {
      return config.notes_enabled;
    }
    if (typeof status?.notes?.enabled === 'boolean') {
      return status.notes.enabled;
    }
    return true;
  }, [configLoaded, config?.notes_enabled, status?.notes?.enabled]);

  const loadVerification = useCallback(async () => {
    if (!notesEnabled) {
      setVerification(null);
      setShowSetupWarning(false);
      return;
    }

    try {
      const result = await apiClient.verifySetup();
      setVerification(result);
      if (!result.all_ready) {
        setShowSetupWarning(true);
      } else {
        setShowSetupWarning(false);
      }
    } catch (err) {
      console.error('Failed to load verification:', err);
    }
  }, [notesEnabled]);

  useEffect(() => {
    if (configLoaded) {
      return;
    }

    let isMounted = true;
    (async () => {
      try {
        const cfg = await apiClient.getConfig();
        if (isMounted) {
          setConfig(cfg);
        }
      } catch (err) {
        console.error('Failed to load configuration:', err);
      }
    })();

    return () => {
      isMounted = false;
    };
  }, [configLoaded, setConfig]);

  useEffect(() => {
    loadStatus();
    const interval = setInterval(loadStatus, 30000);
    return () => clearInterval(interval);
  }, [loadStatus]);

  const hadActiveSyncRef = useRef(false);
  useEffect(() => {
    const isSyncing = activeSyncs.size > 0;
    if (!isSyncing && hadActiveSyncRef.current) {
      loadStatus();
    }
    hadActiveSyncRef.current = isSyncing;
  }, [activeSyncs, loadStatus]);

  useEffect(() => {
    loadVerification();
  }, [loadVerification]);

  const getStatusBadge = (serviceStatus: string) => {
    const statusMap: Record<string, { variant: BadgeProps['variant']; label: string }> = {
      idle: { variant: 'secondary', label: 'Idle' },
      running: { variant: 'default', label: 'Running' },
      success: { variant: 'success', label: 'Success' },
      completed: { variant: 'success', label: 'Success' },
      partial_success: { variant: 'warning', label: 'Partial Success' },
      failed: { variant: 'destructive', label: 'Failed' },
      error: { variant: 'destructive', label: 'Error' },
    };
    const config = statusMap[serviceStatus] || statusMap.idle;
    return <Badge variant={config.variant}>{config.label}</Badge>;
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return 'Never';
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffMins < 1440) return `${Math.floor(diffMins / 60)}h ago`;
    return date.toLocaleDateString();
  };

  if (loading && !status) {
    return (
      <div className="flex items-center justify-center h-full">
        <RefreshCw className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const notesPendingCount = extractPendingCount(status?.notes);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Dashboard</h1>
          <p className="text-muted-foreground">
            Overview of your iCloudBridge sync services
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={wsConnected ? 'success' : 'destructive'}>
            {wsConnected ? 'Connected' : 'Disconnected'}
          </Badge>
          <Button onClick={loadStatus} variant="outline" size="sm">
            <RefreshCw className="w-4 h-4 mr-2" />
            Refresh
          </Button>
        </div>
      </div>

      {/* Error Alert */}
      {error && (
        <Alert variant="destructive">
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Setup Verification Warning */}
      {showSetupWarning && verification && notesEnabled && (
        <Alert variant="warning" className="border-orange-500 bg-orange-50">
          <AlertTriangle className="h-4 w-4" />
          <div>
            <AlertTitle>Notes Setup Incomplete</AlertTitle>
            <AlertDescription>
              <p className="mb-2">
                Your Notes sync setup is incomplete. The following items need attention:
              </p>
              <ul className="list-disc list-inside space-y-1 mb-3">
                {verification.shortcuts.some(s => !s.installed) && (
                  <li>
                    {verification.shortcuts.filter(s => !s.installed).length} of {verification.shortcuts.length} required shortcuts not installed
                  </li>
                )}
                {!verification.full_disk_access.has_access && (
                  <li>Python does not have Full Disk Access</li>
                )}
                {verification.notes_folder.path && !verification.notes_folder.exists && (
                  <li>Notes folder does not exist: {verification.notes_folder.path}</li>
                )}
                {verification.notes_folder.exists && !verification.notes_folder.writable && (
                  <li>Notes folder is not writable</li>
                )}
              </ul>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  asChild
                  className="bg-white text-black hover:text-black dark:bg-white dark:text-black dark:hover:text-black"
                >
                  <Link to="/settings">
                    <ExternalLink className="w-4 h-4 mr-2" />
                    Go to Settings
                  </Link>
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setShowSetupWarning(false)}
                >
                  Dismiss
                </Button>
              </div>
            </AlertDescription>
          </div>
        </Alert>
      )}

      {/* Service Status Cards */}
      <div className="grid gap-4 md:grid-cols-4">
        {/* Notes */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Notes</CardTitle>
            <FileText className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="flex items-center justify-between mb-2">
              {status?.notes && getStatusBadge(status.notes.status ?? 'idle')}
              <Badge variant={status?.notes?.enabled ? 'outline' : 'secondary'}>
                {status?.notes?.enabled ? 'Enabled' : 'Disabled'}
              </Badge>
            </div>
            {activeSyncs.has('notes') ? (
              <div className="text-xs text-blue-600 dark:text-blue-400 flex items-center gap-2">
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                <span>Syncing... {activeSyncs.get('notes')?.message}</span>
              </div>
            ) : (
              <div className="text-xs text-muted-foreground space-y-1">
                <div>Last sync: {formatDate(lastSyncTimestamp(status?.notes) ?? null)}</div>
                {lastSyncMessage(status?.notes) && (
                  <div className="line-clamp-2">
                    {lastSyncMessage(status?.notes)}
                  </div>
                )}
                {status?.notes?.next_sync && (
                  <div>Next sync: {formatDate(status.notes.next_sync)}</div>
                )}
                {notesPendingCount > 0 && (
                  <div className="flex items-center gap-1 text-amber-600">
                    <AlertTriangle className="h-3.5 w-3.5" />
                    <span>{notesPendingCount} note{notesPendingCount === 1 ? '' : 's'} waiting for Apple Notes edits</span>
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Reminders */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Reminders</CardTitle>
            <Calendar className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="flex items-center justify-between mb-2">
              {status?.reminders && getStatusBadge(status.reminders.status ?? 'idle')}
              <Badge variant={status?.reminders?.enabled ? 'outline' : 'secondary'}>
                {status?.reminders?.enabled ? 'Enabled' : 'Disabled'}
              </Badge>
            </div>
            {activeSyncs.has('reminders') ? (
              <div className="text-xs text-blue-600 dark:text-blue-400 flex items-center gap-2">
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                <span>Syncing... {activeSyncs.get('reminders')?.message}</span>
              </div>
            ) : (
              <div className="text-xs text-muted-foreground space-y-1">
                <div>Last sync: {formatDate(lastSyncTimestamp(status?.reminders) ?? null)}</div>
                {lastSyncMessage(status?.reminders) && (
                  <div className="line-clamp-2">{lastSyncMessage(status?.reminders)}</div>
                )}
                {status?.reminders?.next_sync && (
                  <div>Next sync: {formatDate(status.reminders.next_sync)}</div>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Passwords */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Passwords</CardTitle>
            <Key className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="flex items-center justify-between mb-2">
              {status?.passwords && getStatusBadge(status.passwords.status ?? 'idle')}
              <Badge variant={status?.passwords?.enabled ? 'outline' : 'secondary'}>
                {status?.passwords?.enabled ? 'Enabled' : 'Disabled'}
              </Badge>
            </div>
            {activeSyncs.has('passwords') ? (
              <div className="text-xs text-blue-600 dark:text-blue-400 flex items-center gap-2">
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                <span>Syncing... {activeSyncs.get('passwords')?.message}</span>
              </div>
            ) : (
              <div className="text-xs text-muted-foreground space-y-1">
                <div>Last sync: {formatDate(lastSyncTimestamp(status?.passwords) ?? null)}</div>
                {lastSyncMessage(status?.passwords) && (
                  <div className="line-clamp-2">{lastSyncMessage(status?.passwords)}</div>
                )}
                {status?.passwords?.next_sync && (
                  <div>Next sync: {formatDate(status.passwords.next_sync)}</div>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Photos */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Photos</CardTitle>
            <Image className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="flex items-center justify-between mb-2">
              {status?.photos && getStatusBadge(status.photos.status ?? 'idle')}
              <Badge variant={status?.photos?.enabled ? 'outline' : 'secondary'}>
                {status?.photos?.enabled === undefined
                  ? 'Unknown'
                  : status.photos.enabled
                    ? 'Enabled'
                    : 'Disabled'}
              </Badge>
            </div>
            {activeSyncs.has('photos') ? (
              <div className="text-xs text-blue-600 dark:text-blue-400 flex items-center gap-2">
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                <span>Syncing... {activeSyncs.get('photos')?.message}</span>
              </div>
            ) : (
              <div className="text-xs text-muted-foreground space-y-1">
                <div>Last sync: {formatDate(lastSyncTimestamp(status?.photos) ?? null)}</div>
                {lastSyncMessage(status?.photos) && (
                  <div className="line-clamp-2">{lastSyncMessage(status?.photos)}</div>
                )}
                {status?.photos?.next_sync && (
                  <div>Next sync: {formatDate(status?.photos?.next_sync ?? null)}</div>
                )}
                {typeof status?.photos?.pending === 'number' && (
                  status.photos.pending > 0 ? (
                    <div>Pending imports: {status.photos.pending}</div>
                  ) : (
                    <div>No changes detected</div>
                  )
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Scheduler Status */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="w-5 h-5" />
            Scheduler Status
          </CardTitle>
          <CardDescription>
            Background scheduler for automated syncs
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-between">
            <div>
              <Badge variant={status?.scheduler_running ? 'success' : 'destructive'}>
                {status?.scheduler_running ? 'Running' : 'Stopped'}
              </Badge>
              <p className="text-sm text-muted-foreground mt-2">
                {status?.active_schedules || 0} active schedule(s)
              </p>
            </div>
            <Button variant="outline" onClick={() => window.location.href = '/schedules'}>
              View Schedules
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Recent Activity */}
      {logs.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Recent Activity</CardTitle>
            <CardDescription>Latest sync operations and logs</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {logs.slice(-5).reverse().map((log, idx) => (
                <div
                  key={idx}
                  className="flex items-start gap-2 text-sm border-l-2 pl-3 py-1"
                  style={{
                    borderColor:
                      log.level === 'ERROR'
                        ? 'rgb(239 68 68)'
                        : log.level === 'WARNING'
                        ? 'rgb(234 179 8)'
                        : 'rgb(59 130 246)',
                  }}
                >
                  <Badge variant="outline" className="text-xs">
                    {log.service}
                  </Badge>
                  <span className="text-muted-foreground flex-1">{log.message}</span>
                  <span className="text-xs text-muted-foreground">
                    {formatDate(log.timestamp)}
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

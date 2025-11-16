import { useCallback, useEffect, useRef, useState } from 'react';
import { Image, RefreshCw, PlayCircle, Activity, Info } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import apiClient from '@/lib/api-client';
import { useSyncStore } from '@/store/sync-store';
import type { AppConfig, SyncLog } from '@/types/api';
import ServiceDisabledNotice from '@/components/ServiceDisabledNotice';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

interface PhotosStatus {
  enabled: boolean;
  message?: string;
  total_imported?: number;
  pending?: number;
  last_sync?: string;
  sources?: string[];
  skipped_existing?: number;
}

interface PhotosSyncResult {
  message: string;
  stats: {
    discovered: number;
    new_assets: number;
    imported: number;
    dry_run: boolean;
    albums?: Record<string, number>;
    pending?: string[];
    sources?: string[];
    skipped_existing?: number;
  };
}

function SyncStatsView({ result }: { result: PhotosSyncResult | null }) {
  if (!result) {
    return null;
  }

  const { stats } = result;
  return (
    <div className="space-y-4 rounded-md border bg-card/40 p-4">
      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>{stats.dry_run ? 'Simulation results' : 'Sync results'}</span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
        <div>
          <p className="text-muted-foreground">Discovered</p>
          <p className="font-medium">{stats.discovered ?? 0}</p>
        </div>
        <div>
          <p className="text-muted-foreground">New Assets</p>
          <p className="font-medium">{stats.new_assets ?? 0}</p>
        </div>
        <div>
          <p className="text-muted-foreground">Imported</p>
          <p className="font-medium">{stats.imported ?? 0}</p>
        </div>
        <div>
          <p className="text-muted-foreground">Skipped</p>
          <p className="font-medium">{stats.skipped_existing ?? 0}</p>
        </div>
      </div>

      {stats.albums && Object.keys(stats.albums).length > 0 && (
        <div className="space-y-2 border-t pt-3">
          <div className="text-sm font-semibold">Albums</div>
          <div className="grid grid-cols-2 gap-2 text-sm">
            {Object.entries(stats.albums).map(([album, count]) => (
              <div key={album} className="flex justify-between">
                <span className="text-muted-foreground">{album}</span>
                <span className="font-medium">{count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {stats.pending && stats.pending.length > 0 && (
        <div className="space-y-2 border-t pt-3">
          <div className="text-sm font-semibold">
            Pending imports (showing first {Math.min(stats.pending.length, 10)})
          </div>
          <ul className="text-xs text-muted-foreground list-disc list-inside max-h-40 overflow-y-auto">
            {stats.pending.slice(0, 10).map((path, idx) => (
              <li key={idx} className="truncate">{path}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function Photos() {
  const [history, setHistory] = useState<SyncLog[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [status, setStatus] = useState<PhotosStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [syncResult, setSyncResult] = useState<PhotosSyncResult | null>(null);
  const [syncLoading, setSyncLoading] = useState(false);

  const { activeSyncs } = useSyncStore();
  const activeSync = activeSyncs.get('photos');

  const loadConfig = useCallback(async () => {
    try {
      const cfg = await apiClient.getConfig();
      setConfig(cfg);
    } catch (err) {
      console.error('Failed to load config:', err);
    }
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const statusData = await apiClient.getPhotosStatus();
      setStatus(statusData);
    } catch (err) {
      console.error('Failed to load status:', err);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      setHistoryLoading(true);
      const historyData = await apiClient.getPhotosHistory(10);
      setHistory(historyData.logs);
    } catch (err) {
      console.error(err);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    loadHistory();
    loadConfig();
    loadStatus();
  }, [loadHistory, loadConfig, loadStatus]);

  const wasSyncingRef = useRef(false);
  useEffect(() => {
    const isSyncing = Boolean(activeSync);
    if (!isSyncing && wasSyncingRef.current) {
      loadHistory();
      loadStatus();
    }
    wasSyncingRef.current = isSyncing;
  }, [activeSync, loadHistory, loadStatus]);

  const handleSyncAction = async (dryRun: boolean) => {
    try {
      setSyncLoading(true);
      setError(null);
      setSuccess(null);
      const response = await apiClient.syncPhotos(undefined, dryRun);
      setSyncResult(response);
      if (dryRun) {
        setSuccess('Simulation complete. No changes were applied.');
      } else {
        setSuccess('Photo sync complete.');
        await loadHistory();
        await loadStatus();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Photo sync failed');
    } finally {
      setSyncLoading(false);
    }
  };

  const handleReset = async () => {
    if (!confirm('Reset photo sync state? This clears all imported photo records.')) {
      return;
    }
    try {
      setHistoryLoading(true);
      await apiClient.resetPhotos();
      setSuccess('Photos sync state reset.');
      await loadHistory();
      await loadStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Reset failed');
    } finally {
      setHistoryLoading(false);
    }
  };

  const formatDate = (dateValue: string | number) => {
    // Handle both Unix timestamps (numbers) and ISO strings
    const date = typeof dateValue === 'number'
      ? new Date(dateValue * 1000) // Convert Unix timestamp to milliseconds
      : new Date(dateValue);
    return date.toLocaleString();
  };

  const serviceDisabled = config?.photos_enabled === false;

  if (serviceDisabled) {
    return <ServiceDisabledNotice serviceName="Photos" />;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <Image className="w-8 h-8" />
            Photos Sync
          </h1>
          <p className="text-muted-foreground">
            Import photos and videos from local folders to Apple Photos
          </p>
        </div>
        <Button onClick={loadHistory} variant="outline" disabled={historyLoading}>
          <RefreshCw className={`w-4 h-4 mr-2 ${historyLoading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

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

      {status && status.enabled && (
        <div className="rounded-lg border p-4 space-y-3">
          <h3 className="text-lg font-semibold">Status</h3>
          <TooltipProvider>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-sm">
              <div>
                <p className="text-muted-foreground">Total Imported</p>
                <p className="font-medium">{status.total_imported ?? 0}</p>
              </div>
              <div>
                <p className="text-muted-foreground">Pending</p>
                <p className="font-medium">{status.pending ?? 0}</p>
              </div>
              <div>
                <p className="text-muted-foreground">Last Sync</p>
                <p className="font-medium text-xs">
                  {status.last_sync ? new Date(status.last_sync).toLocaleString() : 'Never'}
                </p>
              </div>
              <div>
                <p className="text-muted-foreground">Sources</p>
                <p className="font-medium">{status.sources?.length ?? 0}</p>
              </div>
              <div>
                <p className="text-muted-foreground flex items-center gap-1">
                  Skipped
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Info className="w-3.5 h-3.5 text-muted-foreground" />
                    </TooltipTrigger>
                    <TooltipContent>
                      Photos are skipped when they are already in your Apple Photos library.
                    </TooltipContent>
                  </Tooltip>
                </p>
                <p className="font-medium">{status.skipped_existing ?? 0}</p>
              </div>
            </div>
          </TooltipProvider>
          {status.sources && status.sources.length > 0 && (
            <div className="flex gap-2 flex-wrap">
              {status.sources.map((source) => (
                <Badge key={source} variant="outline">{source}</Badge>
              ))}
            </div>
          )}
        </div>
      )}

      {activeSync && (
        <div className="rounded-lg border bg-card/60 p-4 space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Activity className="w-4 h-4" /> Sync in progress
          </div>
          <div>
            <div className="flex justify-between text-sm mb-2">
              <span>{activeSync.message}</span>
              <span>{activeSync.progress}%</span>
            </div>
            <Progress value={activeSync.progress} />
          </div>
        </div>
      )}

      <section className="rounded-lg border p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold">Sync Photos</h2>
            <p className="text-sm text-muted-foreground">
              Scan configured source folders and import new photos to Apple Photos.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap gap-3">
          <Button
            variant="outline"
            disabled={syncLoading}
            onClick={() => handleSyncAction(true)}
            className="flex-1 min-w-[120px]"
          >
            {syncLoading ? (
              <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
            ) : (
              <PlayCircle className="w-4 h-4 mr-2" />
            )}
            Simulate
          </Button>
          <Button
            disabled={syncLoading}
            onClick={() => handleSyncAction(false)}
            className="flex-1 min-w-[120px]"
          >
            {syncLoading ? (
              <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4 mr-2" />
            )}
            Sync
          </Button>
        </div>

        <SyncStatsView result={syncResult} />
      </section>

      <section className="rounded-lg border p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-lg font-semibold flex items-center gap-2">
              Sync history
              {(historyLoading || activeSync) && (
                <RefreshCw className="w-4 h-4 animate-spin text-muted-foreground" />
              )}
            </h3>
            <p className="text-sm text-muted-foreground">Auto-updates after each sync (last 10)</p>
          </div>
          <Button variant="ghost" size="sm" onClick={handleReset}>
            Reset state
          </Button>
        </div>
        {history.length === 0 ? (
          <p className="text-sm text-muted-foreground">No sync history available.</p>
        ) : (
          <div className="space-y-4">
            {history.map((log) => {
              const stats = (log.stats || {}) as PhotosSyncResult['stats'] & { sources?: string[]; pending?: string[] };
              const albumEntries = stats?.albums ? Object.entries(stats.albums) : [];
              const sources = Array.isArray(stats?.sources) ? stats.sources : [];
              const pending = Array.isArray(stats?.pending) ? stats.pending : [];
              return (
                <div key={log.id} className="rounded-md border-l-4 bg-card/40 p-4" style={{
                  borderColor:
                    log.status === 'success'
                      ? 'rgb(34 197 94)'
                      : log.status === 'error'
                      ? 'rgb(239 68 68)'
                      : 'rgb(59 130 246)',
                }}>
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="font-medium">Photo Sync</p>
                      <p className="text-xs text-muted-foreground">{formatDate(log.started_at)}</p>
                    </div>
                    <div className="flex gap-2">
                      {log.status === 'success' && typeof stats.imported === 'number' && (
                        stats.imported > 0 ? (
                          <Badge variant="outline" className="bg-green-50 text-green-700 border-green-200">
                            +{stats.imported}
                          </Badge>
                        ) : (
                          <Badge variant="outline" className="text-xs">
                            No new photos detected
                          </Badge>
                        )
                      )}
                      <Badge variant={log.status === 'success' ? 'success' : log.status === 'error' ? 'destructive' : 'default'}>
                        {log.status}
                      </Badge>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs mt-3">
                    {typeof stats.discovered === 'number' && (
                      <div>
                        <span className="text-muted-foreground">Discovered</span>
                        <p className="font-medium">{stats.discovered}</p>
                      </div>
                    )}
                    {typeof stats.new_assets === 'number' && (
                      <div>
                        <span className="text-muted-foreground">New Assets</span>
                        <p className="font-medium">{stats.new_assets}</p>
                      </div>
                    )}
                    {typeof stats.imported === 'number' && (
                      <div>
                        <span className="text-muted-foreground">Imported</span>
                        <p className="font-medium">{stats.imported}</p>
                      </div>
                    )}
                    {typeof stats.skipped_existing === 'number' && (
                      <div>
                        <span className="text-muted-foreground">Skipped</span>
                        <p className="font-medium">{stats.skipped_existing}</p>
                      </div>
                    )}
                    {log.duration_seconds !== null && (
                      <div>
                        <span className="text-muted-foreground">Duration</span>
                        <p className="font-medium">{log.duration_seconds.toFixed(1)}s</p>
                      </div>
                    )}
                  </div>
                  {sources.length > 0 && (
                    <div className="mt-4">
                      <p className="text-xs text-muted-foreground mb-1">Sources scanned</p>
                      <div className="flex flex-wrap gap-1">
                        {sources.map((source) => (
                          <Badge key={`${log.id}-${source}`} variant="outline" className="text-[11px]">
                            {source}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {albumEntries.length > 0 && (
                    <div className="mt-4 space-y-1">
                      <p className="text-xs text-muted-foreground">Album imports</p>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
                        {albumEntries.map(([album, count]) => (
                          <div
                            key={`${log.id}-${album}`}
                            className="flex items-center justify-between rounded border border-muted/40 bg-background/60 px-3 py-1.5"
                          >
                            <span className="truncate pr-2">{album}</span>
                            <span className="font-medium">{count}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {pending.length > 0 && (
                    <div className="mt-4 space-y-1">
                      <p className="text-xs text-muted-foreground">
                        Pending imports (showing first {Math.min(pending.length, 5)})
                      </p>
                      <ul className="text-[11px] text-muted-foreground space-y-0.5 max-h-32 overflow-y-auto border rounded border-dashed px-3 py-2 bg-background/70">
                        {pending.slice(0, 5).map((path, idx) => (
                          <li key={`${log.id}-pending-${idx}`} className="truncate">
                            {path}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {log.error_message && (
                    <p className="text-xs text-destructive mt-2">{log.error_message}</p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}

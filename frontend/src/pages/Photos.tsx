import { useCallback, useEffect, useRef, useState } from 'react';
import { Image, RefreshCw, PlayCircle, Activity, Info, AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import apiClient from '@/lib/api-client';
import { useSyncStore } from '@/store/sync-store';
import type { AppConfig, SyncLog } from '@/types/api';
import ServiceDisabledNotice from '@/components/ServiceDisabledNotice';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

interface PhotosStatus {
  enabled: boolean;
  message?: string;
  library_items?: number;
  last_imported?: number;
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

interface PhotosExportStatus {
  enabled: boolean;
  sync_mode?: string;
  export_mode?: string;
  export_folder?: string;
  organize_by?: string;
  total_exported?: number;
  baseline_date?: string;
  last_export?: string;
  message?: string;
}

// Combined result for mode-aware sync
interface CombinedSyncResult {
  importResult: PhotosSyncResult | null;
  exportResult: PhotosExportResult | null;
}

interface PhotosExportResult {
  message: string;
  stats: {
    total_found?: number;
    exported: number;
    would_export?: number;  // Count for dry runs
    skipped_existing?: number;
    skipped_already_exported?: number;  // API uses this name
    skipped_before_baseline?: number;
    skipped_imported?: number;
    skipped_imported_from_nextcloud?: number;  // API uses this name
    errors: number;
    dry_run: boolean;
    preview?: Array<{ filename: string; dest_path: string; size: number; created: string }>;
    baseline_set?: boolean;
    message?: string;
  };
}

function SyncStatsView({ result }: { result: CombinedSyncResult | null }) {
  if (!result || (!result.importResult && !result.exportResult)) {
    return null;
  }

  const importStats = result.importResult?.stats;
  const exportStats = result.exportResult?.stats;
  const isDryRun = importStats?.dry_run || exportStats?.dry_run;

  return (
    <div className="space-y-4 rounded-md border bg-card/40 p-4">
      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>{isDryRun ? 'Simulation results' : 'Sync results'}</span>
      </div>

      {/* Import Stats */}
      {importStats && (
        <div className="space-y-2">
          <div className="text-sm font-semibold text-primary">Import</div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            <div>
              <p className="text-muted-foreground">Discovered</p>
              <p className="font-medium">{importStats.discovered ?? 0}</p>
            </div>
            <div>
              <p className="text-muted-foreground">New Assets</p>
              <p className="font-medium">{importStats.new_assets ?? 0}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Imported</p>
              <p className="font-medium">{importStats.imported ?? 0}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Skipped</p>
              <p className="font-medium">{importStats.skipped_existing ?? 0}</p>
            </div>
          </div>

          {importStats.albums && Object.keys(importStats.albums).length > 0 && (
            <div className="space-y-2 border-t pt-3">
              <div className="text-sm font-semibold">Albums</div>
              <div className="grid grid-cols-2 gap-2 text-sm">
                {Object.entries(importStats.albums).map(([album, count]) => (
                  <div key={album} className="flex justify-between">
                    <span className="text-muted-foreground">{album}</span>
                    <span className="font-medium">{count}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {importStats.pending && importStats.pending.length > 0 && (
            <div className="space-y-2 border-t pt-3">
              <div className="text-sm font-semibold">
                Pending imports (showing first {Math.min(importStats.pending.length, 10)})
              </div>
              <ul className="text-xs text-muted-foreground list-disc list-inside max-h-40 overflow-y-auto">
                {importStats.pending.slice(0, 10).map((path, idx) => (
                  <li key={idx} className="truncate">{path}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Export Stats */}
      {exportStats && (
        <div className="space-y-2">
          {importStats && <div className="border-t pt-3" />}
          <div className="text-sm font-semibold text-primary">Export</div>

          {/* Show baseline message if just set */}
          {exportStats.baseline_set && exportStats.message && (
            <div className="flex items-start gap-2 p-3 rounded-md bg-blue-50 border border-blue-200 text-sm">
              <Info className="w-4 h-4 text-blue-600 mt-0.5 shrink-0" />
              <span className="text-blue-800">{exportStats.message}</span>
            </div>
          )}

          {!exportStats.baseline_set && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
              <div>
                <p className="text-muted-foreground">
                  {exportStats.dry_run ? 'Would Export' : 'Exported'}
                </p>
                <p className="font-medium">
                  {exportStats.dry_run
                    ? (exportStats.would_export ?? exportStats.exported ?? 0)
                    : (exportStats.exported ?? 0)}
                </p>
              </div>
              <div>
                <p className="text-muted-foreground">Skipped (Existing)</p>
                <p className="font-medium">{exportStats.skipped_existing ?? exportStats.skipped_already_exported ?? 0}</p>
              </div>
              <div>
                <p className="text-muted-foreground">Skipped (Baseline)</p>
                <p className="font-medium">{exportStats.skipped_before_baseline ?? 0}</p>
              </div>
              {(exportStats.errors ?? 0) > 0 && (
                <div>
                  <p className="text-destructive">Errors</p>
                  <p className="font-medium text-destructive">{exportStats.errors}</p>
                </div>
              )}
            </div>
          )}
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

  const [syncResult, setSyncResult] = useState<CombinedSyncResult | null>(null);
  const [syncLoading, setSyncLoading] = useState(false);

  // Export state
  const [exportStatus, setExportStatus] = useState<PhotosExportStatus | null>(null);
  const [_exportHistory, setExportHistory] = useState<SyncLog[]>([]);
  const [showFullLibraryWarning, setShowFullLibraryWarning] = useState(false);

  // Determine sync mode from config
  const syncMode = config?.photos_sync_mode || 'import';

  const { activeSyncs } = useSyncStore();
  const activeSync = activeSyncs.get('photos');
  const activeExport = activeSyncs.get('photos_export');

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

  const loadExportStatus = useCallback(async () => {
    try {
      const exportStatusData = await apiClient.getPhotosExportStatus();
      setExportStatus(exportStatusData);
    } catch (err) {
      console.error('Failed to load export status:', err);
    }
  }, []);

  const loadExportHistory = useCallback(async () => {
    try {
      const exportHistoryData = await apiClient.getPhotosExportHistory(10);
      setExportHistory(exportHistoryData.logs);
    } catch (err) {
      console.error('Failed to load export history:', err);
    }
  }, []);

  useEffect(() => {
    loadHistory();
    loadConfig();
    loadStatus();
    loadExportStatus();
    loadExportHistory();
  }, [loadHistory, loadConfig, loadStatus, loadExportStatus, loadExportHistory]);

  const wasSyncingRef = useRef(false);
  const wasExportingRef = useRef(false);
  useEffect(() => {
    const isSyncing = Boolean(activeSync);
    if (!isSyncing && wasSyncingRef.current) {
      loadHistory();
      loadStatus();
    }
    wasSyncingRef.current = isSyncing;
  }, [activeSync, loadHistory, loadStatus]);

  useEffect(() => {
    const isExporting = Boolean(activeExport);
    if (!isExporting && wasExportingRef.current) {
      loadExportHistory();
      loadExportStatus();
    }
    wasExportingRef.current = isExporting;
  }, [activeExport, loadExportHistory, loadExportStatus]);

  const handleSyncAction = async (dryRun: boolean, fullLibrary: boolean = false) => {
    try {
      setSyncLoading(true);
      setError(null);
      setSuccess(null);

      let importResult: PhotosSyncResult | null = null;
      let exportResult: PhotosExportResult | null = null;

      // Import phase (import or bidirectional mode)
      if (syncMode === 'import' || syncMode === 'bidirectional') {
        importResult = await apiClient.syncPhotos(undefined, dryRun);
      }

      // Export phase (export or bidirectional mode)
      if (syncMode === 'export' || syncMode === 'bidirectional') {
        exportResult = await apiClient.exportPhotos({ dryRun, fullLibrary });
      }

      setSyncResult({ importResult, exportResult });

      // Check if baseline was just set (first export run in "going forward" mode)
      const baselineJustSet = exportResult?.stats?.baseline_set === true;

      if (dryRun) {
        if (baselineJustSet) {
          setSuccess('Export baseline set. Run sync again to export new photos going forward.');
        } else if (syncMode === 'bidirectional') {
          setSuccess('Simulation complete (import + export). No changes were applied.');
        } else if (syncMode === 'export') {
          setSuccess('Export simulation complete. No photos were copied.');
        } else {
          setSuccess('Import simulation complete. No changes were applied.');
        }
      } else {
        if (baselineJustSet) {
          setSuccess('Export baseline set. Run sync again to export new photos going forward.');
        } else if (syncMode === 'bidirectional') {
          setSuccess('Photo sync complete (import + export).');
        } else if (syncMode === 'export') {
          setSuccess('Photo export complete.');
        } else {
          setSuccess('Photo import complete.');
        }
        await loadHistory();
        await loadStatus();
        if (syncMode === 'export' || syncMode === 'bidirectional') {
          await loadExportHistory();
          await loadExportStatus();
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Photo sync failed');
    } finally {
      setSyncLoading(false);
      setShowFullLibraryWarning(false);
    }
  };

  const handleSetBaseline = async () => {
    try {
      setSyncLoading(true);
      setError(null);
      const response = await apiClient.setPhotosExportBaseline();
      setSuccess(`Export baseline set to ${new Date(response.baseline_date).toLocaleString()}`);
      await loadExportStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to set baseline');
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
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold">Status</h3>
            {syncMode === 'bidirectional' && (
              <Badge variant="outline" className="bg-blue-50 text-blue-700 border-blue-200">
                Bidirectional
              </Badge>
            )}
          </div>
          <TooltipProvider>
            {/* First row - common stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
              <div>
                <p className="text-muted-foreground flex items-center gap-1">
                  Library Items
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Info className="w-3.5 h-3.5 text-muted-foreground" />
                    </TooltipTrigger>
                    <TooltipContent>
                      Total photos/videos tracked in source folders.
                    </TooltipContent>
                  </Tooltip>
                </p>
                <p className="font-medium">{status.library_items ?? 0}</p>
              </div>
              <div>
                <p className="text-muted-foreground flex items-center gap-1">
                  Pending
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Info className="w-3.5 h-3.5 text-muted-foreground" />
                    </TooltipTrigger>
                    <TooltipContent>
                      {syncMode === 'bidirectional'
                        ? 'Photos waiting to be imported or exported.'
                        : 'Photos waiting to be imported.'}
                    </TooltipContent>
                  </Tooltip>
                </p>
                <p className="font-medium">{status.pending ?? 0}</p>
              </div>
              <div>
                <p className="text-muted-foreground flex items-center gap-1">
                  Skipped
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Info className="w-3.5 h-3.5 text-muted-foreground" />
                    </TooltipTrigger>
                    <TooltipContent>
                      Photos skipped because they already exist.
                    </TooltipContent>
                  </Tooltip>
                </p>
                <p className="font-medium">{status.skipped_existing ?? 0}</p>
              </div>
              <div>
                <p className="text-muted-foreground">Last Sync</p>
                <p className="font-medium text-xs">
                  {status.last_sync ? new Date(status.last_sync).toLocaleString() : 'Never'}
                </p>
              </div>
            </div>

            {/* Second row - bidirectional mode: import + export stats */}
            {syncMode === 'bidirectional' && exportStatus && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm border-t pt-3 mt-3">
                <div>
                  <p className="text-muted-foreground flex items-center gap-1">
                    Last Imported
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Info className="w-3.5 h-3.5 text-muted-foreground" />
                      </TooltipTrigger>
                      <TooltipContent>
                        Photos imported in the most recent sync.
                      </TooltipContent>
                    </Tooltip>
                  </p>
                  <p className="font-medium">{status.last_imported ?? 0}</p>
                </div>
                <div>
                  <p className="text-muted-foreground flex items-center gap-1">
                    Total Exported
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Info className="w-3.5 h-3.5 text-muted-foreground" />
                      </TooltipTrigger>
                      <TooltipContent>
                        Total photos exported to the folder.
                      </TooltipContent>
                    </Tooltip>
                  </p>
                  <p className="font-medium">{exportStatus.total_exported ?? 0}</p>
                </div>
                <div>
                  <p className="text-muted-foreground flex items-center gap-1">
                    Export Mode
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Info className="w-3.5 h-3.5 text-muted-foreground" />
                      </TooltipTrigger>
                      <TooltipContent>
                        {exportStatus.export_mode === 'going_forward'
                          ? 'Only exports photos added after the baseline date.'
                          : 'Exports your entire photo library.'}
                      </TooltipContent>
                    </Tooltip>
                  </p>
                  <p className="font-medium capitalize">
                    {exportStatus.export_mode?.replace('_', ' ') || 'going forward'}
                  </p>
                </div>
                <div>
                  <p className="text-muted-foreground flex items-center gap-1">
                    Export Baseline
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Info className="w-3.5 h-3.5 text-muted-foreground" />
                      </TooltipTrigger>
                      <TooltipContent>
                        Only photos added after this date will be exported.
                      </TooltipContent>
                    </Tooltip>
                  </p>
                  <p className="font-medium text-xs">
                    {exportStatus.baseline_date
                      ? new Date(exportStatus.baseline_date).toLocaleString()
                      : 'Not set'}
                  </p>
                </div>
              </div>
            )}

            {/* Second row - export-only mode: export stats */}
            {syncMode === 'export' && exportStatus && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm border-t pt-3 mt-3">
                <div>
                  <p className="text-muted-foreground flex items-center gap-1">
                    Total Exported
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Info className="w-3.5 h-3.5 text-muted-foreground" />
                      </TooltipTrigger>
                      <TooltipContent>
                        Total photos exported to the folder.
                      </TooltipContent>
                    </Tooltip>
                  </p>
                  <p className="font-medium">{exportStatus.total_exported ?? 0}</p>
                </div>
                <div>
                  <p className="text-muted-foreground flex items-center gap-1">
                    Export Mode
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Info className="w-3.5 h-3.5 text-muted-foreground" />
                      </TooltipTrigger>
                      <TooltipContent>
                        {exportStatus.export_mode === 'going_forward'
                          ? 'Only exports photos added after the baseline date.'
                          : 'Exports your entire photo library.'}
                      </TooltipContent>
                    </Tooltip>
                  </p>
                  <p className="font-medium capitalize">
                    {exportStatus.export_mode?.replace('_', ' ') || 'going forward'}
                  </p>
                </div>
                <div>
                  <p className="text-muted-foreground flex items-center gap-1">
                    Export Baseline
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Info className="w-3.5 h-3.5 text-muted-foreground" />
                      </TooltipTrigger>
                      <TooltipContent>
                        Only photos added after this date will be exported.
                      </TooltipContent>
                    </Tooltip>
                  </p>
                  <p className="font-medium text-xs">
                    {exportStatus.baseline_date
                      ? new Date(exportStatus.baseline_date).toLocaleString()
                      : 'Not set'}
                  </p>
                </div>
              </div>
            )}

            {/* Second row - import-only mode: just Last Imported */}
            {syncMode === 'import' && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm border-t pt-3 mt-3">
                <div>
                  <p className="text-muted-foreground flex items-center gap-1">
                    Last Imported
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Info className="w-3.5 h-3.5 text-muted-foreground" />
                      </TooltipTrigger>
                      <TooltipContent>
                        Photos imported in the most recent sync.
                      </TooltipContent>
                    </Tooltip>
                  </p>
                  <p className="font-medium">{status.last_imported ?? 0}</p>
                </div>
              </div>
            )}
          </TooltipProvider>
        </div>
      )}

      {(activeSync || activeExport) && (
        <div className="rounded-lg border bg-card/60 p-4 space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Activity className="w-4 h-4" />
            {syncMode === 'bidirectional' ? 'Sync in progress' : activeSync ? 'Import in progress' : 'Export in progress'}
          </div>
          {activeSync && (
            <div>
              <div className="flex justify-between text-sm mb-2">
                <span>{syncMode === 'bidirectional' ? `Import: ${activeSync.message}` : activeSync.message}</span>
                <span>{activeSync.progress}%</span>
              </div>
              <Progress value={activeSync.progress} />
            </div>
          )}
          {activeExport && (
            <div className={activeSync ? 'mt-3' : ''}>
              <div className="flex justify-between text-sm mb-2">
                <span>{syncMode === 'bidirectional' ? `Export: ${activeExport.message}` : activeExport.message}</span>
                <span>{activeExport.progress}%</span>
              </div>
              <Progress value={activeExport.progress} />
            </div>
          )}
        </div>
      )}

      <section className="rounded-lg border p-6 space-y-4">
        <div>
          <h2 className="text-xl font-semibold">
            {syncMode === 'import' && 'Import Photos'}
            {syncMode === 'bidirectional' && 'Sync Photos'}
            {syncMode === 'export' && 'Export Photos'}
          </h2>
          <p className="text-sm text-muted-foreground">
            {syncMode === 'import' && 'Scan configured source folders and import new photos to Apple Photos.'}
            {syncMode === 'bidirectional' && 'Import photos from folders AND export from Apple Photos to the same folder.'}
            {syncMode === 'export' && 'Export photos from Apple Photos to your configured folder.'}
          </p>
        </div>

        {/* Warning for bidirectional mode */}
        {syncMode === 'bidirectional' && (
          <Alert variant="warning" className="border-orange-500 bg-orange-50">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Disable NextCloud Auto-Upload</AlertTitle>
            <AlertDescription>
              When using bidirectional sync, disable auto-upload from the NextCloud mobile app to avoid duplicates.
            </AlertDescription>
          </Alert>
        )}

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
          {/* Set Baseline button for export/bidirectional modes when no baseline exists */}
          {(syncMode === 'bidirectional' || syncMode === 'export') && exportStatus && !exportStatus.baseline_date && (
            <Button
              variant="secondary"
              disabled={syncLoading}
              onClick={handleSetBaseline}
              className="min-w-[120px]"
            >
              Set Baseline
            </Button>
          )}
          {/* Full Library Export button for export/bidirectional modes */}
          {(syncMode === 'bidirectional' || syncMode === 'export') && (
            <Button
              variant="ghost"
              disabled={syncLoading}
              onClick={() => setShowFullLibraryWarning(true)}
              className="min-w-[140px]"
            >
              Full Library
            </Button>
          )}
        </div>

        <SyncStatsView result={syncResult} />
      </section>

      {/* Full Library Warning Dialog */}
      <Dialog open={showFullLibraryWarning} onOpenChange={setShowFullLibraryWarning}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="w-5 h-5 text-amber-500" />
              {syncMode === 'bidirectional' ? 'Full Library Sync' : 'Export Full Library'}
            </DialogTitle>
            <DialogDescription>
              {syncMode === 'bidirectional'
                ? 'This will import all photos from your folder AND export your entire Apple Photos library. For large libraries, this may take a long time.'
                : 'This will export your entire Apple Photos library. For large libraries, this may take a long time.'}
            </DialogDescription>
          </DialogHeader>
          <div className="py-4 text-sm text-muted-foreground">
            <p>Recommended: Run a simulation first to preview what will be synced.</p>
          </div>
          <DialogFooter className="flex gap-2">
            <Button variant="outline" onClick={() => setShowFullLibraryWarning(false)}>
              Cancel
            </Button>
            <Button variant="secondary" onClick={() => { setShowFullLibraryWarning(false); handleSyncAction(true, true); }}>
              Simulate First
            </Button>
            <Button onClick={() => handleSyncAction(false, true)}>
              {syncMode === 'bidirectional' ? 'Sync All' : 'Export All'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <section className="rounded-lg border p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-lg font-semibold flex items-center gap-2">
              Import history
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

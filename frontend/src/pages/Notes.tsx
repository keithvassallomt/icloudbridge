import { Fragment, useEffect, useState } from 'react';
import {
  FileText,
  RefreshCw,
  Trash2,
  AlertTriangle,
  PlayCircle,
  ChevronDown,
  FolderOpen,
  PlusCircle,
  MinusCircle,
  CheckCircle2,
  ChevronRight,
  ListChecks,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Progress } from '@/components/ui/progress';
import { Label } from '@/components/ui/label';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { FolderMappingTable } from '@/components/FolderMappingTable';
import apiClient from '@/lib/api-client';
import { useSyncStore } from '@/store/sync-store';
import type { SyncLog, SetupVerificationResponse, NotesAllFoldersResponse, FolderMapping, AppConfig, SyncResponse } from '@/types/api';
import ServiceDisabledNotice from '@/components/ServiceDisabledNotice';

type SimulationChangeCategory = 'added' | 'updated' | 'deleted' | 'unchanged';

interface SimulationDetailSection {
  added?: string[];
  updated?: string[];
  deleted?: string[];
  unchanged?: string[];
}

interface SimulationFolderStats {
  created_local?: number;
  created_remote?: number;
  updated_local?: number;
  updated_remote?: number;
  deleted_local?: number;
  deleted_remote?: number;
  unchanged?: number;
  would_delete_local?: number;
  would_delete_remote?: number;
  details?: {
    apple?: SimulationDetailSection;
    markdown?: SimulationDetailSection;
  };
}

interface SimulationFolderResult {
  folder: string;
  status: 'success' | 'error';
  stats?: SimulationFolderStats;
  error?: string;
}

type SimulationChangeSummary = Record<SimulationChangeCategory, number>;

const CHANGE_ORDER: SimulationChangeCategory[] = ['added', 'updated', 'deleted', 'unchanged'];
const CHANGE_LABELS: Record<SimulationChangeCategory, string> = {
  added: 'Added',
  updated: 'Updated',
  deleted: 'Deleted',
  unchanged: 'Unchanged',
};

const CHANGE_ICONS: Record<SimulationChangeCategory, typeof PlusCircle> = {
  added: PlusCircle,
  updated: RefreshCw,
  deleted: MinusCircle,
  unchanged: CheckCircle2,
};

const CHANGE_COLORS: Record<SimulationChangeCategory, string> = {
  added: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-400/40 dark:bg-emerald-400/10 dark:text-emerald-200',
  updated: 'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-400/40 dark:bg-blue-400/10 dark:text-blue-200',
  deleted: 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-400/40 dark:bg-rose-400/10 dark:text-rose-200',
  unchanged: 'border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-400/40 dark:bg-slate-400/10 dark:text-slate-200',
};

const buildSummary = (stats?: SimulationFolderStats) => ({
  apple: {
    added: stats?.created_local ?? 0,
    updated: stats?.updated_local ?? 0,
    deleted: stats?.deleted_local ?? 0,
    unchanged: stats?.unchanged ?? 0,
  } satisfies SimulationChangeSummary,
  markdown: {
    added: stats?.created_remote ?? 0,
    updated: stats?.updated_remote ?? 0,
    deleted: stats?.deleted_remote ?? 0,
    unchanged: stats?.unchanged ?? 0,
  } satisfies SimulationChangeSummary,
});

const buildDetails = (stats?: SimulationFolderStats) => ({
  apple: stats?.details?.apple ?? {},
  markdown: stats?.details?.markdown ?? {},
});

interface FolderResultsTableProps {
  folderResults: SimulationFolderResult[];
  emptyMessage: string;
  contextPrefix: string;
  expandedState: Record<string, boolean>;
  onToggle: (folderKey: string) => void;
}

const FolderResultsTable = ({
  folderResults,
  emptyMessage,
  contextPrefix,
  expandedState,
  onToggle,
}: FolderResultsTableProps) => {
  if (folderResults.length === 0) {
    return <div className="py-8 text-center text-muted-foreground">{emptyMessage}</div>;
  }

  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-sm">
        <thead className="bg-muted/40">
          <tr>
            <th className="p-4 text-left font-semibold">Folder</th>
            <th className="p-4 text-left font-semibold">
              <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground">
                <FileText className="h-4 w-4" />
                Apple Notes
              </div>
            </th>
            <th className="p-4 text-left font-semibold">
              <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground">
                <FolderOpen className="h-4 w-4" />
                Markdown / Nextcloud
              </div>
            </th>
          </tr>
        </thead>
        <tbody>
          {folderResults.map((folderResult, index) => {
            const stats = folderResult.stats;
            const summaries = buildSummary(stats);
            const details = buildDetails(stats);
            const folderKey = `${contextPrefix}:${folderResult.folder}:${index}`;
            const isExpanded = expandedState[folderKey];

            return (
              <Fragment key={`${folderResult.folder}-${index}`}>
                <tr className="border-b bg-background/70 transition-colors hover:bg-muted/30">
                  <td className="align-top p-4">
                    <div className="flex items-start gap-3">
                      {folderResult.status === 'success' && (
                        <button
                          onClick={() => onToggle(folderKey)}
                          className="rounded-full border p-1 text-muted-foreground transition-colors hover:bg-muted"
                          aria-label={isExpanded ? 'Collapse details' : 'Expand details'}
                        >
                          {isExpanded ? (
                            <ChevronDown className="h-4 w-4" />
                          ) : (
                            <ChevronRight className="h-4 w-4" />
                          )}
                        </button>
                      )}
                      <div className="space-y-1">
                        <div className="text-base font-semibold">{folderResult.folder}</div>
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <ListChecks className="h-3.5 w-3.5" />
                          {folderResult.status === 'success' ? 'Sync ready' : 'Sync failed'}
                        </div>
                        {folderResult.status === 'error' && (
                          <div className="flex items-center gap-2 text-sm text-rose-600">
                            <AlertTriangle className="h-4 w-4" />
                            {folderResult.error || 'Unknown error'}
                          </div>
                        )}
                      </div>
                    </div>
                  </td>
                  {folderResult.status === 'error' ? (
                    <td className="p-4 text-sm text-muted-foreground" colSpan={2}>
                      Unable to preview stats for this folder.
                    </td>
                  ) : (
                    <>
                      <td className="p-4 align-top">
                        <div className="grid grid-cols-2 gap-2">
                          {CHANGE_ORDER.map((key) => {
                            const Icon = CHANGE_ICONS[key];
                            return (
                              <div key={key} className={`flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-semibold ${CHANGE_COLORS[key]}`}>
                                <Icon className="h-3.5 w-3.5" />
                                <span>{CHANGE_LABELS[key]}</span>
                                <span className="ml-auto text-sm">{summaries.apple[key]}</span>
                              </div>
                            );
                          })}
                        </div>
                      </td>
                      <td className="p-4 align-top">
                        <div className="grid grid-cols-2 gap-2">
                          {CHANGE_ORDER.map((key) => {
                            const Icon = CHANGE_ICONS[key];
                            return (
                              <div key={key} className={`flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-semibold ${CHANGE_COLORS[key]}`}>
                                <Icon className="h-3.5 w-3.5" />
                                <span>{CHANGE_LABELS[key]}</span>
                                <span className="ml-auto text-sm">{summaries.markdown[key]}</span>
                              </div>
                            );
                          })}
                        </div>
                      </td>
                    </>
                  )}
                </tr>
                {folderResult.status === 'success' && (
                  <tr className="border-b">
                    <td colSpan={3} className="bg-muted/20 p-4">
                      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${isExpanded ? 'rotate-0' : '-rotate-90'}`} />
                        Details
                      </div>
                      {isExpanded ? (
                        <div className="mt-4 grid gap-6 md:grid-cols-2">
                          {(
                            [
                              { key: 'apple', title: 'Apple Notes', icon: FileText },
                              { key: 'markdown', title: 'Markdown / Nextcloud', icon: FolderOpen },
                            ] as const
                          ).map(({ key, title, icon: Icon }) => {
                            const sectionDetails = details[key];
                            const sectionSummary = summaries[key];
                            return (
                              <div key={key} className="space-y-3 rounded-lg border bg-background/80 p-4 shadow-sm">
                                <div className="flex items-center gap-2 text-sm font-semibold">
                                  <Icon className="h-4 w-4 text-muted-foreground" />
                                  {title}
                                </div>
                                <div className="space-y-3 text-sm">
                                  {CHANGE_ORDER.map((category) => {
                                    const IconBadge = CHANGE_ICONS[category];
                                    const list = sectionDetails?.[category] ?? [];
                                    const count = sectionSummary[category];
                                    return (
                                      <div key={category}>
                                        <div className="flex items-center gap-2 font-semibold">
                                          <IconBadge className={`h-4 w-4 ${category === 'deleted' ? 'text-rose-500' : 'text-muted-foreground'}`} />
                                          <span>{CHANGE_LABELS[category]}</span>
                                          <span className="ml-auto text-xs text-muted-foreground">{count}</span>
                                        </div>
                                        {list && list.length > 0 ? (
                                          <ul className="mt-2 list-disc space-y-1 pl-6 text-xs text-muted-foreground">
                                            {list.map((item, idx) => (
                                              <li key={`${category}-${idx}`}>{item}</li>
                                            ))}
                                          </ul>
                                        ) : (
                                          <p className="mt-2 text-xs text-muted-foreground">No items in this category.</p>
                                        )}
                                      </div>
                                    );
                                  })}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      ) : (
                        <div className="mt-4 text-sm text-muted-foreground">Expand to view detailed note changes.</div>
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

export default function Notes() {
  const [allFolders, setAllFolders] = useState<NotesAllFoldersResponse | null>(null);
  const [history, setHistory] = useState<SyncLog[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [simulating, setSimulating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [verification, setVerification] = useState<SetupVerificationResponse | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [simulationResult, setSimulationResult] = useState<SyncResponse | null>(null);
  const [mode, setMode] = useState<'auto' | 'manual'>('auto');
  const [showMappings, setShowMappings] = useState(false);
  const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>({});

  const { activeSyncs } = useSyncStore();
  const activeSync = activeSyncs.get('notes');

  useEffect(() => {
    loadData();
    loadVerification();
  }, []);

  // Set to manual mode if there are existing mappings
  useEffect(() => {
    if (config?.notes_folder_mappings && Object.keys(config.notes_folder_mappings).length > 0) {
      setMode('manual');
      setShowMappings(true);
    }
  }, [config]);

  const loadData = async () => {
    try {
      setLoading(true);
      const [allFoldersData, historyData, configData] = await Promise.all([
        apiClient.getAllNotesFolders(),
        apiClient.getNotesHistory(10),
        apiClient.getConfig(),
      ]);
      setAllFolders(allFoldersData);
      setHistory(historyData.logs);
      setConfig(configData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load data');
    } finally {
      setLoading(false);
    }
  };

  const loadVerification = async () => {
    try {
      const result = await apiClient.verifySetup();
      setVerification(result);
    } catch (err) {
      console.error('Failed to load verification:', err);
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

      const result = await apiClient.syncNotes({
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

  const simulationFolderResults = (simulationResult?.stats?.folder_results as SimulationFolderResult[]) || [];

  const toggleFolderDetails = (folderKey: string) => {
    setExpandedFolders((prev) => ({ ...prev, [folderKey]: !prev[folderKey] }));
  };

  if (!loading && config && config.notes_enabled === false) {
    return <ServiceDisabledNotice serviceName="Notes" />;
  }

  const handleSaveMappings = async (mappings: Record<string, FolderMapping>) => {
    try {
      setError(null);
      setSuccess(null);

      await apiClient.updateConfig({
        notes_folder_mappings: mappings,
      });

      setSuccess('Folder mappings saved successfully');
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save mappings');
      throw err; // Re-throw so FolderMappingTable knows it failed
    }
  };

  const handleReset = async () => {
    if (!confirm('Are you sure you want to reset Notes sync? This will clear all sync state.')) {
      return;
    }

    try {
      setLoading(true);
      await apiClient.resetNotes();
      setSuccess('Notes sync reset successfully');
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

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <FileText className="w-8 h-8" />
            Notes Sync
          </h1>
          <p className="text-muted-foreground">
            Sync Apple Notes with your markdown files
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

      {/* Setup Verification Warning */}
      {verification && !verification.all_ready && (
        <Alert variant="warning" className="border-orange-500 bg-orange-50">
          <AlertTriangle className="h-4 w-4" />
          <div>
            <AlertTitle>Setup Incomplete</AlertTitle>
            <AlertDescription>
              <p className="mb-2">
                Your Notes sync setup needs attention:
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
              <Button
                size="sm"
                variant="outline"
                onClick={loadVerification}
                className="bg-white"
              >
                <RefreshCw className="w-4 h-4 mr-2" />
                Check Again
              </Button>
            </AlertDescription>
          </div>
        </Alert>
      )}

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
          <CardDescription>Configure and run notes synchronisation</CardDescription>
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
              {mode === 'auto' && 'Automatically sync all Apple Notes folders to markdown folders with matching names using 1:1 mapping.'}
              {mode === 'manual' && 'Manually configure which Apple Notes folders sync to which markdown folders. Only mapped folders will sync.'}
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
            <div className="space-y-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="text-sm text-muted-foreground">
                  Preview covers {(simulationResult.stats?.folder_count ?? simulationFolderResults.length).toString()} folder(s)
                  {simulationResult.stats?.mapping_mode ? ' using manual mappings.' : '.'}
                </div>
                <Button onClick={() => setSimulationResult(null)} variant="outline" size="sm">
                  Dismiss
                </Button>
              </div>

              <FolderResultsTable
                folderResults={simulationFolderResults}
                emptyMessage="Simulation finished without a folder-by-folder breakdown."
                contextPrefix="simulation"
                expandedState={expandedFolders}
                onToggle={toggleFolderDetails}
              />
            </div>
          </CardContent>
        </Card>
      )}
      {/* Folder Mappings */}
      <Card>
        <CardHeader>
          <CardTitle>Folder Mappings</CardTitle>
          <CardDescription>
            {mode === 'auto'
              ? 'View automatic folder mappings (read-only)'
              : 'Configure which Apple Notes folders sync to which markdown folders'}
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
                {loading || !allFolders ? (
                  <div className="text-center text-muted-foreground">Loading...</div>
                ) : Object.keys(allFolders.folders).length === 0 ? (
                  <div className="text-center text-muted-foreground">No folders found</div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full">
                      <thead>
                        <tr className="border-b">
                          <th className="text-left p-3 font-medium">Apple Notes Folder</th>
                          <th className="text-left p-3 font-medium">Markdown Folder</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(() => {
                          // Merge folders by normalized path to avoid duplicates
                          const mergedFolders = new Map<string, { apple: boolean; markdown: boolean }>();

                          Object.entries(allFolders.folders).forEach(([path, info]) => {
                            // Normalize path by removing iCloud/ prefix
                            const normalizedPath = path.startsWith('iCloud/') ? path.slice(7) : path;

                            // Merge info for the same normalized path
                            const existing = mergedFolders.get(normalizedPath);
                            if (existing) {
                              mergedFolders.set(normalizedPath, {
                                apple: existing.apple || info.apple,
                                markdown: existing.markdown || info.markdown,
                              });
                            } else {
                              mergedFolders.set(normalizedPath, {
                                apple: info.apple,
                                markdown: info.markdown,
                              });
                            }
                          });

                          return Array.from(mergedFolders.entries())
                            .filter(([, info]) => info.apple || info.markdown)
                            .map(([displayPath, info]) => (
                              <tr key={displayPath} className="border-b">
                                <td className="p-3">
                                  <div className="flex items-center gap-2">
                                    <FolderOpen className="w-4 h-4 text-muted-foreground" />
                                    <span className="font-medium">{displayPath}</span>
                                    {!info.apple && info.markdown && (
                                      <Badge variant="outline" className="text-xs bg-blue-50 text-blue-700 border-blue-200">
                                        Will be created
                                      </Badge>
                                    )}
                                  </div>
                                </td>
                                <td className="p-3">
                                  <div className="flex items-center gap-2">
                                    <span className="text-muted-foreground">{displayPath}</span>
                                    {info.apple && !info.markdown && (
                                      <Badge variant="outline" className="text-xs bg-blue-50 text-blue-700 border-blue-200">
                                        Will be created
                                      </Badge>
                                    )}
                                  </div>
                                </td>
                              </tr>
                            ));
                        })()}
                      </tbody>
                    </table>
                  </div>
                )}
              </CollapsibleContent>
            </Collapsible>
          ) : (
            loading || !allFolders || !config ? (
              <div className="text-center text-muted-foreground py-8">Loading folders...</div>
            ) : (
              <FolderMappingTable
                folders={allFolders.folders}
                mappings={config.notes_folder_mappings || {}}
                onSave={handleSaveMappings}
                manualMappingEnabled={mode === 'manual'}
              />
            )
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
              {history.map((log) => {
                const historyFolderResults = (log.stats?.folder_results as SimulationFolderResult[]) || [];
                const historyFolderCount = log.stats?.folder_count ?? historyFolderResults.length;
                return (
                  <div key={log.id} className="border-l-2 pl-4 py-2 space-y-2" style={{
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
                  {historyFolderResults.length > 0 ? (
                    <div className="rounded-lg border border-muted/60 bg-background/70 p-3">
                      <div className="text-xs text-muted-foreground mb-3">
                        Sync covered {historyFolderCount.toString()} folder(s)
                        {log.stats?.mapping_mode ? ' using manual mappings.' : '.'}
                      </div>
                      <FolderResultsTable
                        folderResults={historyFolderResults}
                        emptyMessage="Sync finished without a folder-by-folder breakdown."
                        contextPrefix={`history-${log.id}`}
                        expandedState={expandedFolders}
                        onToggle={toggleFolderDetails}
                      />
                    </div>
                  ) : (
                    <div className="text-xs text-muted-foreground">No folder-by-folder data captured for this sync.</div>
                  )}
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

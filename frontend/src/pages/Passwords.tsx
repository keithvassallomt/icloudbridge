import { useEffect, useRef, useState } from 'react';
import { Key, RefreshCw, Upload, PlayCircle, Activity, FileDown, Download, ChevronDown, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import apiClient from '@/lib/api-client';
import { useSyncStore } from '@/store/sync-store';
import type { AppConfig, PasswordsDownloadInfo, PasswordsSyncResponse, SyncLog } from '@/types/api';
import ServiceDisabledNotice from '@/components/ServiceDisabledNotice';

type PasswordProvider = 'vaultwarden' | 'nextcloud';

function DownloadLink({ info }: { info: PasswordsDownloadInfo }) {
  const [hasExpired, setHasExpired] = useState(false);
  const [countdown, setCountdown] = useState('');

  useEffect(() => {
    const expiresAt = new Date(info.expires_at).getTime();

    const updateCountdown = () => {
      const remaining = expiresAt - Date.now();
      if (remaining <= 0) {
        setHasExpired(true);
        setCountdown('expired');
        return;
      }
      const minutes = Math.floor(remaining / 60000);
      const seconds = Math.floor((remaining % 60000) / 1000);
      setCountdown(`${minutes}m ${seconds}s`);
    };

    updateCountdown();
    const timer = setInterval(updateCountdown, 1000);
    return () => clearInterval(timer);
  }, [info]);

  return (
    <div className="space-y-1">
      <Button
        asChild
        variant="outline"
        disabled={hasExpired}
        className="w-full md:w-auto"
      >
        <a
          href={`/api/passwords/download/${info.token}`}
          download={info.filename}
          className="flex items-center gap-2"
        >
          <FileDown className="w-4 h-4" />
          {hasExpired ? 'Link expired' : 'Download Apple CSV'}
        </a>
      </Button>
      <p className="text-xs text-muted-foreground">
        {hasExpired ? 'Please generate a new import file.' : `Link expires in ${countdown}`}
      </p>
    </div>
  );
}

function SyncStatsView({ result, providerLabel }: { result: PasswordsSyncResponse | null; providerLabel: string }) {
  const [pushOpen, setPushOpen] = useState(true);
  const [pullOpen, setPullOpen] = useState(true);
  const [pushEntriesOpen, setPushEntriesOpen] = useState(false);
  const [pullEntriesOpen, setPullEntriesOpen] = useState(false);

  if (!result) {
    return null;
  }

  const { stats } = result;
  const hasPushStats = stats.push && (stats.push.created > 0 || stats.push.skipped > 0 || stats.push.failed > 0 || stats.push.deleted > 0);
  const hasPullStats = stats.pull && (stats.pull.new_entries > 0 || stats.pull.deleted > 0);

  if (!hasPushStats && !hasPullStats) {
    return (
      <div className="rounded-md border bg-card/40 p-4">
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>{result.simulate ? 'Simulation results' : 'Sync results'}</span>
          <span>{stats.total_time ? `${stats.total_time.toFixed(1)}s` : ''}</span>
        </div>
        <p className="text-sm text-muted-foreground mt-3">No changes detected.</p>
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-md border bg-card/40 p-4">
      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>{result.simulate ? 'Simulation results' : 'Sync results'}</span>
        <span>{stats.total_time ? `${stats.total_time.toFixed(1)}s` : ''}</span>
      </div>

      {hasPullStats && stats.pull && (
        <Collapsible open={pullOpen} onOpenChange={setPullOpen}>
          <div className="rounded-lg border bg-background">
            <CollapsibleTrigger className="flex w-full items-center justify-between p-3 hover:bg-accent/50 transition-colors">
              <div className="flex items-center gap-2">
                {pullOpen ? (
                  <ChevronDown className="h-4 w-4 text-muted-foreground" />
                ) : (
                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                )}
                <span className="font-semibold text-sm">Apple Passwords Import</span>
              </div>
              <div className="text-sm text-muted-foreground">
                New: {stats.pull?.new_entries ?? 0} {(stats.pull?.deleted ?? 0) > 0 && `• Deleted: ${stats.pull?.deleted ?? 0}`}
              </div>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <div className="px-3 pb-3 pt-1 space-y-3">
                <div className="grid grid-cols-3 gap-3 text-sm">
                  <div className="rounded-md bg-muted/50 p-2">
                    <p className="text-muted-foreground text-xs">New</p>
                    <p className="font-semibold text-lg">{stats.pull?.new_entries ?? 0}</p>
                  </div>
                  <div className="rounded-md bg-muted/50 p-2">
                    <p className="text-muted-foreground text-xs">Updated</p>
                    <p className="font-semibold text-lg">0</p>
                  </div>
                  <div className="rounded-md bg-destructive/10 p-2">
                    <p className="text-muted-foreground text-xs">Deleted</p>
                    <p className="font-semibold text-lg text-destructive">{stats.pull?.deleted ?? 0}</p>
                  </div>
                </div>
                {stats.pull?.entries && stats.pull.entries.length > 0 && (
                  <Collapsible open={pullEntriesOpen} onOpenChange={setPullEntriesOpen}>
                    <CollapsibleTrigger className="flex w-full items-center gap-2 text-xs text-muted-foreground hover:text-foreground transition-colors">
                      {pullEntriesOpen ? (
                        <ChevronDown className="h-3 w-3" />
                      ) : (
                        <ChevronRight className="h-3 w-3" />
                      )}
                      <span>View {stats.pull.entries.length} password{stats.pull.entries.length !== 1 ? 's' : ''}</span>
                    </CollapsibleTrigger>
                    <CollapsibleContent className="mt-2">
                      <div className="rounded-md border bg-muted/20 max-h-48 overflow-y-auto">
                        <div className="p-2 space-y-1">
                          {stats.pull.entries.map((entry, idx) => (
                            <div key={idx} className="text-xs p-1.5 rounded hover:bg-muted/50">
                              <div className="font-medium">{entry.title}</div>
                              {entry.username && (
                                <div className="text-muted-foreground text-[10px]">{entry.username}</div>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    </CollapsibleContent>
                  </Collapsible>
                )}
                {result.download && (stats.pull?.new_entries ?? 0) > 0 && (
                  <DownloadLink info={result.download} />
                )}
                {(stats.pull?.new_entries ?? 0) === 0 && (
                  <p className="text-xs text-muted-foreground">No new entries found.</p>
                )}
              </div>
            </CollapsibleContent>
          </div>
        </Collapsible>
      )}

      {hasPushStats && stats.push && (
        <Collapsible open={pushOpen} onOpenChange={setPushOpen}>
          <div className="rounded-lg border bg-background">
            <CollapsibleTrigger className="flex w-full items-center justify-between p-3 hover:bg-accent/50 transition-colors">
              <div className="flex items-center gap-2">
                {pushOpen ? (
                  <ChevronDown className="h-4 w-4 text-muted-foreground" />
                ) : (
                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                )}
                <span className="font-semibold text-sm">{providerLabel} Import</span>
              </div>
              <div className="text-sm text-muted-foreground">
                New: {stats.push?.created ?? 0} {(stats.push?.deleted ?? 0) > 0 && `• Deleted: ${stats.push?.deleted ?? 0}`}
              </div>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <div className="px-3 pb-3 pt-1 space-y-3">
                <div className="grid grid-cols-3 gap-3 text-sm">
                  <div className="rounded-md bg-muted/50 p-2">
                    <p className="text-muted-foreground text-xs">New</p>
                    <p className="font-semibold text-lg">{stats.push?.created ?? 0}</p>
                  </div>
                  <div className="rounded-md bg-muted/50 p-2">
                    <p className="text-muted-foreground text-xs">Updated</p>
                    <p className="font-semibold text-lg">0</p>
                  </div>
                  <div className="rounded-md bg-destructive/10 p-2">
                    <p className="text-muted-foreground text-xs">Deleted</p>
                    <p className="font-semibold text-lg text-destructive">{stats.push?.deleted ?? 0}</p>
                  </div>
                </div>
                {stats.push?.entries && stats.push.entries.length > 0 && (
                  <Collapsible open={pushEntriesOpen} onOpenChange={setPushEntriesOpen}>
                    <CollapsibleTrigger className="flex w-full items-center gap-2 text-xs text-muted-foreground hover:text-foreground transition-colors">
                      {pushEntriesOpen ? (
                        <ChevronDown className="h-3 w-3" />
                      ) : (
                        <ChevronRight className="h-3 w-3" />
                      )}
                      <span>View {stats.push.entries.length} password{stats.push.entries.length !== 1 ? 's' : ''}</span>
                    </CollapsibleTrigger>
                    <CollapsibleContent className="mt-2">
                      <div className="rounded-md border bg-muted/20 max-h-48 overflow-y-auto">
                        <div className="p-2 space-y-1">
                          {stats.push.entries.map((entry, idx) => (
                            <div key={idx} className="text-xs p-1.5 rounded hover:bg-muted/50">
                              <div className="font-medium">{entry.title}</div>
                              {entry.username && (
                                <div className="text-muted-foreground text-[10px]">{entry.username}</div>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    </CollapsibleContent>
                  </Collapsible>
                )}
                {((stats.push?.skipped ?? 0) > 0 || (stats.push?.failed ?? 0) > 0) && (
                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <div className="rounded-md bg-muted/30 p-2">
                      <p className="text-muted-foreground text-xs">Skipped</p>
                      <p className="font-medium">{stats.push?.skipped ?? 0}</p>
                    </div>
                    <div className="rounded-md bg-muted/30 p-2">
                      <p className="text-muted-foreground text-xs">Failed</p>
                      <p className="font-medium">{stats.push?.failed ?? 0}</p>
                    </div>
                  </div>
                )}
                {stats.push?.errors && stats.push.errors.length > 0 && (
                  <div className="rounded-md bg-destructive/10 p-2">
                    <p className="text-xs font-medium text-destructive mb-1">Errors:</p>
                    <ul className="text-xs text-destructive list-disc list-inside space-y-0.5">
                      {stats.push.errors.map((err, idx) => (
                        <li key={`${err}-${idx}`}>{err}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </CollapsibleContent>
          </div>
        </Collapsible>
      )}
    </div>
  );
}

export default function Passwords() {
  const [history, setHistory] = useState<SyncLog[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [bidirectionalFile, setBidirectionalFile] = useState<File | null>(null);
  const [exportFile, setExportFile] = useState<File | null>(null);
  const [bidirectionalResult, setBidirectionalResult] = useState<PasswordsSyncResponse | null>(null);
  const [exportResult, setExportResult] = useState<PasswordsSyncResponse | null>(null);
  const [importResult, setImportResult] = useState<PasswordsSyncResponse | null>(null);

  const [bidirectionalLoading, setBidirectionalLoading] = useState(false);
  const [exportLoading, setExportLoading] = useState(false);
  const [importLoading, setImportLoading] = useState(false);

  const { activeSyncs } = useSyncStore();
  const activeSync = activeSyncs.get('passwords');

  const bidirectionalInputRef = useRef<HTMLInputElement | null>(null);
  const exportInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    loadHistory();
    loadConfig();
  }, []);

  const loadConfig = async () => {
    try {
      const cfg = await apiClient.getConfig();
      setConfig(cfg);
    } catch (err) {
      console.error('Failed to load config:', err);
    }
  };

  const loadHistory = async () => {
    try {
      setHistoryLoading(true);
      const historyData = await apiClient.getPasswordsHistory(10);
      setHistory(historyData.logs);
    } catch (err) {
      console.error(err);
    } finally {
      setHistoryLoading(false);
    }
  };

  const handleBidirectionalAction = async (simulate: boolean) => {
    if (!bidirectionalFile) {
      setError('Upload an Apple Passwords CSV first.');
      return;
    }

    try {
      setBidirectionalLoading(true);
      setError(null);
      setSuccess(null);
      const response = await apiClient.passwordsBidirectionalSync(bidirectionalFile, {
        simulate,
        bulk: true,
      });
      setBidirectionalResult(response);
      if (simulate) {
        setSuccess('Simulation complete. No changes were applied.');
      } else {
        setSuccess('Bidirectional sync complete.');
        await loadHistory();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Bidirectional sync failed');
    } finally {
      setBidirectionalLoading(false);
    }
  };

  const handleExportAction = async () => {
    if (!exportFile) {
      setError('Upload an Apple Passwords CSV to export.');
      return;
    }
    try {
      setExportLoading(true);
      setError(null);
      setSuccess(null);
      const response = await apiClient.passwordsExportToProvider(exportFile, { bulk: true });
      setExportResult(response);
      setSuccess(`Export to ${providerActionLabel} complete.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export failed');
    } finally {
      setExportLoading(false);
    }
  };

  const handleImportAction = async () => {
    try {
      setImportLoading(true);
      setError(null);
      setSuccess(null);
      const response = await apiClient.passwordsImportFromProvider({ bulk: true });
      setImportResult(response);
      if (response.stats.pull?.new_entries) {
        setSuccess('Import prepared. Download the Apple CSV and import it.');
      } else {
        setSuccess(`No new ${providerActionLabel} entries found.`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Import failed');
    } finally {
      setImportLoading(false);
    }
  };

  const handleReset = async () => {
    if (!confirm('Reset password sync state? This clears cached metadata.')) {
      return;
    }
    try {
      setHistoryLoading(true);
      await apiClient.resetPasswords();
      setSuccess('Passwords sync state reset.');
      await loadHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Reset failed');
    } finally {
      setHistoryLoading(false);
    }
  };

  const formatDate = (dateStr: string) => new Date(dateStr).toLocaleString();

  const disabled = config && config.passwords_enabled === false;
  const provider: PasswordProvider = (config?.passwords_provider as PasswordProvider) ?? 'vaultwarden';
  const providerLabel = provider === 'nextcloud' ? 'Nextcloud Passwords' : 'VaultWarden';
  const providerMarketingLabel = provider === 'nextcloud' ? 'Nextcloud Passwords' : 'Bitwarden / VaultWarden';
  const providerActionLabel = providerLabel;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <Key className="w-8 h-8" />
            Passwords Sync
          </h1>
          <p className="text-muted-foreground">
            Sync passwords with {providerMarketingLabel} and manage exports
          </p>
        </div>
        <Button onClick={loadHistory} variant="outline" disabled={historyLoading}>
          <RefreshCw className={`w-4 h-4 mr-2 ${historyLoading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      <input
        type="file"
        ref={bidirectionalInputRef}
        accept=".csv"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0] ?? null;
          setBidirectionalFile(file);
          setBidirectionalResult(null);
          if (bidirectionalInputRef.current) {
            bidirectionalInputRef.current.value = '';
          }
        }}
      />
      <input
        type="file"
        ref={exportInputRef}
        accept=".csv"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0] ?? null;
          setExportFile(file);
          setExportResult(null);
          if (exportInputRef.current) {
            exportInputRef.current.value = '';
          }
        }}
      />

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

      {disabled ? (
        <ServiceDisabledNotice serviceName="Passwords" />
      ) : (
        <>
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
                <h2 className="text-xl font-semibold">Bidirectional sync</h2>
                <p className="text-sm text-muted-foreground">
                  Upload the latest Apple Passwords export, simulate the run, then apply it.
                </p>
              </div>
              <Button
                variant="outline"
                onClick={() => bidirectionalInputRef.current?.click()}
              >
                <Upload className="w-4 h-4 mr-2" />
                Upload Apple CSV
              </Button>
            </div>

            {bidirectionalFile ? (
              <Badge variant="outline" className="w-fit">{bidirectionalFile.name}</Badge>
            ) : (
              <p className="text-sm text-muted-foreground">No file selected.</p>
            )}

            <div className="flex flex-wrap gap-3">
              <Button
                variant="outline"
                disabled={!bidirectionalFile || bidirectionalLoading}
                onClick={() => handleBidirectionalAction(true)}
                className="flex-1 min-w-[120px]"
              >
                {bidirectionalLoading ? (
                  <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                ) : (
                  <PlayCircle className="w-4 h-4 mr-2" />
                )}
                Simulate
              </Button>
              <Button
                disabled={!bidirectionalFile || bidirectionalLoading}
                onClick={() => handleBidirectionalAction(false)}
                className="flex-1 min-w-[120px]"
              >
                {bidirectionalLoading ? (
                  <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                ) : (
                  <RefreshCw className="w-4 h-4 mr-2" />
                )}
                Sync
              </Button>
            </div>

            <SyncStatsView result={bidirectionalResult} providerLabel={providerActionLabel} />
          </section>

          <div className="grid gap-6 md:grid-cols-2">
            <section className="rounded-lg border p-6 space-y-4">
              <div>
                <h3 className="text-lg font-semibold">Export Passwords</h3>
                <p className="text-sm text-muted-foreground">
                  Push Apple Passwords changes to {providerMarketingLabel}.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <Button variant="outline" onClick={() => exportInputRef.current?.click()}>
                  <Upload className="w-4 h-4 mr-2" />
                  Upload Apple CSV
                </Button>
                {exportFile && <Badge variant="outline">{exportFile.name}</Badge>}
              </div>
              <Button
                className="w-full"
                onClick={handleExportAction}
                disabled={!exportFile || exportLoading}
              >
                {exportLoading ? (
                  <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                ) : (
                  <RefreshCw className="w-4 h-4 mr-2" />
                )}
                Export to {providerActionLabel}
              </Button>
              <SyncStatsView result={exportResult} providerLabel={providerActionLabel} />
            </section>

            <section className="rounded-lg border p-6 space-y-4">
              <div>
                <h3 className="text-lg font-semibold">Import Passwords</h3>
                <p className="text-sm text-muted-foreground">
                  Fetch new passwords from {providerMarketingLabel} and generate an Apple-ready CSV.
                </p>
              </div>
              <Button
                className="w-full"
                onClick={handleImportAction}
                disabled={importLoading}
              >
                {importLoading ? (
                  <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                ) : (
                  <Download className="w-4 h-4 mr-2" />
                )}
                Import from {providerActionLabel}
              </Button>
              <SyncStatsView result={importResult} providerLabel={providerActionLabel} />
            </section>
          </div>

          <section className="rounded-lg border p-6 space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-lg font-semibold">Sync history</h3>
                <p className="text-sm text-muted-foreground">Latest 10 manual syncs.</p>
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
                  const stats = (log.stats || {}) as { push?: any; pull?: any };
                  const pushStats = stats.push || {};
                  const pullStats = stats.pull || {};
                  return (
                    <div key={log.id} className="rounded-md border-l-4 bg-card/40 p-4" style={{
                      borderColor:
                        log.status === 'completed'
                          ? 'rgb(34 197 94)'
                          : log.status === 'failed'
                          ? 'rgb(239 68 68)'
                          : 'rgb(59 130 246)',
                    }}>
                      <div className="flex items-center justify-between">
                        <div>
                          <p className="font-medium">{log.operation}</p>
                          <p className="text-xs text-muted-foreground">{formatDate(log.started_at)}</p>
                        </div>
                        <Badge variant={log.status === 'completed' ? 'success' : log.status === 'failed' ? 'destructive' : 'default'}>
                          {log.status}
                        </Badge>
                      </div>
                      <p className="text-sm text-muted-foreground mt-1">{log.message}</p>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs mt-3">
                        {typeof pushStats.created === 'number' && (
                          <div>
                            <span className="text-muted-foreground">Created</span>
                            <p className="font-medium">{pushStats.created}</p>
                          </div>
                        )}
                        {typeof pushStats.skipped === 'number' && (
                          <div>
                            <span className="text-muted-foreground">Skipped</span>
                            <p className="font-medium">{pushStats.skipped}</p>
                          </div>
                        )}
                        {typeof pullStats.new_entries === 'number' && (
                          <div>
                            <span className="text-muted-foreground">New entries</span>
                            <p className="font-medium">{pullStats.new_entries}</p>
                          </div>
                        )}
                        {log.duration_seconds !== null && (
                          <div>
                            <span className="text-muted-foreground">Duration</span>
                            <p className="font-medium">{log.duration_seconds}s</p>
                          </div>
                        )}
                      </div>
                      {log.error_message && (
                        <p className="text-xs text-destructive mt-2">{log.error_message}</p>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

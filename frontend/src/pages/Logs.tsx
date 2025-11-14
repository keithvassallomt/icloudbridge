import { useState, useEffect, useRef } from 'react';
import { Terminal, Download, Trash2, Search, Filter } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import type { BadgeProps } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { useSyncStore } from '@/store/sync-store';

const LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR'] as const;
const SERVICES = ['notes', 'reminders', 'passwords', 'scheduler', 'api'] as const;

export default function Logs() {
  const { logs, clearLogs } = useSyncStore();
  const [searchTerm, setSearchTerm] = useState('');
  const [serviceFilter, setServiceFilter] = useState<string | null>(null);
  const [levelFilter, setLevelFilter] = useState<string | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [showFilters, setShowFilters] = useState(false);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const logsContainerRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when new logs arrive
  useEffect(() => {
    if (autoScroll && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, autoScroll]);

  // Filter logs based on search term, service, and level
  const filteredLogs = logs.filter((log) => {
    // Search filter
    if (searchTerm && !log.message.toLowerCase().includes(searchTerm.toLowerCase())) {
      return false;
    }

    // Service filter
    if (serviceFilter && log.service !== serviceFilter) {
      return false;
    }

    // Level filter
    if (levelFilter && log.level !== levelFilter) {
      return false;
    }

    return true;
  });

  const handleExport = () => {
    const logData = filteredLogs.map((log) => ({
      timestamp: log.timestamp,
      service: log.service,
      level: log.level,
      message: log.message,
    }));

    const blob = new Blob([JSON.stringify(logData, null, 2)], {
      type: 'application/json',
    });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `icloudbridge-logs-${new Date().toISOString()}.json`;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
  };

  const handleClearLogs = () => {
    if (confirm('Are you sure you want to clear all logs?')) {
      clearLogs();
    }
  };

  const getLevelBadgeVariant = (level: string): BadgeProps['variant'] => {
    switch (level) {
      case 'ERROR':
        return 'destructive';
      case 'WARNING':
        return 'warning';
      case 'INFO':
        return 'default';
      case 'DEBUG':
        return 'secondary';
      default:
        return 'outline';
    }
  };

  const formatTimestamp = (timestamp: string) => {
    const date = new Date(timestamp);
    const timeStr = date.toLocaleTimeString('en-US', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    const ms = date.getMilliseconds().toString().padStart(3, '0');
    return `${timeStr}.${ms}`;
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <Terminal className="w-8 h-8" />
            Real-Time Logs
          </h1>
          <p className="text-muted-foreground">
            View and filter all sync operations and system logs
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            onClick={() => setShowFilters(!showFilters)}
            variant="outline"
            size="sm"
          >
            <Filter className="w-4 h-4 mr-2" />
            Filters
          </Button>
          <Button onClick={handleExport} variant="outline" size="sm">
            <Download className="w-4 h-4 mr-2" />
            Export
          </Button>
          <Button onClick={handleClearLogs} variant="outline" size="sm">
            <Trash2 className="w-4 h-4 mr-2" />
            Clear
          </Button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Total Logs</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{logs.length}</div>
            <p className="text-xs text-muted-foreground">Last 100 entries</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Filtered</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{filteredLogs.length}</div>
            <p className="text-xs text-muted-foreground">Matching filters</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Errors</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-destructive">
              {logs.filter((l) => l.level === 'ERROR').length}
            </div>
            <p className="text-xs text-muted-foreground">Error level logs</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Warnings</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-yellow-600 dark:text-yellow-400">
              {logs.filter((l) => l.level === 'WARNING').length}
            </div>
            <p className="text-xs text-muted-foreground">Warning level logs</p>
          </CardContent>
        </Card>
      </div>

      {/* Filters Panel */}
      {showFilters && (
        <Card>
          <CardHeader>
            <CardTitle>Filters</CardTitle>
            <CardDescription>Filter logs by service, level, or search term</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Search */}
            <div className="space-y-2">
              <Label htmlFor="search">Search Logs</Label>
              <div className="relative">
                <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                <Input
                  id="search"
                  placeholder="Search log messages..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="pl-9"
                />
              </div>
            </div>

            {/* Service Filter */}
            <div className="space-y-2">
              <Label>Service</Label>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant={serviceFilter === null ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setServiceFilter(null)}
                >
                  All Services
                </Button>
                {SERVICES.map((service) => (
                  <Button
                    key={service}
                    variant={serviceFilter === service ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => setServiceFilter(service)}
                  >
                    {service}
                  </Button>
                ))}
              </div>
            </div>

            {/* Level Filter */}
            <div className="space-y-2">
              <Label>Log Level</Label>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant={levelFilter === null ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setLevelFilter(null)}
                >
                  All Levels
                </Button>
                {LOG_LEVELS.map((level) => (
                  <Button
                    key={level}
                    variant={levelFilter === level ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => setLevelFilter(level)}
                  >
                    {level}
                  </Button>
                ))}
              </div>
            </div>

            {/* Auto-scroll */}
            <div className="flex items-center justify-between pt-2 border-t">
              <div className="space-y-0.5">
                <Label htmlFor="auto-scroll">Auto-scroll</Label>
                <p className="text-sm text-muted-foreground">
                  Automatically scroll to new log entries
                </p>
              </div>
              <Switch
                id="auto-scroll"
                checked={autoScroll}
                onCheckedChange={setAutoScroll}
              />
            </div>
          </CardContent>
        </Card>
      )}

      {/* Logs Display */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>Log Stream</span>
            <div className="flex items-center gap-2 text-sm font-normal">
              <div className="flex items-center gap-1">
                <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                <span className="text-muted-foreground">Live</span>
              </div>
            </div>
          </CardTitle>
          <CardDescription>
            Real-time logs from all services • Updates automatically
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div
            ref={logsContainerRef}
            className="bg-black dark:bg-black rounded-lg p-4 h-[600px] overflow-y-auto font-mono text-sm"
          >
            {filteredLogs.length === 0 ? (
              <div className="flex items-center justify-center h-full text-muted-foreground">
                {logs.length === 0 ? (
                  <div className="text-center">
                    <Terminal className="w-12 h-12 mx-auto mb-2 opacity-50" />
                    <p>No logs yet</p>
                    <p className="text-xs mt-1">Logs will appear here as syncs run</p>
                  </div>
                ) : (
                  <div className="text-center">
                    <Filter className="w-12 h-12 mx-auto mb-2 opacity-50" />
                    <p>No logs match your filters</p>
                    <p className="text-xs mt-1">Try adjusting your search or filters</p>
                  </div>
                )}
              </div>
            ) : (
              <div className="space-y-1">
                {filteredLogs.map((log, idx) => (
                  <div
                    key={idx}
                    className="flex items-start gap-3 py-1 hover:bg-white/5 rounded px-2 -mx-2"
                  >
                    {/* Timestamp */}
                    <span className="text-gray-500 dark:text-gray-500 shrink-0 select-none">
                      {formatTimestamp(log.timestamp)}
                    </span>

                    {/* Level Badge */}
                    <Badge
                      variant={getLevelBadgeVariant(log.level)}
                      className="shrink-0 w-16 justify-center text-xs"
                    >
                      {log.level}
                    </Badge>

                    {/* Service Badge */}
                    <Badge variant="outline" className="shrink-0 text-xs">
                      {log.service}
                    </Badge>

                    {/* Message */}
                    <span
                      className="flex-1 text-gray-100 dark:text-gray-100"
                      style={{
                        color:
                          log.level === 'ERROR'
                            ? '#ef4444'
                            : log.level === 'WARNING'
                            ? '#eab308'
                            : undefined,
                      }}
                    >
                      {log.message}
                    </span>
                  </div>
                ))}
                <div ref={logsEndRef} />
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Legend */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Log Levels</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-4 text-sm">
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-gray-500" />
              <span className="text-muted-foreground">DEBUG - Detailed diagnostic information</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-blue-500" />
              <span className="text-muted-foreground">INFO - General informational messages</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-yellow-500" />
              <span className="text-muted-foreground">WARNING - Warning messages</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-full bg-red-500" />
              <span className="text-muted-foreground">ERROR - Error messages</span>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

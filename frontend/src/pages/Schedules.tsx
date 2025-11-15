import { useEffect, useState, useCallback } from 'react';
import { Clock, RefreshCw, Plus, Play, Pause, Trash2 } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import apiClient from '@/lib/api-client';
import { useSchedulesStore } from '@/store/schedules-store';
import { cn } from '@/lib/utils';
import type { Schedule, ScheduleCreate } from '@/types/api';

type ServiceKey = 'notes' | 'reminders' | 'photos';

const SERVICE_OPTIONS: { id: ServiceKey; label: string }[] = [
  { id: 'notes', label: 'Notes' },
  { id: 'reminders', label: 'Reminders' },
  { id: 'photos', label: 'Photos' },
];

export default function Schedules() {
  const {
    schedules,
    setSchedules,
    serviceFilter,
    setServiceFilter,
  } = useSchedulesStore();

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [showCreateForm, setShowCreateForm] = useState(false);

  // Form state
  const [formData, setFormData] = useState<Partial<ScheduleCreate>>({
    services: ['notes'],
    name: '',
    schedule_type: 'interval',
    interval_minutes: 60,
    cron_expression: '',
    config_json: {},
    enabled: true,
  });

  const loadSchedules = useCallback(async () => {
    try {
      setLoading(true);
      const data = await apiClient.getSchedules(serviceFilter || undefined);
      setSchedules(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load schedules');
    } finally {
      setLoading(false);
    }
  }, [serviceFilter, setSchedules]);

  useEffect(() => {
    loadSchedules();
  }, [loadSchedules]);

  const handleCreate = async () => {
    try {
      if (!formData.services || formData.services.length === 0) {
        setError('Select at least one item to sync');
        return;
      }
      setLoading(true);
      setError(null);

      const schedule = await apiClient.createSchedule(formData as ScheduleCreate);
      setSuccess(`Schedule "${schedule.name}" created successfully`);
      setShowCreateForm(false);
      resetForm();
      await loadSchedules();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create schedule');
    } finally {
      setLoading(false);
    }
  };

  const handleToggle = async (schedule: Schedule) => {
    try {
      await apiClient.toggleSchedule(schedule.id);
      setSuccess(`Schedule "${schedule.name}" ${schedule.enabled ? 'disabled' : 'enabled'}`);
      await loadSchedules();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to toggle schedule');
    }
  };

  const handleRun = async (schedule: Schedule) => {
    try {
      await apiClient.runSchedule(schedule.id);
      setSuccess(`Schedule "${schedule.name}" triggered successfully`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to run schedule');
    }
  };

  const handleDelete = async (schedule: Schedule) => {
    if (!confirm(`Are you sure you want to delete schedule "${schedule.name}"?`)) {
      return;
    }

    try {
      await apiClient.deleteSchedule(schedule.id);
      setSuccess(`Schedule "${schedule.name}" deleted successfully`);
      await loadSchedules();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete schedule');
    }
  };

  const resetForm = () => {
    setFormData({
      services: ['notes'],
      name: '',
      schedule_type: 'interval',
      interval_minutes: 60,
      cron_expression: '',
      config_json: {},
      enabled: true,
    });
  };

  const toggleService = (serviceId: ServiceKey) => {
    const selected = formData.services ?? [];
    const exists = selected.includes(serviceId);
    const next = exists ? selected.filter((s) => s !== serviceId) : [...selected, serviceId];
    setFormData({ ...formData, services: next });
  };

  const isServiceSelected = (serviceId: ServiceKey) => (formData.services ?? []).includes(serviceId);

  const formatDate = (dateStr: string | undefined) => {
    if (!dateStr) return 'Never';
    return new Date(dateStr).toLocaleString();
  };

  const getScheduleDescription = (schedule: Schedule) => {
    if (schedule.schedule_type === 'interval') {
      return `Every ${schedule.interval_minutes} minutes`;
    } else {
      return `Cron: ${schedule.cron_expression}`;
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <Clock className="w-8 h-8" />
            Schedules
          </h1>
          <p className="text-muted-foreground">
            Manage automated sync schedules
          </p>
        </div>
        <div className="flex gap-2">
          <Button onClick={loadSchedules} variant="outline" disabled={loading}>
            <RefreshCw className={`w-4 h-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
          <Button onClick={() => setShowCreateForm(true)}>
            <Plus className="w-4 h-4 mr-2" />
            New Schedule
          </Button>
        </div>
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

      {/* Filter */}
      <Card>
        <CardHeader>
          <CardTitle>Filter Schedules</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex gap-2">
            <Button
              variant={serviceFilter === null ? 'default' : 'outline'}
              onClick={() => setServiceFilter(null)}
              size="sm"
            >
              All Services
            </Button>
            <Button
              variant={serviceFilter === 'notes' ? 'default' : 'outline'}
              onClick={() => setServiceFilter('notes')}
              size="sm"
            >
              Notes
            </Button>
            <Button
              variant={serviceFilter === 'reminders' ? 'default' : 'outline'}
              onClick={() => setServiceFilter('reminders')}
              size="sm"
            >
              Reminders
            </Button>
            <Button
              variant={serviceFilter === 'photos' ? 'default' : 'outline'}
              onClick={() => setServiceFilter('photos')}
              size="sm"
            >
              Photos
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Create Form */}
      {showCreateForm && (
        <Card>
          <CardHeader>
            <CardTitle>Create New Schedule</CardTitle>
            <CardDescription>Configure a new automated sync schedule</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="name">Schedule Name</Label>
                <Input
                  id="name"
                  placeholder="e.g., Daily Sync"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                />
              </div>
            </div>

            <div className="space-y-2">
              <Label>Items to Sync</Label>
              <div className="flex flex-wrap gap-2">
                {SERVICE_OPTIONS.map((option) => (
                  <Button
                    key={option.id}
                    type="button"
                    variant={isServiceSelected(option.id) ? 'default' : 'outline'}
                    onClick={() => toggleService(option.id)}
                    size="sm"
                    aria-pressed={isServiceSelected(option.id)}
                  >
                    {option.label}
                  </Button>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                Select one or more data sources for this schedule.
              </p>
            </div>

            <div className="space-y-2">
              <Label>Schedule Type</Label>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant={formData.schedule_type === 'interval' ? 'default' : 'outline'}
                  onClick={() => setFormData({ ...formData, schedule_type: 'interval' })}
                  className="flex-1"
                >
                  Interval
                </Button>
                <Button
                  type="button"
                  variant={formData.schedule_type === 'datetime' ? 'default' : 'outline'}
                  onClick={() => setFormData({ ...formData, schedule_type: 'datetime' })}
                  className="flex-1"
                >
                  Cron
                </Button>
              </div>
            </div>

            {formData.schedule_type === 'interval' ? (
              <div className="space-y-2">
                <Label htmlFor="interval">Interval (minutes)</Label>
                <Input
                  id="interval"
                  type="number"
                  min="1"
                  value={formData.interval_minutes}
                  onChange={(e) => setFormData({ ...formData, interval_minutes: parseInt(e.target.value) })}
                />
              </div>
            ) : (
              <div className="space-y-2">
                <Label htmlFor="cron">Cron Expression</Label>
                <Input
                  id="cron"
                  placeholder="0 */6 * * *"
                  value={formData.cron_expression}
                  onChange={(e) => setFormData({ ...formData, cron_expression: e.target.value })}
                />
                <p className="text-xs text-muted-foreground">
                  Example: "0 */6 * * *" = Every 6 hours
                </p>
              </div>
            )}

            <div className="flex items-center space-x-2">
              <Switch
                id="enabled"
                checked={formData.enabled}
                onCheckedChange={(checked) => setFormData({ ...formData, enabled: checked })}
              />
              <Label htmlFor="enabled">Enable schedule</Label>
            </div>

            <div className="flex gap-2">
              <Button onClick={handleCreate} disabled={!formData.name || loading}>
                Create Schedule
              </Button>
              <Button
                onClick={() => {
                  setShowCreateForm(false);
                  resetForm();
                }}
                variant="outline"
              >
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Schedules List */}
      <div className="space-y-4">
        {loading && schedules.length === 0 ? (
          <Card>
            <CardContent className="py-8">
              <div className="text-center text-muted-foreground">Loading...</div>
            </CardContent>
          </Card>
        ) : schedules.length === 0 ? (
          <Card>
            <CardContent className="py-8">
              <div className="text-center text-muted-foreground">
                No schedules found. Create one to get started.
              </div>
            </CardContent>
          </Card>
        ) : (
          schedules.map((schedule) => (
            <Card
              key={schedule.id}
              className={cn(
                !schedule.enabled &&
                  'border-yellow-400/80 bg-yellow-50/70 dark:border-yellow-500/70 dark:bg-yellow-900/20'
              )}
            >
              <CardHeader>
                <div className="flex items-start justify-between">
                  <div>
                    <CardTitle className="flex items-center gap-2 flex-wrap">
                      {schedule.name}
                      <Badge variant={schedule.enabled ? 'success' : 'secondary'}>
                        {schedule.enabled ? 'Enabled' : 'Disabled'}
                      </Badge>
                      {(schedule.services?.length ? schedule.services : [schedule.service])
                        .filter((svc): svc is string => Boolean(svc))
                        .map((svc) => (
                          <Badge key={`${schedule.id}-${svc}`} variant="outline">
                            {svc}
                          </Badge>
                        ))}
                    </CardTitle>
                    <CardDescription>{getScheduleDescription(schedule)}</CardDescription>
                  </div>
                  <div className="flex gap-1">
                    <Button
                      size="icon"
                      variant="outline"
                      onClick={() => handleRun(schedule)}
                      title="Run now"
                    >
                      <Play className="w-4 h-4" />
                    </Button>
                    <Button
                      size="icon"
                      variant="outline"
                      onClick={() => handleToggle(schedule)}
                      title={schedule.enabled ? 'Disable' : 'Enable'}
                      className={cn(
                        !schedule.enabled &&
                          'border-yellow-400/80 text-yellow-700 bg-yellow-50/90 dark:text-yellow-200 dark:border-yellow-500/80 dark:bg-transparent'
                      )}
                    >
                      <Pause className="w-4 h-4" />
                    </Button>
                    <Button
                      size="icon"
                      variant="outline"
                      onClick={() => handleDelete(schedule)}
                      title="Delete"
                    >
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <span className="text-muted-foreground">Last run:</span>{' '}
                    <span>{formatDate(schedule.last_run)}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Next run:</span>{' '}
                    <span>{formatDate(schedule.next_run)}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Created:</span>{' '}
                    <span>{formatDate(schedule.created_at)}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Updated:</span>{' '}
                    <span>{formatDate(schedule.updated_at)}</span>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))
        )}
      </div>
    </div>
  );
}

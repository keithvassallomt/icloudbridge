import { Link } from 'react-router-dom';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';

interface ServiceDisabledNoticeProps {
  serviceName: string;
}

export function ServiceDisabledNotice({ serviceName }: ServiceDisabledNoticeProps) {
  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{serviceName} sync is disabled</CardTitle>
          <CardDescription>
            Enable {serviceName} sync in Settings to configure calendars or run jobs.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Head to the Settings page to turn {serviceName} sync back on. Once enabled, this screen will let you manage
            mappings, simulate runs, and trigger syncs.
          </p>
          <Button asChild>
            <Link to="/settings">Go to Settings</Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

export default ServiceDisabledNotice;

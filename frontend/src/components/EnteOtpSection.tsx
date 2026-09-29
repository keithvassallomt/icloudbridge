import { lazy, Suspense, useState } from 'react';
import { AlertTriangle, Check, ChevronDown, ChevronRight, Copy, KeyRound, QrCode, Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import apiClient from '@/lib/api-client';
import type { EnteOtpMatch, EnteOtpNamed, EnteOtpPreview, EnteOtpSkipped } from '@/types/api';

const SetupQr = lazy(() =>
  import('qrcode.react').then((mod) => ({ default: mod.QRCodeSVG })),
);

function maskSetupKey(setupKey: string): string {
  if (setupKey.length <= 8) {
    return '•'.repeat(setupKey.length);
  }
  const hidden = Math.min(12, setupKey.length - 8);
  return `${setupKey.slice(0, 4)}${'•'.repeat(hidden)}${setupKey.slice(-4)}`;
}

function rowKey(parts: string[]): string {
  return parts.join('\u0000');
}

// The settings that differ from the SHA1, 6 digits, 30 seconds a bare setup key implies.
function unusualSettings(item: EnteOtpMatch): string {
  const parts: string[] = [];
  if (item.digits !== 6) {
    parts.push(`${item.digits} digits`);
  }
  if (item.period !== 30) {
    parts.push(`a ${item.period}-second period`);
  }
  if (item.algorithm !== 'SHA1') {
    parts.push(item.algorithm);
  }
  return parts.join(' and ');
}

function ResultGroup({
  title,
  items,
}: {
  title: string;
  items: { key: string; primary: string; secondary?: string }[];
}) {
  const [open, setOpen] = useState(false);
  if (items.length === 0) {
    return null;
  }
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div className="rounded-lg border bg-background">
        <CollapsibleTrigger className="flex w-full items-center justify-between p-3 hover:bg-accent/50 transition-colors">
          <div className="flex items-center gap-2">
            {open ? (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-4 w-4 text-muted-foreground" />
            )}
            <span className="font-semibold text-sm">{title}</span>
          </div>
          <span className="text-sm text-muted-foreground">{items.length}</span>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="px-3 pb-3">
            <ul className="space-y-1">
              {items.map((item) => (
                <li key={item.key} className="text-xs p-1.5 rounded hover:bg-muted/50">
                  <div className="font-medium truncate">{item.primary}</div>
                  {item.secondary && (
                    <div className="text-muted-foreground text-[10px] truncate">{item.secondary}</div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}

function namedRows(items: EnteOtpNamed[], prefix: string) {
  return items.map((item, index) => ({
    key: rowKey([prefix, item.issuer, item.account, item.title ?? '', String(index)]),
    primary: item.title ? `${item.title}` : item.issuer,
    secondary: [item.username, item.account && item.account !== item.username ? item.account : '']
      .filter(Boolean)
      .join(' · '),
  }));
}

function skippedRows(items: EnteOtpSkipped[]) {
  return items.map((item, index) => ({
    key: rowKey(['skipped', item.label, item.reason, String(index)]),
    primary: item.label,
    secondary: item.reason,
  }));
}

export default function EnteOtpSection() {
  const [open, setOpen] = useState(false);
  const [appleFile, setAppleFile] = useState<File | null>(null);
  const [enteFile, setEnteFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<EnteOtpPreview | null>(null);
  const [done, setDone] = useState<Set<string>>(new Set());
  const [revealed, setRevealed] = useState<Set<string>>(new Set());
  const [copied, setCopied] = useState<string | null>(null);

  const previewCodes = async () => {
    if (!appleFile || !enteFile) {
      return;
    }
    setLoading(true);
    setError(null);
    setDone(new Set());
    setRevealed(new Set());
    try {
      const result = await apiClient.passwordsEntePreview(appleFile, enteFile);
      setPreview(result);
    } catch (err) {
      setPreview(null);
      setError(err instanceof Error ? err.message : 'Preview failed');
    } finally {
      setLoading(false);
    }
  };

  const toggleDone = (key: string) => {
    setDone((current) => {
      const next = new Set(current);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const toggleQr = (key: string) => {
    setRevealed((current) => {
      const next = new Set(current);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const copyKey = async (key: string, setupKey: string) => {
    try {
      await navigator.clipboard.writeText(setupKey);
      setCopied(key);
      window.setTimeout(() => {
        setCopied((current) => (current === key ? null : current));
      }, 2000);
    } catch {
      setError('Could not copy the setup key.');
    }
  };

  const toCheck = preview ? preview.matched.filter((item) => item.account_differs).length : 0;

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <section className="rounded-lg border p-6 space-y-4">
        <CollapsibleTrigger className="flex w-full items-start gap-2 text-left">
          {open ? (
            <ChevronDown className="h-5 w-5 mt-1 text-muted-foreground shrink-0" />
          ) : (
            <ChevronRight className="h-5 w-5 mt-1 text-muted-foreground shrink-0" />
          )}
          <div>
            <h3 className="text-lg font-semibold flex items-center gap-2">
              <KeyRound className="w-4 h-4" />
              Verification codes from Ente Auth
            </h3>
            <p className="text-sm text-muted-foreground">
              Find which Apple logins your Ente Auth codes belong to, and get the setup key for each one.
            </p>
          </div>
        </CollapsibleTrigger>

        <CollapsibleContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Apple Passwords doesn't let apps add a verification code to a login, so you add each one
            yourself. This matches a plain-text Ente Auth export to an Apple Passwords export and lists
            the setup key for each login.
          </p>

          <ol className="text-sm text-muted-foreground list-decimal pl-5 space-y-1">
            <li>In Ente Auth, choose Settings &gt; Data &gt; Export codes &gt; Plain text.</li>
            <li>In Apple Passwords, choose File &gt; Export All Passwords to File.</li>
            <li>Upload both files here and choose Preview.</li>
            <li>
              For each match, open the login in Apple Passwords, choose Edit &gt; Set Up
              Verification Code &gt; Enter Setup Key, and paste the key. You can scan its QR code instead.
            </li>
            <li>
              When you're done, delete both export files. Between them they hold every password and
              verification code in plain text.
            </li>
          </ol>

          <div className="flex flex-wrap items-center gap-3">
            <label className="inline-flex">
              <input
                type="file"
                accept=".csv,text/csv"
                className="sr-only"
                onChange={(event) => {
                  setAppleFile(event.target.files?.[0] ?? null);
                  setPreview(null);
                  event.target.value = '';
                }}
              />
              <span className="inline-flex h-10 items-center rounded-md border border-input bg-background px-4 text-sm font-medium hover:bg-accent hover:text-accent-foreground cursor-pointer">
                <Upload className="w-4 h-4 mr-2" />
                Apple Passwords CSV
              </span>
            </label>
            {appleFile && <Badge variant="outline">{appleFile.name}</Badge>}
            <label className="inline-flex">
              <input
                type="file"
                accept=".txt,text/plain"
                className="sr-only"
                onChange={(event) => {
                  setEnteFile(event.target.files?.[0] ?? null);
                  setPreview(null);
                  event.target.value = '';
                }}
              />
              <span className="inline-flex h-10 items-center rounded-md border border-input bg-background px-4 text-sm font-medium hover:bg-accent hover:text-accent-foreground cursor-pointer">
                <Upload className="w-4 h-4 mr-2" />
                Ente Auth export
              </span>
            </label>
            {enteFile && <Badge variant="outline">{enteFile.name}</Badge>}
          </div>

          <Button onClick={previewCodes} disabled={!appleFile || !enteFile || loading}>
            {loading ? 'Matching…' : 'Preview'}
          </Button>

          {error && <p className="text-sm text-destructive">{error}</p>}

          {preview && (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                {preview.matched.length} to enter
                {toCheck > 0 ? ` (${toCheck} to check first)` : ''}
                {preview.already_set.length > 0 ? `, ${preview.already_set.length} already set` : ''}
                {preview.ambiguous.length > 0
                  ? `, ${preview.ambiguous.length} ${preview.ambiguous.length === 1 ? 'needs' : 'need'} a choice`
                  : ''}
                {preview.conflict.length > 0
                  ? `, ${preview.conflict.length} ${preview.conflict.length === 1 ? 'conflict' : 'conflicts'}`
                  : ''}
                {preview.unmatched.length > 0 ? `, ${preview.unmatched.length} unmatched` : ''}
              </p>

              {preview.matched.length === 0 ? (
                <p className="text-sm text-muted-foreground">No logins are waiting for a setup key.</p>
              ) : (
                <ul className="space-y-3">
                  {preview.matched.map((item) => {
                    const key = rowKey([item.title, item.username, item.issuer, item.account, item.setup_key]);
                    const isDone = done.has(key);
                    const showQr = item.qr_only || revealed.has(key);
                    const enteLabel = item.account ? `${item.issuer} (${item.account})` : item.issuer;
                    return (
                      <li key={key} className="rounded-md border p-3 space-y-2">
                        <div className="flex items-start gap-3">
                          <input
                            type="checkbox"
                            className="mt-1"
                            checked={isDone}
                            onChange={() => toggleDone(key)}
                            aria-label={`Mark ${item.title} done`}
                          />
                          <div className="min-w-0 flex-1">
                            <p className={`font-medium text-sm truncate ${isDone ? 'line-through text-muted-foreground' : ''}`}>
                              {item.title}
                            </p>
                            <p className="text-xs text-muted-foreground truncate">
                              {item.username} · Ente: {enteLabel}
                            </p>
                          </div>
                          {isDone && <Check className="w-4 h-4 text-green-600" />}
                        </div>
                        {item.account_differs && (
                          <p className="flex items-start gap-1.5 pl-7 text-xs text-amber-700 dark:text-amber-400">
                            <AlertTriangle className="w-3.5 h-3.5 mt-px shrink-0" />
                            Ente has this code for {item.account}, but this login's username is{' '}
                            {item.username}. Check it's the same account before you add it.
                          </p>
                        )}
                        {item.qr_only && (
                          <p className="flex items-start gap-1.5 pl-7 text-xs text-muted-foreground">
                            <QrCode className="w-3.5 h-3.5 mt-px shrink-0" />
                            This code uses {unusualSettings(item)}. Scan the QR code to add it: a setup
                            key on its own doesn't carry these settings, so it would give the wrong codes.
                          </p>
                        )}
                        {!item.qr_only && (
                          <div className="flex flex-wrap items-center gap-2 pl-7">
                            <code className="text-xs bg-muted px-2 py-1 rounded">{maskSetupKey(item.setup_key)}</code>
                            <Button type="button" variant="outline" size="sm" onClick={() => copyKey(key, item.setup_key)}>
                              <Copy className="w-3 h-3 mr-1" />
                              {copied === key ? 'Copied' : 'Copy setup key'}
                            </Button>
                            <Button type="button" variant="ghost" size="sm" onClick={() => toggleQr(key)}>
                              <QrCode className="w-3 h-3 mr-1" />
                              {showQr ? 'Hide QR' : 'Show QR'}
                            </Button>
                          </div>
                        )}
                        {showQr && (
                          <div className="pl-7">
                            <Suspense fallback={<p className="text-xs text-muted-foreground">Loading QR…</p>}>
                              <SetupQr value={item.otpauth_uri} size={160} includeMargin />
                            </Suspense>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}

              <ResultGroup
                title="Needs a choice"
                items={preview.ambiguous.map((item, index) => ({
                  key: rowKey(['ambiguous', item.issuer, item.account, String(index)]),
                  primary: item.issuer,
                  secondary: item.candidates.map((candidate) => candidate.title).join(', '),
                }))}
              />
              <ResultGroup title="Already set" items={namedRows(preview.already_set, 'set')} />
              <ResultGroup
                title="Different code already saved"
                items={namedRows(preview.conflict, 'conflict')}
              />
              <ResultGroup title="No matching login" items={namedRows(preview.unmatched, 'unmatched')} />
              <ResultGroup title="Skipped in Ente" items={skippedRows(preview.skipped)} />
            </div>
          )}
        </CollapsibleContent>
      </section>
    </Collapsible>
  );
}

#!/bin/bash
# iCloudBridge Ruby runtime diagnostics.
# Run on the affected machine WHILE the setup screen is stuck, if possible:
#   bash diagnose_ruby_runtime.sh > icloudbridge-diag.txt 2>&1
# Then send icloudbridge-diag.txt. Read-only: changes nothing.

echo "=== iCloudBridge Ruby runtime diagnostics ==="
date
sw_vers 2>/dev/null | tr '\n' ' '; echo; uname -m; echo

RT="$HOME/Library/iCloudBridge/Runtime/Ruby"
LEGACY="$HOME/Library/Application Support/iCloudBridge/gems"

section() { echo; echo "----- $1 -----"; }

section "1. Is the app running, and is it stuck?"
# Match on the executable's own name (field 5 = comm), so the diagnostic
# script's own command line and unrelated daemons do not show up as hits.
ps -Ao pid,etime,stat,%cpu,comm | awk 'NR==1 || $5 ~ /iCloudBridge/' \
  | grep -v diagnose_ruby || echo "(app not running)"
echo
echo "Ruby-side subprocesses (a live one means it is working, not hung):"
found=$(ps -Ao pid,etime,stat,%cpu,comm \
  | awk '$5 ~ /\/(ruby|gem|bundler?|make|cc1|cc1plus|clang|ld)$/ {print}')
if [ -n "$found" ]; then
  echo "$found"
  echo "^ something IS running: the installer is working, just slowly."
else
  echo "(none - no Ruby-side work in progress)"
fi

section "2. Where is it stuck? (stack sample of the menubar app)"
APP_PID=$(pgrep -f "iCloudBridge.app/Contents/MacOS/iCloudBridgeMenubar" | head -1)
if [ -n "$APP_PID" ]; then
  echo "sampling pid $APP_PID for 3s..."
  sample "$APP_PID" 3 -file /tmp/icb-sample.txt >/dev/null 2>&1
  if [ -s /tmp/icb-sample.txt ]; then
    # A beachball means the MAIN thread is blocked, so always print its full
    # stack rather than filtering for keywords that may not appear.
    echo "-- MAIN THREAD (a beachball is always here) --"
    awk '/^ *[0-9]+ Thread_/{p = /main-thread/} p' /tmp/icb-sample.txt | head -60
    echo
    echo "-- every other thread, top frames --"
    awk '/^ *[0-9]+ Thread_/{t=$0; n=0} {if(t && n<6){print; n++}}' /tmp/icb-sample.txt \
      | grep -v main-thread | head -70
    echo
    echo "-- blocking calls anywhere in the sample --"
    grep -nE "waitUntilExit|readDataToEndOfFile|removeItemAt|NSTask|_wait4|__select|semaphore_wait|dispatch_sync|__psynch" \
      /tmp/icb-sample.txt | head -20
    echo "(full sample: /tmp/icb-sample.txt)"
  else
    echo "(sample produced nothing; try: sudo sample $APP_PID 3)"
  fi
else
  echo "(app not running - cannot sample)"
fi

section "3. Installer logs"
for d in "$TMPDIR" /tmp; do
  L="$d/icloudbridge-preflight-logs"
  [ -d "$L" ] && { echo "log dir: $L"; ls -la "$L"; }
done
# Both logs matter: preflight only finishes when Python AND Ruby succeed, so a
# healthy Ruby install still leaves the setup window up if Python failed.
for d in "$TMPDIR" /tmp; do
  for f in "$d"/icloudbridge-preflight-logs/*.log; do
    [ -f "$f" ] || continue
    echo; echo "--- tail of $f ---"
    tail -80 "$f"
    echo "--- errors in $f ---"
    grep -inE "error|failed|fatal|traceback|no such|denied|not found|abort" "$f" | tail -25 \
      || echo "(no error lines)"
  done
done

section "3b. Python venv state (preflight needs this too)"
VENV="$HOME/Library/Application Support/iCloudBridge/venv"
if [ -d "$VENV" ]; then
  echo "venv: $VENV"
  ls -la "$VENV/bin/python3" 2>/dev/null || echo "  bin/python3 MISSING"
  echo "  .fingerprint: $([ -f "$VENV/.fingerprint" ] && echo present || echo ABSENT)"
  echo "  pyvenv.cfg executable: $(grep -i '^executable' "$VENV/pyvenv.cfg" 2>/dev/null)"
  echo "  base interpreter still installed? $(grep -i '^executable' "$VENV/pyvenv.cfg" 2>/dev/null | sed 's/.*= *//' | xargs -I{} sh -c '[ -x "{}" ] && echo yes || echo NO - venv is stale')"
  echo "  can it import the backend?"
  "$VENV/bin/python3" -c "import icloudbridge, sys; print('   ok', icloudbridge.__file__)" 2>&1 | tail -5
else
  echo "(no venv at $VENV - Python install never completed)"
fi
echo
echo "Homebrew python present?"
ls -la /opt/homebrew/opt/python@3.12/bin/python3* 2>/dev/null || echo "  MISSING - preflight will block on Python"

section "4. Managed runtime state"
if [ -d "$RT" ]; then
  echo "root: $RT"
  du -sh "$RT" 2>/dev/null
  echo "files: $(find "$RT" -type f 2>/dev/null | wc -l | tr -d ' ')"
  echo "markers:"
  for m in .fingerprint .layout .bundler-version; do
    [ -f "$RT/$m" ] && echo "  $m = $(cat "$RT/$m" | head -c 300)" || echo "  $m = (absent)"
  done
  echo "bin/:"; ls -la "$RT/bin" 2>/dev/null || echo "  (no bin dir)"
  echo "installed gems:"; ls "$RT/gems/gems" 2>/dev/null | head -40 || echo "  (none)"
else
  echo "(no managed runtime at $RT)"
fi
echo
echo "legacy tree: $LEGACY"
[ -d "$LEGACY" ] && du -sh "$LEGACY" 2>/dev/null || echo "  (absent - already cleaned up)"

section "5. Interpreters"
echo "PATH ruby : $(command -v ruby)  ->  $(ruby --version 2>&1 | head -1)"
for r in /opt/homebrew/opt/ruby/bin/ruby /usr/bin/ruby; do
  [ -x "$r" ] && echo "$r  ->  $($r --version 2>&1 | head -1)"
done
echo "brew ruby bin contents:"; ls /opt/homebrew/opt/ruby/bin 2>/dev/null | tr '\n' ' '; echo

section "6. Can the runtime actually start a bundled Ruby?"
GEMFILE="/Applications/iCloudBridge.app/Contents/Resources/ruby_deps/Gemfile"
[ -f "$GEMFILE" ] || GEMFILE="$(ls -d /Applications/iCloudBridge.app/Contents/Resources/ruby_deps/Gemfile 2>/dev/null)"
VER=$(cat "$RT/.bundler-version" 2>/dev/null)
if [ -x "$RT/bin/bundle" ] && [ -f "$GEMFILE" ]; then
  echo "using bundle=$RT/bin/bundle version=${VER:-<none>} gemfile=$GEMFILE"
  env -i HOME="$HOME" PATH="/opt/homebrew/opt/ruby/bin:/usr/bin:/bin" RUBYOPT="" \
    GEM_HOME="$RT/gems" GEM_PATH="$RT/gems" BUNDLE_PATH="$RT/gems" \
    BUNDLE_APP_CONFIG="$RT/.bundle" BUNDLE_GEMFILE="$GEMFILE" \
    "$RT/bin/bundle" ${VER:+_${VER}_} exec /opt/homebrew/opt/ruby/bin/ruby \
    -e 'puts "ruby="+RUBY_VERSION; puts "rubygems="+Gem::VERSION; puts "bundler="+Bundler::VERSION; puts "RUBYOPT="+ENV["RUBYOPT"].to_s.inspect' 2>&1 | head -15
  echo "exit=$?"
else
  echo "(cannot self-test: bundle or Gemfile missing)"
fi

section "7. Backend log tail"
tail -40 "$HOME/Library/Logs/iCloudBridge/backend.log" 2>/dev/null || echo "(no backend log)"

section "8. Why has the backend not started?"
echo "-- is anything listening on the backend port? --"
lsof -nP -iTCP:27731 -sTCP:LISTEN 2>/dev/null || echo "(nothing listening on 27731)"
echo
echo "-- essential permission states (written by preflight) --"
cat "$HOME/.icloudbridge/permissions.json" 2>/dev/null || echo "(no permissions.json - preflight never got that far)"
echo
echo "-- the app's own log messages (this says what it decided) --"
# The app's own NSLog lines carry no Apple subsystem; excluding those strips
# out the CFNetwork/nw_connection chatter that otherwise buries them.
log show --last 45m --style compact \
  --predicate 'process == "iCloudBridgeMenubar" AND NOT (subsystem BEGINSWITH "com.apple")' \
  2>/dev/null | tail -50 || echo "(could not read unified log)"
echo
echo "-- anything mentioning backend/venv/ruby/preflight, whatever the subsystem --"
log show --last 45m --style compact \
  --predicate 'process == "iCloudBridgeMenubar"' 2>/dev/null \
  | grep -iE "backend|venv|python|ruby|bundle|preflight|launch|exited|restart|giving up|not found|failed" \
  | grep -viE "nw_|CFNetwork|com.apple.network|boringssl|quic|tcp" | tail -40 \
  || echo "(nothing matched)"

section "9. Backend launch test  [only with --try-backend]"
if [ "$1" = "--try-backend" ]; then
  VENVPY="$HOME/Library/Application Support/iCloudBridge/venv/bin/python3"
  BSRC="/Applications/iCloudBridge.app/Contents/Resources/backend_src"
  RT2="$HOME/Library/iCloudBridge/Runtime/Ruby"
  if [ -x "$VENVPY" ] && [ -d "$BSRC" ]; then
    echo "launching the backend the same way the app does, for 15s..."
    PYTHONPATH="$BSRC" \
    ICLOUDBRIDGE_VENV_PYTHON="$VENVPY" \
    ICLOUDBRIDGE_BUNDLE_PATH="$RT2/bin/bundle" \
    ICLOUDBRIDGE_RUBY_PATH="/opt/homebrew/opt/ruby/bin/ruby" \
    GEM_HOME="$RT2/gems" GEM_PATH="$RT2/gems" BUNDLE_PATH="$RT2/gems" \
    BUNDLE_APP_CONFIG="$RT2/.bundle" \
    "$VENVPY" -m icloudbridge.scripts.menubar_backend > /tmp/icb-backend-try.log 2>&1 &
    BPID=$!
    sleep 15
    kill "$BPID" 2>/dev/null
    wait "$BPID" 2>/dev/null
    echo "--- output ---"
    tail -40 /tmp/icb-backend-try.log
  else
    echo "(cannot test: venv python or backend_src missing)"
  fi
else
  echo "(skipped - re-run as: bash diagnose_ruby_runtime.sh --try-backend)"
fi

echo
echo "=== end of diagnostics ==="

#!/bin/bash
# Start the overnight QA-generation run, fully detached, plus the dashboard.
#
# Detaching matters and is the whole point of this file. A plain `cmd &` (or
# a background job started from an agent session) stays in the caller's
# process group and dies with it -- that is how the first overnight attempt
# was lost. `os.setsid()` puts the driver in a brand-new session with no
# controlling terminal, so it outlives the shell, the terminal window, and
# the Claude Code session.
#
# `caffeinate -ims` holds the machine awake for the duration:
#     -i  no idle sleep      -m  no disk sleep      -s  no system sleep (on AC)
# Closing a laptop lid still sleeps regardless of caffeinate, so leave the
# lid open and the machine on mains power.
#
#   bash qa_generation/monitor/run_overnight_start.sh
#   bash qa_generation/monitor/run_overnight_start.sh --status
#   bash qa_generation/monitor/run_overnight_start.sh --stop

set -u
MON="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # qa_generation/monitor/
QA="$(cd "$MON/.." && pwd)"
PY=/opt/miniconda3/envs/ai/bin/python
PIDFILE="$QA/output/overnight.pid"
DASH_PIDFILE="$QA/output/dashboard.pid"

running() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

case "${1:-start}" in
  --status)
    running "$PIDFILE"      && echo "generation: RUNNING (pid $(cat "$PIDFILE"))" \
                            || echo "generation: not running"
    running "$DASH_PIDFILE" && echo "dashboard:  RUNNING (pid $(cat "$DASH_PIDFILE")) http://127.0.0.1:8765" \
                            || echo "dashboard:  not running"
    # macOS pgrep has no -c (that is procps/Linux), and it fails loudly
    # rather than returning a count -- which read as a plausible "0" and
    # made a healthy run look dead. Count lines instead.
    count() { pgrep -f "$1" 2>/dev/null | wc -l | tr -d ' '; }
    echo "workers:    $(count generate_qa_openrouter)"
    echo "caffeinate: $(count 'caffeinate -ims')"
    echo "awake:      $(pmset -g assertions 2>/dev/null | awk '/PreventSystemSleep/{print ($2==1?"yes, sleep held off":"NO - machine may sleep"); exit}')"
    latest=$(ls -t "$QA"/output/overnight_*.log 2>/dev/null | head -1)
    [ -n "${latest:-}" ] && { echo "log:        $latest"; tail -5 "$latest"; }
    exit 0
    ;;
  --stop)
    for f in "$PIDFILE" "$DASH_PIDFILE"; do
      if running "$f"; then
        pid=$(cat "$f")
        # Negative pid signals the whole session/group, so caffeinate and
        # the python child go down with the driver rather than being orphaned.
        kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
        echo "stopped $(basename "$f") (pid $pid)"
      fi
      rm -f "$f"
    done
    pkill -f generate_qa_openrouter 2>/dev/null
    echo "stopped. Progress is saved; re-run this script to resume."
    exit 0
    ;;
esac

if running "$PIDFILE"; then
  echo "Already running (pid $(cat "$PIDFILE")). Use --status or --stop."
  exit 1
fi

mkdir -p "$QA/output"

# setsid via python: macOS has no setsid(1).
#
# Forks so the parent can print the *detached* pid and exit. An earlier
# version backgrounded this with `&` and recorded `$!`, which was the
# transient wrapper shell, not the job -- that pid dies with the calling
# session, so --status would report "not running" for a perfectly healthy
# run and --stop would fail to stop it. The forked child is a session
# leader, so `kill -TERM -$pid` in --stop reaches caffeinate and the
# generator together.
#
#   detach <logfile> <command...>   ->  echoes the detached pid
detach() {
  "$PY" - "$@" <<'PYEOF'
import os, sys

logfile, argv = sys.argv[1], sys.argv[2:]
pid = os.fork()
if pid > 0:
    print(pid)
    sys.exit(0)
os.setsid()
fd = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(fd, 1)
os.dup2(fd, 2)
os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
os.execvp(argv[0], argv)
PYEOF
}

detach "$QA/output/overnight_driver.out" \
       caffeinate -ims /bin/bash "$MON/run_overnight.sh" > "$PIDFILE"

if ! curl -sf -o /dev/null --max-time 2 http://127.0.0.1:8765/ 2>/dev/null; then
  detach "$QA/output/dashboard.log" "$PY" -u "$MON/dashboard.py" > "$DASH_PIDFILE"
fi

sleep 3
echo "Started."
echo "  generation pid : $(cat "$PIDFILE")"
echo "  dashboard      : http://127.0.0.1:8765"
echo "  log            : $(ls -t "$QA"/output/overnight_*.log 2>/dev/null | head -1)"
echo
echo "Check on it with : bash qa_generation/monitor/run_overnight_start.sh --status"
echo "Stop it with     : bash qa_generation/monitor/run_overnight_start.sh --stop"

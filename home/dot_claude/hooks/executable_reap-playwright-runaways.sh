#!/usr/bin/env bash
# Reap runaway and orphaned Playwright MCP browsers.
#
# WHY THIS EXISTS. On 2026-08-13 a headless Chrome renderer spawned by
# playwright-mcp was found pinning 97% of a core for 1 day 18 hours, alongside a
# GPU process at 29% and a node parent holding 9.3% of RAM -- roughly 107% of a
# core burned continuously by a browser nobody was driving. Two older
# playwright-mcp servers (10 and 25 days) were also still resident.
#
# THE OBVIOUS HEURISTIC DOES NOT WORK. The runaway's ancestor chain led to a
# LIVE `claude` process -- the very session that was running at the time. So
# "kill anything whose parent died" would not have caught it. Sustained CPU is
# the signal that actually separates a runaway from a healthy idle browser.
#
# Two independent conditions, either of which reaps the owning playwright-mcp
# tree (killing that node reliably takes its Chrome children with it):
#
#   1. RUNAWAY  -- an ms-playwright process whose CUMULATIVE cpu time is more
#      than CPU_RATIO_PCT of its wall-clock life, once it is older than
#      MIN_AGE_S. A browser doing real work bursts for seconds; averaged over
#      half an hour or more, a healthy one sits near zero. The runaway above
#      would have scored ~97%.
#   2. ORPHAN   -- a playwright-mcp server with no live `claude` ancestor. These
#      burn little CPU but hold RAM indefinitely.
#
# Never touches: the system chromium on port 9222, Gotenberg's chromium, or any
# browser belonging to a live session that is behaving itself.
#
# DRY_RUN=1 prints what it would kill and kills nothing.

set -uo pipefail

DRY_RUN="${DRY_RUN:-0}"
MIN_AGE_S="${MIN_AGE_S:-1800}"        # 30 min -- ignore short legitimate bursts
CPU_RATIO_PCT="${CPU_RATIO_PCT:-50}"  # >0.5 core sustained over its whole life

reaped=0

# etime/time (DD-HH:MM:SS, HH:MM:SS, or MM:SS) -> seconds
secs() {
  awk -F'[-:]' '{
    if (NF==4)      print $1*86400 + $2*3600 + $3*60 + $4
    else if (NF==3) print $1*3600 + $2*60 + $3
    else if (NF==2) print $1*60 + $2
    else            print 0
  }' <<<"$1"
}

# First ancestor (including self) whose args name playwright-mcp
owning_mcp() {
  local pid=$1 depth=0 args
  while [ "${pid:-0}" -gt 1 ] && [ "$depth" -lt 12 ]; do
    args=$(ps -o args= -p "$pid" 2>/dev/null)
    case "$args" in *playwright-mcp*|*@playwright/mcp*) echo "$pid"; return 0;; esac
    pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    depth=$((depth + 1))
  done
  return 1
}

has_live_claude_ancestor() {
  local pid=$1 depth=0 args
  while [ "${pid:-0}" -gt 1 ] && [ "$depth" -lt 12 ]; do
    pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    [ -z "${pid:-}" ] && return 1
    args=$(ps -o args= -p "$pid" 2>/dev/null)
    case "$args" in *claude*) return 0;; esac
    depth=$((depth + 1))
  done
  return 1
}

reap() { # pid reason
  local pid=$1 reason=$2
  local mcp
  mcp=$(owning_mcp "$pid") || mcp=$pid
  echo "reap pid=$mcp (via $pid): $reason"
  if [ "$DRY_RUN" != 1 ]; then
    kill -TERM "$mcp" 2>/dev/null
    sleep 2
    kill -9 "$mcp" 2>/dev/null
  fi
  reaped=$((reaped + 1))
}

# --- 1. runaways -------------------------------------------------------------
while read -r pid etime cputime rest; do
  case "$rest" in *ms-playwright*) ;; *) continue ;; esac
  age=$(secs "$etime"); cpu=$(secs "$cputime")
  [ "${age:-0}" -lt "$MIN_AGE_S" ] && continue
  ratio=$(( cpu * 100 / age ))
  [ "$ratio" -lt "$CPU_RATIO_PCT" ] && continue
  reap "$pid" "runaway browser, ${ratio}% of a core sustained over ${age}s"
done < <(ps -eo pid=,etime=,time=,args=)

# --- 2. orphans --------------------------------------------------------------
for pid in $(pgrep -f "node .*playwright-mcp" 2>/dev/null); do
  kill -0 "$pid" 2>/dev/null || continue          # already reaped above
  has_live_claude_ancestor "$pid" && continue
  reap "$pid" "orphaned playwright-mcp, no live claude ancestor"
done

[ "$reaped" -gt 0 ] && echo "{\"systemMessage\": \"Reaped $reaped runaway/orphaned Playwright browser tree(s).\"}"
exit 0

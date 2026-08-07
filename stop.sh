#!/usr/bin/env bash
#
# Baize (白泽) — 关闭脚本（重构版）
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BACKEND_PORT="${BACKEND_PORT:-8001}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
PID_DIR="$SCRIPT_DIR/logs"
SIGNAL="TERM"
[[ "${1:-}" == "--hard" ]] && SIGNAL="KILL"

log() { echo -e "\033[0;32m[stop]\033[0m $*"; }

_kill_pidfile() {
  local name="$1"; local pf="$PID_DIR/$1.pid"
  if [[ -f "$pf" ]]; then
    local pid; pid="$(cat "$pf" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      log "停止 $name (PID $pid)"; kill "-$SIGNAL" "$pid" 2>/dev/null || true
    fi
  fi
}

_kill_port() {
  local name="$1" port="$2"
  local pids; pids="$(lsof -ti :"$port" 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    log "按端口 $port 停止 $name"
    for pid in $pids; do kill "-$SIGNAL" "$pid" 2>/dev/null || true; done
  fi
}

_kill_pidfile "backend"; _kill_pidfile "frontend"
_kill_port "后端" "$BACKEND_PORT"
_kill_port "前端" "$FRONTEND_PORT"
rm -f "$PID_DIR/backend.pid" "$PID_DIR/frontend.pid"
echo ""
echo "白泽 (Baize) 服务已停止。"

#!/usr/bin/env bash
#
# Baize (白泽) — 关闭脚本 v4.0.0
#
# 停止策略（与 start.sh 的 setsid 进程组启动配套）：
#   1. 读 PID 文件 → 校验进程归属（命令行必须含本项目服务标识），
#      防止 PID 复用时误杀无关进程
#   2. 按**进程组**停止（uvicorn --reload 的 reloader 父进程 + worker
#      子进程一并回收），先 TERM 优雅退出，超时再 KILL
#   3. PID 文件丢失/失效时按端口兜底，且只杀命令行匹配的本项目进程
#
# 用法:
#   ./stop.sh           优雅停止（SIGTERM，等待 8s）
#   ./stop.sh --hard    强制停止（SIGKILL）
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BACKEND_PORT="${BACKEND_PORT:-8001}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
# BAIZE_LOG_DIR 优先（与 start.sh 保持一致），LOG_DIR 作为兼容回退
LOG_DIR="${BAIZE_LOG_DIR:-${LOG_DIR:-$SCRIPT_DIR/logs}}"
PID_DIR="$LOG_DIR"
SIGNAL="TERM"
GRACE=8
[[ "${1:-}" == "--hard" ]] && { SIGNAL="KILL"; GRACE=0; }

log() { echo -e "\033[0;32m[stop]\033[0m $*"; }
warn() { echo -e "\033[1;33m[stop]\033[0m $*"; }

# 服务标识：进程命令行必须包含这些关键字之一才认定归属本项目
BACKEND_MARK="baize.api.app"
FRONTEND_MARK="vite"

# 返回进程命令行（不存在返回空）
proc_args() { ps -o args= -p "$1" 2>/dev/null || true; }

# 归属校验：PID 命令行是否包含指定标识
proc_belongs() {
  local pid="$1" mark="$2"
  [[ "$(proc_args "$pid")" == *"$mark"* ]]
}

# 停止一个进程组（setsid 启动时 PID == PGID），再兜底直杀组首进程
_kill_group() {
  local pid="$1" sig="$2"
  # 负号 PID = 向整个进程组发信号；非组首时返回 ESRCH，忽略即可。
  # 用 -s 指定信号名，避免 "-TERM" 被解析成 kill 选项而非负 PGID。
  kill -s "$sig" -"$pid" 2>/dev/null || true
  kill -s "$sig" -- "$pid" 2>/dev/null || true
}

_stop_pidfile() {
  local name="$1" mark="$2" pf="$PID_DIR/$1.pid"
  [[ -f "$pf" ]] || return 0
  local pid; pid="$(cat "$pf" 2>/dev/null || true)"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  if ! proc_belongs "$pid" "$mark"; then
    warn "PID $pid ($name) 命令行不含 '$mark'，拒绝停止以防误杀。"
    warn "如确认是残留 PID 文件，请手动删除: $pf"
    return 0
  fi
  log "停止 $name (进程组 $pid)"
  _kill_group "$pid" "$SIGNAL"

  if [[ "$SIGNAL" == "TERM" ]]; then
    # 等待优雅退出，超时升级 KILL
    for _ in $(seq 1 "$GRACE"); do
      kill -0 "$pid" 2>/dev/null || return 0
      sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
      warn "$name 未在 ${GRACE}s 内退出，升级 SIGKILL"
      _kill_group "$pid" KILL
    fi
  fi
}

# 端口兜底：lsof 优先，ss 回退；只杀命令行匹配本项目标识的进程
port_pids() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti :"$port" 2>/dev/null || true
  elif command -v ss >/dev/null 2>&1; then
    ss -lptnH "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u || true
  fi
}

_kill_port() {
  local name="$1" port="$2" mark="$3"
  local pids; pids="$(port_pids "$port")"
  [[ -n "$pids" ]] || return 0
  for pid in $pids; do
    if proc_belongs "$pid" "$mark"; then
      log "按端口 $port 停止 $name (PID $pid)"
      _kill_group "$pid" "$SIGNAL"
    else
      warn "端口 $port 的 PID $pid 非本项目进程（不含 '$mark'），跳过: $(proc_args "$pid" | head -c 80)"
    fi
  done
}

_stop_pidfile "backend" "$BACKEND_MARK"
_stop_pidfile "frontend" "$FRONTEND_MARK"
_kill_port "后端" "$BACKEND_PORT" "$BACKEND_MARK"
_kill_port "前端" "$FRONTEND_PORT" "$FRONTEND_MARK"
rm -f "$PID_DIR/backend.pid" "$PID_DIR/frontend.pid"

echo ""
if [[ "$SIGNAL" == "KILL" ]]; then
  echo "白泽 (Baize) 服务已强制停止。"
else
  echo "白泽 (Baize) 服务已停止。"
fi

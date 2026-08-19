#!/usr/bin/env bash
#
# Baize (白泽) — 启动脚本 v1.5.0
#
# 启动后端 (FastAPI/uvicorn, 端口 8001) 和前端 (Vite, 端口 5173)。
# 项目布局：
#   baize-core-v1.5.0          核心框架（含前端 web/）
#   baize-orchestration        流水线编排模块（可选）
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="${BAIZE_CORE_DIR:-$SCRIPT_DIR}"
WEB_DIR="${BAIZE_WEB_DIR:-$CORE_DIR/web}"
LOG_DIR="${BAIZE_LOG_DIR:-$CORE_DIR/logs}"
PYTHON_BIN="${BAIZE_PYTHON:-$CORE_DIR/.venv/bin/python}"

C_GREEN='\033[0;32m'; C_CYAN='\033[0;36m'; C_RED='\033[0;31m'; C_RESET='\033[0m'
log()  { echo -e "${C_GREEN}[start]${C_RESET} $*"; }
err()  { echo -e "${C_RED}[start]${C_RESET} $*"; }

BACKEND_PORT="${BACKEND_PORT:-8001}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
PID_DIR="$LOG_DIR"

if [[ ! -x "$PYTHON_BIN" ]]; then
  err "找不到 Python 虚拟环境: $PYTHON_BIN"
  err "请先运行: $CORE_DIR/setup.sh"
  exit 1
fi

if ! "$PYTHON_BIN" -c "import baize" >/dev/null 2>&1; then
  err "baize 包无法导入，环境可能不完整。请先运行: $CORE_DIR/setup.sh"
  exit 1
fi

mkdir -p "$LOG_DIR" "$PID_DIR"

is_port_in_use() {
  lsof -ti :"$1" >/dev/null 2>&1
}

BACKEND_ALREADY_RUNNING=false
if is_port_in_use "$BACKEND_PORT"; then
  log "后端端口 $BACKEND_PORT 已被占用，跳过。"
  BACKEND_ALREADY_RUNNING=true
else
  log "启动后端 (端口 $BACKEND_PORT)..."
  export BAIZE_API_REQUIRE_AUTH="${BAIZE_API_REQUIRE_AUTH:-1}"
  nohup "$PYTHON_BIN" -m uvicorn baize.api.app:create_baize_api_app \
    --factory --host 0.0.0.0 --port "$BACKEND_PORT" > "$BACKEND_LOG" 2>&1 &
  echo $! > "$PID_DIR/backend.pid"
  log "后端 PID: $(cat "$PID_DIR/backend.pid")"
fi

if is_port_in_use "$FRONTEND_PORT"; then
  log "前端端口 $FRONTEND_PORT 已被占用，跳过。"
else
  VITE_BIN="$WEB_DIR/node_modules/.bin/vite"
  if [[ -x "$VITE_BIN" ]]; then
    log "启动前端 (端口 $FRONTEND_PORT)..."
    ( cd "$WEB_DIR" && nohup "$VITE_BIN" --host 0.0.0.0 --port "$FRONTEND_PORT" > "$FRONTEND_LOG" 2>&1 & echo $! > "$PID_DIR/frontend.pid" )
  else
    log "前端依赖未安装，跳过前端（先运行: cd $WEB_DIR && npm install）。"
  fi
fi

echo ""
echo -e "${C_CYAN}============================================${C_RESET}"
echo -e "${C_CYAN}  白泽 (Baize) 已启动${C_RESET}"
echo -e "${C_CYAN}============================================${C_RESET}"
echo -e "  后端: http://localhost:$BACKEND_PORT"
echo -e "  前端: http://localhost:$FRONTEND_PORT"
echo -e "  停止: $CORE_DIR/stop.sh"
echo -e "${C_CYAN}============================================${C_RESET}"

if $BACKEND_ALREADY_RUNNING; then
  log "后端此前已在运行，跳过凭证等待。"
else
  timeout=30; deadline=$(( $(date +%s) + timeout ))
  while (( $(date +%s) < deadline )); do
    if grep -q "登录凭证" "$BACKEND_LOG" 2>/dev/null; then
      sed 's/\x1b\[[0-9;]*m//g' "$BACKEND_LOG" | grep -A7 "登录凭证" | head -8
      exit 0
    fi
    pid="$(cat "$PID_DIR/backend.pid" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
      err "后端进程已退出，请查看 $BACKEND_LOG"
      exit 1
    fi
    sleep 0.5
  done
  log "等待凭证超时，请查看 $BACKEND_LOG"
fi

#!/usr/bin/env bash
#
# Baize (白泽) — 启动脚本（重构版）
#
# 启动后端 (FastAPI/uvicorn, 端口 8001) 和前端 (Vite, 端口 5173)。
# 登录凭证会在后端启动时自动生成并输出到终端。
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

C_GREEN='\033[0;32m'; C_CYAN='\033[0;36m'; C_RED='\033[0;31m'; C_RESET='\033[0m'
log()  { echo -e "${C_GREEN}[start]${C_RESET} $*"; }
err()  { echo -e "${C_RED}[start]${C_RESET} $*"; }

PYTHON_BIN="${PYTHON_BIN:-$SCRIPT_DIR/.venv/bin/python}"
BACKEND_PORT="${BACKEND_PORT:-8001}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
LOG_DIR="${LOG_DIR:-$SCRIPT_DIR/logs}"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
PID_DIR="$LOG_DIR"

if [[ ! -x "$PYTHON_BIN" ]]; then
  err "找不到 Python 虚拟环境: $PYTHON_BIN"
  err "请先运行: ./setup.sh"
  exit 1
fi

# 检测 baize 包是否可导入（虚拟环境从其他路径复制/迁移后 editable 指向易失效）
if ! "$PYTHON_BIN" -c "import baize" >/dev/null 2>&1; then
  err "baize 包无法导入，环境可能不完整（或虚拟环境来自其他路径）。"
  err "请先运行: ./setup.sh"
  err "或手动修复: $SCRIPT_DIR/.venv/bin/pip install -e ."
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
  VITE_BIN="$SCRIPT_DIR/web/node_modules/.bin/vite"
  if [[ -x "$VITE_BIN" ]]; then
    log "启动前端 (端口 $FRONTEND_PORT)..."
    ( cd "$SCRIPT_DIR/web" && nohup "$VITE_BIN" --host 0.0.0.0 --port "$FRONTEND_PORT" > "$FRONTEND_LOG" 2>&1 & echo $! > "$PID_DIR/frontend.pid" )
  else
    log "前端依赖未安装，跳过前端（先运行 ./setup.sh）。"
  fi
fi

echo ""
echo -e "${C_CYAN}============================================${C_RESET}"
echo -e "${C_CYAN}  白泽 (Baize) 已启动${C_RESET}"
echo -e "${C_CYAN}============================================${C_RESET}"
echo -e "  后端: http://localhost:$BACKEND_PORT"
echo -e "  前端: http://localhost:$FRONTEND_PORT"
echo -e "  停止: ./stop.sh"
echo -e "${C_CYAN}============================================${C_RESET}"

# 等待凭证输出
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

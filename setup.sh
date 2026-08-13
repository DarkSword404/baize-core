#!/usr/bin/env bash
#
# 白泽 (Baize) — v1.3.0 环境安装脚本
#
# 功能：
#   1. 创建 Python 虚拟环境 (.venv)
#   2. 安装 baize-core（核心模块）
#   3. 安装前端依赖并构建 (web/)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

C_GREEN='\033[0;32m'; C_YELLOW='\033[1;33m'; C_CYAN='\033[0;36m'; C_RED='\033[0;31m'; C_RESET='\033[0m'
log()  { echo -e "${C_GREEN}[setup]${C_RESET} $*"; }
warn() { echo -e "${C_YELLOW}[setup]${C_RESET} $*"; }
err()  { echo -e "${C_RED}[setup]${C_RESET} $*"; }

PYTHON="${PYTHON:-python3}"
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  err "未找到 Python（${PYTHON}），请先安装 Python 3.11+。"
  exit 1
fi

# ---- 1. 虚拟环境 ----
log "创建虚拟环境 .venv ..."
"$PYTHON" -m venv "$VENV_DIR"
"$PYTHON_BIN" -m pip install --upgrade pip >/dev/null 2>&1 || true

# ---- 2. 核心模块 ----
log "安装 baize-core（核心模块）..."
"$PYTHON_BIN" -m pip install -e "$SCRIPT_DIR" 2>&1 | tail -3

# ---- 3. 前端 ----
if [[ -f web/package.json ]]; then
  log "安装前端依赖并构建 (web/) ..."
  ( cd web && npm install --no-audit --no-fund && npm run build ) || \
    warn "前端构建失败，可跳过此步直接运行 ./start.sh（将以后端 API + 开发服务器模式启动）。"
else
  warn "未找到 web/package.json，跳过前端安装。"
fi

echo ""
echo -e "${C_CYAN}══════════════════════════════════════════════════════════${C_RESET}"
echo -e "${C_CYAN}  白泽 (Baize) v1.3.0 环境就绪${C_RESET}"
echo -e "${C_CYAN}  启动服务：./start.sh${C_RESET}"
echo -e "${C_CYAN}  停止服务：./stop.sh${C_RESET}"
echo -e "${C_CYAN}══════════════════════════════════════════════════════════${C_RESET}"

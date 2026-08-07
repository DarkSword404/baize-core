#!/usr/bin/env bash
#
# Baize (白泽) — 环境安装脚本（重构版）
#
# 创建虚拟环境、安装后端依赖、安装前端依赖并构建。
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

C_GREEN='\033[0;32m'; C_YELLOW='\033[1;33m'; C_CYAN='\033[0;36m'; C_RESET='\033[0m'
log()  { echo -e "${C_GREEN}[setup]${C_RESET} $*"; }

PYTHON="${PYTHON:-python3}"
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"

log "创建虚拟环境..."
"$PYTHON" -m venv "$VENV_DIR"
"$PYTHON_BIN" -m pip install --upgrade pip >/dev/null 2>&1 || true
log "安装后端依赖..."
"$PYTHON_BIN" -m pip install -e .

log "安装前端依赖并构建..."
if [[ -f web/package.json ]]; then
  ( cd web && npm install && npm run build ) || log "前端构建失败（可跳过）"
else
  log "未找到 web/package.json，跳过前端。"
fi

echo ""
echo -e "${C_CYAN}白泽 (Baize) 环境就绪。运行 ./start.sh 启动。${C_RESET}"

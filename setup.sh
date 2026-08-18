#!/usr/bin/env bash
#
# 白泽·智脑 (Baize) — v1.4.0 环境安装脚本
#
# 安装 baize-core（必需，含前端 web/）和 baize-orchestration（可选）到虚拟环境。
# 前端依赖需在 baize-core/web 目录单独安装。
#
set -euo pipefail

CORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$CORE_DIR"

C_GREEN='\033[0;32m'; C_YELLOW='\033[1;33m'; C_CYAN='\033[0;36m'; C_RED='\033[0;31m'; C_RESET='\033[0m'
log()  { echo -e "${C_GREEN}[setup]${C_RESET} $*"; }
warn() { echo -e "${C_YELLOW}[setup]${C_RESET} $*"; }
err()  { echo -e "${C_RED}[setup]${C_RESET} $*"; }

PYTHON="${PYTHON:-python3}"
VENV_DIR="$CORE_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"
ORCH_DIR="${BAIZE_ORCH_DIR:-$CORE_DIR/../baize-orchestration}"

log "创建虚拟环境..."
"$PYTHON" -m venv "$VENV_DIR"
"$PYTHON_BIN" -m pip install --upgrade pip >/dev/null 2>&1 || true

log "安装 baize-core（核心模块）..."
"$PYTHON_BIN" -m pip install -e "$CORE_DIR" 2>&1 | tail -3

if [[ -d "$ORCH_DIR" ]]; then
  log "安装 baize-orchestration（流水线编排模块）..."
  "$PYTHON_BIN" -m pip install -e "$ORCH_DIR" --no-deps 2>&1 | tail -3 || warn "baize-orchestration 安装失败，跳过编排模块。"
else
  warn "未找到 $ORCH_DIR，跳过编排模块。"
fi

echo ""
echo -e "${C_CYAN}═══════════════════════════════════════════════════════${C_RESET}"
echo -e "${C_CYAN}  白泽·智脑 (Baize) 环境就绪${C_RESET}"
echo -e "${C_CYAN}  前端依赖: cd $CORE_DIR/web && npm install${C_RESET}"
echo -e "${C_CYAN}  运行 ./start.sh 启动服务${C_RESET}"
echo -e "${C_CYAN}═══════════════════════════════════════════════════════${C_RESET}"

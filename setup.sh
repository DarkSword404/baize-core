#!/usr/bin/env bash
#
# 白泽·智脑 (Baize) — v4.0.0 环境安装脚本
#
# 安装内容：
#   1. 创建 .venv 虚拟环境并 pip install baize-core
#   2. 安装 web/ 前端依赖（node/npm 可用时自动执行）
#   3. 可选安装 baize-orchestration（同级目录存在时自动安装）
#
# 参数：
#   --with-tools     同时预装系统工具依赖（nmap/sqlmap/nuclei 等，见 install-tools.sh）
#   --with-sandbox   构建会话级容器沙箱镜像 baize-sandbox:base（需 docker 或 podman）
#
set -euo pipefail

CORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$CORE_DIR"

C_GREEN='\033[0;32m'; C_YELLOW='\033[1;33m'; C_CYAN='\033[0;36m'; C_RED='\033[0;31m'; C_RESET='\033[0m'
log()  { echo -e "${C_GREEN}[setup]${C_RESET} $*"; }
warn() { echo -e "${C_YELLOW}[setup]${C_RESET} $*"; }
err()  { echo -e "${C_RED}[setup]${C_RESET} $*"; }

WITH_TOOLS=0
WITH_SANDBOX=0
for arg in "$@"; do
  case "$arg" in
    --with-tools)   WITH_TOOLS=1 ;;
    --with-sandbox) WITH_SANDBOX=1 ;;
    *) warn "忽略未知参数: $arg" ;;
  esac
done

PYTHON="${PYTHON:-python3}"
VENV_DIR="$CORE_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"
WEB_DIR="$CORE_DIR/web"
ORCH_DIR="${BAIZE_ORCH_DIR:-$CORE_DIR/../baize-orchestration}"
SANDBOX_IMAGE="${BAIZE_SANDBOX_IMAGE:-baize-sandbox:base}"
SANDBOX_DOCKERFILE="$CORE_DIR/docker/baize-sandbox/Dockerfile"

log "创建虚拟环境..."
"$PYTHON" -m venv "$VENV_DIR"
"$PYTHON_BIN" -m pip install --upgrade pip >/dev/null 2>&1 || true

log "安装 baize-core（核心模块，v4.0.0）..."
"$PYTHON_BIN" -m pip install -e "$CORE_DIR" 2>&1 | tail -3

if [[ -d "$ORCH_DIR" ]]; then
  log "安装 baize-orchestration（流水线编排模块）..."
  "$PYTHON_BIN" -m pip install -e "$ORCH_DIR" --no-deps 2>&1 | tail -3 || warn "baize-orchestration 安装失败，跳过编排模块。"
else
  warn "未找到 $ORCH_DIR，跳过编排模块（可选）。"
fi

# ── 前端依赖 ──
if [[ -d "$WEB_DIR" && -f "$WEB_DIR/package.json" ]]; then
  if command -v npm >/dev/null 2>&1; then
    if [[ ! -d "$WEB_DIR/node_modules" ]]; then
      log "安装前端依赖 (npm install)..."
      ( cd "$WEB_DIR" && npm install --no-audit --no-fund ) 2>&1 | tail -3
    else
      log "前端依赖已存在 (web/node_modules)，跳过。"
    fi
  else
    warn "未找到 npm，跳过前端依赖。请安装 Node.js 18+ 后执行: cd web && npm install"
  fi
else
  warn "未找到 $WEB_DIR，跳过前端依赖安装。"
fi

# ── 会话级容器沙箱镜像（v4.0.0）──
build_sandbox_image() {
  local runtime="$1"
  log "使用 $runtime 构建沙箱镜像 $SANDBOX_IMAGE ..."
  "$runtime" build -t "$SANDBOX_IMAGE" -f "$SANDBOX_DOCKERFILE" "$CORE_DIR"
}

if [[ "$WITH_SANDBOX" == "1" ]]; then
  echo ""
  if [[ ! -f "$SANDBOX_DOCKERFILE" ]]; then
    warn "未找到沙箱 Dockerfile: $SANDBOX_DOCKERFILE，跳过镜像构建。"
  elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    build_sandbox_image docker
  elif command -v docker >/dev/null 2>&1 && sg docker -c 'docker info' >/dev/null 2>&1; then
    # 二进制在但当前 shell 无 docker 组成员身份 → 经 sg 临时提组构建
    log "当前 shell 无 docker 直接权限，经 'sg docker' 构建镜像..."
    sg docker -c "docker build -t $SANDBOX_IMAGE -f $SANDBOX_DOCKERFILE $CORE_DIR"
  elif command -v podman >/dev/null 2>&1; then
    build_sandbox_image podman
  else
    warn "未找到可用的 docker/podman（或当前用户无 docker 权限），跳过沙箱镜像构建。"
    warn "可稍后手动执行：docker build -t $SANDBOX_IMAGE -f docker/baize-sandbox/Dockerfile ."
  fi
fi

echo ""
echo -e "${C_CYAN}═══════════════════════════════════════════════════════${C_RESET}"
echo -e "${C_CYAN}  白泽·智脑 (Baize) v4.0.0 环境就绪${C_RESET}"
echo -e "${C_CYAN}  启动服务: ./start.sh${C_RESET}"
echo -e "${C_CYAN}  停止服务: ./stop.sh${C_RESET}"
echo -e "${C_CYAN}═══════════════════════════════════════════════════════${C_RESET}"

if [[ "$WITH_TOOLS" == "1" ]]; then
  echo ""
  log "预装系统工具依赖（nmap / sqlmap / nuclei / 浏览器等）..."
  "$CORE_DIR/install-tools.sh" --yes || warn "工具预装部分失败，可稍后重新运行 ./install-tools.sh --yes 补装。"
fi

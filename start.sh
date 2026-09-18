#!/usr/bin/env bash
#
# Baize (白泽) — 启动脚本 v4.0.0
#
# 启动后端 (FastAPI/uvicorn, 端口 8001) 和前端 (Vite, 端口 5173)。
#
# 用法:
#   ./start.sh             正常启动
#   ./start.sh --reload    后端开发模式（uvicorn --reload，代码变更自动重启）
#
# 说明：
#   - 后端/前端均以独立进程组（setsid）启动，stop.sh 可整组回收，
#     --reload 模式的 reloader 父进程 + worker 子进程不会残留
#   - 若当前用户无 docker 直接权限但属于 docker 组，后端自动经
#     'sg docker -c' 包装启动（v4.0.0 会话级容器沙箱需要）
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="${BAIZE_CORE_DIR:-$SCRIPT_DIR}"
WEB_DIR="${BAIZE_WEB_DIR:-$CORE_DIR/web}"
# BAIZE_LOG_DIR 优先（与其它 BAIZE_* 变量一致），LOG_DIR 作为兼容回退
LOG_DIR="${BAIZE_LOG_DIR:-${LOG_DIR:-$CORE_DIR/logs}}"
PYTHON_BIN="${BAIZE_PYTHON:-$CORE_DIR/.venv/bin/python}"

# 白泽数据目录总开关：默认指向项目内 .baize-data/，绕过沙箱对 ~/.baize 的写入限制。
export BAIZE_DATA_DIR="${BAIZE_DATA_DIR:-$CORE_DIR/.baize-data}"
# 协作浏览器持久化 profile（可单独覆盖，默认跟随 BAIZE_DATA_DIR）
export BAIZE_SHARED_BROWSER_PROFILE="${BAIZE_SHARED_BROWSER_PROFILE:-$BAIZE_DATA_DIR/shared-browser-profile}"
# 认证库（可单独覆盖，默认跟随 BAIZE_DATA_DIR）
export BAIZE_AUTH_DB="${BAIZE_AUTH_DB:-$BAIZE_DATA_DIR/api_auth.json}"

C_GREEN='\033[0;32m'; C_CYAN='\033[0;36m'; C_YELLOW='\033[1;33m'; C_RED='\033[0;31m'; C_RESET='\033[0m'
log()  { echo -e "${C_GREEN}[start]${C_RESET} $*"; }
warn() { echo -e "${C_YELLOW}[start]${C_RESET} $*"; }
err()  { echo -e "${C_RED}[start]${C_RESET} $*"; }

BACKEND_PORT="${BACKEND_PORT:-8001}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
PID_DIR="$LOG_DIR"
SANDBOX_IMAGE="${BAIZE_SANDBOX_IMAGE:-baize-sandbox:base}"

BACKEND_RELOAD="${BAIZE_RELOAD:-0}"
for arg in "$@"; do
  case "$arg" in
    --reload) BACKEND_RELOAD=1 ;;
    *) warn "忽略未知参数: $arg" ;;
  esac
done

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

# 端口占用探测（lsof 优先，最小系统回退 ss）
port_pids() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -ti :"$port" 2>/dev/null || true
  elif command -v ss >/dev/null 2>&1; then
    ss -lptnH "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u || true
  fi
}
is_port_in_use() { [[ -n "$(port_pids "$1")" ]]; }

# ── docker 权限探测：容器沙箱需要访问 docker daemon ──
# 直接可用 → 不包装；仅在 docker 组内可提组 → sg 包装；都不行 → 告警但不阻止启动
# BAIZE_DOCKER_WRAP=sg|direct 可强制覆盖（排障用）
DOCKER_WRAP=""
case "${BAIZE_DOCKER_WRAP:-}" in
  sg)     DOCKER_WRAP="sg" ;;
  direct) DOCKER_WRAP="" ;;
  "")
    if command -v docker >/dev/null 2>&1; then
      if docker info >/dev/null 2>&1; then
        DOCKER_WRAP=""
      elif sg docker -c 'docker info' >/dev/null 2>&1; then
        DOCKER_WRAP="sg"
        warn "当前 shell 无 docker 直接权限，后端将经 'sg docker -c' 包装启动。"
      else
        warn "docker 已安装但当前用户无访问权限（不在 docker 组），会话级容器沙箱不可用。"
        warn "可执行: sudo usermod -aG docker \$USER 后重新登录；本地执行器不受影响。"
      fi
    fi
    ;;
  *) warn "忽略无效 BAIZE_DOCKER_WRAP=${BAIZE_DOCKER_WRAP}（可选: sg/direct）" ;;
esac

# 在**独立会话/进程组**中启动服务并由子进程自写 PID 文件。
# 不能用 `setsid cmd &; echo $!`：当本脚本自身是进程组首进程时 setsid 会
# fork，$! 拿到的是随即退出的父进程 PID。子 shell 写 $$ 保证 PID == PGID。
# 用法: start_pg <pidfile> <logfile> <workdir|-> -- <命令...>
start_pg() {
  local pidfile="$1" logfile="$2" workdir="$3"; shift 3
  [[ "$1" == "--" ]] && shift
  setsid bash -c '
    pidfile="$1"; logfile="$2"; workdir="$3"; shift 3
    echo $$ > "$pidfile"
    [[ "$workdir" != "-" ]] && cd "$workdir"
    exec "$@"
  ' _ "$pidfile" "$logfile" "$workdir" "$@" > "$logfile" 2>&1 < /dev/null &
}

BACKEND_ALREADY_RUNNING=false
if is_port_in_use "$BACKEND_PORT"; then
  log "后端端口 $BACKEND_PORT 已被占用，跳过。"
  BACKEND_ALREADY_RUNNING=true
else
  log "启动后端 (端口 $BACKEND_PORT$([[ "$BACKEND_RELOAD" == "1" ]] && echo ', --reload') )..."
  export BAIZE_API_REQUIRE_AUTH="${BAIZE_API_REQUIRE_AUTH:-1}"

  # 组装 uvicorn 命令；--factory 必须，--reload 仅开发模式（生产请勿开）
  BACKEND_CMD=(
    "$PYTHON_BIN" -m uvicorn baize.api.app:create_baize_api_app
    --factory --host 0.0.0.0 --port "$BACKEND_PORT"
  )
  [[ "$BACKEND_RELOAD" == "1" ]] && BACKEND_CMD+=(--reload)

  if [[ "$DOCKER_WRAP" == "sg" ]]; then
    # 需要 docker 提组：进程组首进程为 sg，其派生的 sh/python 均留在同一
    # 进程组内，stop.sh 一次组信号即可回收全链；%q 生成 sh 可解析的安全命令串
    QUOTED_CMD="$(printf '%q ' "${BACKEND_CMD[@]}")"
    start_pg "$PID_DIR/backend.pid" "$BACKEND_LOG" - -- \
      sg docker -c "$QUOTED_CMD"
  else
    start_pg "$PID_DIR/backend.pid" "$BACKEND_LOG" - -- "${BACKEND_CMD[@]}"
  fi
  sleep 0.3
  log "后端进程组 PID: $(cat "$PID_DIR/backend.pid" 2>/dev/null || echo '?')"
fi

if is_port_in_use "$FRONTEND_PORT"; then
  log "前端端口 $FRONTEND_PORT 已被占用，跳过。"
else
  VITE_BIN="$WEB_DIR/node_modules/.bin/vite"
  if [[ -x "$VITE_BIN" ]]; then
    log "启动前端 (端口 $FRONTEND_PORT)..."
    start_pg "$PID_DIR/frontend.pid" "$FRONTEND_LOG" "$WEB_DIR" -- \
      "$VITE_BIN" --host 0.0.0.0 --port "$FRONTEND_PORT"
  else
    warn "前端依赖未安装，跳过前端（运行: cd $WEB_DIR && npm install）。"
  fi
fi

# ── 健康检查：轮询 /api/v1/health，最多 30s ──
if ! $BACKEND_ALREADY_RUNNING; then
  log "等待后端就绪..."
  ready=false
  for _ in $(seq 1 60); do
    if curl -sf "http://localhost:$BACKEND_PORT/api/v1/health" >/dev/null 2>&1; then
      ready=true; break
    fi
    pid="$(cat "$PID_DIR/backend.pid" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
      err "后端进程已退出，请查看 $BACKEND_LOG"
      exit 1
    fi
    sleep 0.5
  done
  if $ready; then
    log "后端健康检查通过。"
  else
    warn "后端 30s 内未通过健康检查，请查看 $BACKEND_LOG"
  fi
fi

# ── 沙箱镜像就绪提示（不阻塞启动）──
sandbox_runtime=""
if command -v docker >/dev/null 2>&1; then
  if docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1; then
    sandbox_runtime="docker"
  elif [[ "$DOCKER_WRAP" == "sg" ]] && sg docker -c "docker image inspect $SANDBOX_IMAGE" >/dev/null 2>&1; then
    sandbox_runtime="docker(sg)"
  fi
elif command -v podman >/dev/null 2>&1 && podman image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1; then
  sandbox_runtime="podman"
fi

echo ""
echo -e "${C_CYAN}============================================${C_RESET}"
echo -e "${C_CYAN}  白泽 (Baize) v4.0.0 已启动${C_RESET}"
echo -e "${C_CYAN}============================================${C_RESET}"
echo -e "  后端: http://localhost:$BACKEND_PORT"
echo -e "  前端: http://localhost:$FRONTEND_PORT"
echo -e "  停止: $CORE_DIR/stop.sh"
if [[ -n "$sandbox_runtime" ]]; then
  echo -e "  沙箱: $SANDBOX_IMAGE ($sandbox_runtime) 就绪"
else
  echo -e "  沙箱: 未检测到 $SANDBOX_IMAGE，需要时执行 ./setup.sh --with-sandbox"
fi
echo -e "${C_CYAN}============================================${C_RESET}"

# 首次启动时展示自动生成的管理员登录凭证
if $BACKEND_ALREADY_RUNNING; then
  log "后端此前已在运行，跳过凭证等待。"
elif [[ -f "$BAIZE_AUTH_DB" ]]; then
  log "凭证文件已存在，跳过生成 ($BAIZE_AUTH_DB)"
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
  err "等待凭证超时，请查看 $BACKEND_LOG"
fi

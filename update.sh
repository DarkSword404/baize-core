#!/usr/bin/env bash
#
# Baize 更新脚本
#
# 从 GitHub 远程仓库拉取最新版本并更新依赖。
# 用法:
#   ./update.sh            # 拉取最新版本并更新依赖
#   ./update.sh --check    # 仅检查是否有更新，不实际更新
#
set -euo pipefail

# 定位脚本所在目录（无论从何处执行）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 远程仓库
REMOTE="origin"
BRANCH="main"

echo "=============================================="
echo "  Baize 更新脚本"
echo "=============================================="

# 1. 检查是否为 git 仓库
if [ ! -d ".git" ]; then
  echo "❌ 当前目录不是 git 仓库，无法通过 git 更新。"
  echo "   请确保已通过 git clone 或本仓库初始化了 git。"
  exit 1
fi

# 2. 检查远程是否配置
if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
  echo "❌ 未配置远程仓库 '$REMOTE'。"
  echo "   请执行: git remote add $REMOTE https://github.com/DarkSword404/baize.git"
  exit 1
fi

echo ""
echo "▶ 拉取远程更新信息..."
git fetch "$REMOTE" "$BRANCH"

LOCAL_HEAD=$(git rev-parse HEAD 2>/dev/null || echo "")
REMOTE_HEAD=$(git rev-parse "$REMOTE/$BRANCH" 2>/dev/null || echo "")

if [ -z "$REMOTE_HEAD" ]; then
  echo "❌ 无法获取远程分支信息。请检查网络或仓库配置。"
  exit 1
fi

# 3. 检查是否有本地未提交改动
LOCAL_DIRTY=false
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  LOCAL_DIRTY=true
fi

echo ""
echo "  本地版本 : $(git log -1 --format='%h %s' HEAD 2>/dev/null || echo '无提交')"
echo "  远程版本 : $(git log -1 --format='%h %s' "$REMOTE/$BRANCH" 2>/dev/null)"

# 4. 是否已有更新
if [ "$LOCAL_HEAD" = "$REMOTE_HEAD" ] && [ "$LOCAL_DIRTY" = false ]; then
  echo ""
  echo "✅ 当前已是最新版本，无需更新。"
  exit 0
fi

# --check 模式：仅报告
if [ "${1:-}" = "--check" ]; then
  echo ""
  if [ "$LOCAL_HEAD" != "$REMOTE_HEAD" ]; then
    echo "⚠️ 检测到远程有新版本，可执行 ./update.sh 更新。"
  else
    echo "✅ 当前版本与远程一致，但有本地未提交改动。"
  fi
  exit 0
fi

# 5. 处理本地未提交改动
if [ "$LOCAL_DIRTY" = true ]; then
  echo ""
  echo "⚠️ 检测到本地有未提交的改动。"
  echo "   为避免冲突，将自动暂存这些改动（git stash）。"
  read -p "是否继续？[y/N] " -r CONFIRM
  if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "已取消。"
    exit 0
  fi
  git stash push -u -m "baize-update-$(date +%s)"
  echo "✅ 本地改动已暂存。更新后可用 'git stash pop' 恢复。"
fi

# 6. 拉取远程更新
echo ""
echo "▶ 拉取最新版本..."
if git rev-parse "$REMOTE/$BRANCH" >/dev/null 2>&1 && \
   [ "$(git merge-base HEAD "$REMOTE/$BRANCH" 2>/dev/null)" = "$LOCAL_HEAD" ]; then
  # fast-forward 合并
  git merge --ff-only "$REMOTE/$BRANCH"
  echo "✅ 已更新到最新版本。"
else
  # 非 fast-forward，尝试 rebase
  echo "⚠️ 本地与远程存在分叉，尝试 rebase..."
  if git rebase "$REMOTE/$BRANCH"; then
    echo "✅ rebase 完成。"
  else
    echo "❌ rebase 冲突，请手动解决后执行: git rebase --continue"
    exit 1
  fi
fi

# 7. 更新 Python 依赖（若 pyproject.toml 有变化）
echo ""
echo "▶ 检查并更新 Python 依赖..."
if [ -x ".venv/bin/pip" ]; then
  # 比较依赖声明与已安装，尝试安装新依赖
  .venv/bin/pip install -e ".[dev]" 2>/dev/null || .venv/bin/pip install -e . || \
    echo "⚠️ 依赖安装跳过（请在需要时手动执行 .venv/bin/pip install -e .）"
  echo "✅ 依赖已检查。"
else
  echo "⚠️ 未找到虚拟环境 .venv，跳过依赖更新。"
fi

# 8. 前端依赖
echo ""
echo "▶ 检查前端依赖..."
if [ -d "web" ]; then
  if [ -f "web/package.json" ] && [ ! -d "web/node_modules" ]; then
    echo "   前端依赖未安装，执行 npm install..."
    (cd web && npm install --no-audit --no-fund)
  fi
fi

echo ""
echo "=============================================="
echo "  ✅ 更新完成！"
echo "=============================================="
echo ""
echo "  ⚠️ 如果服务正在运行，请重启服务使更新生效："
echo "     ./stop.sh && ./start.sh"
echo ""
echo "  如需恢复本次更新前的本地改动: git stash pop"

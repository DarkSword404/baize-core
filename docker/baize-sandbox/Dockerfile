# baize-sandbox:base —— 白泽会话级沙箱基础镜像
#
# 每个会话启动一个持久容器，agent 的所有 shell/工具命令通过 exec 进入执行。
# 容器内默认 root（非 sudo），可直接 apt 安装渗透工具；
# 运行时由 baize 以 --cap-drop=ALL --security-opt=no-new-privileges --read-only 启动。
#
# 构建：
#   docker build -t baize-sandbox:base -f docker/baize-sandbox/Dockerfile .
#
# 设计原则：
# - 核心工具（apt 安装）必须成功，否则构建失败
# - 可选工具（nuclei/sqlmap 等需外网下载）失败时仅警告，不阻断构建
#   用户可在容器内通过 apt/pip 手动补装

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DEFAULT_TIMEOUT=120

# 核心系统工具 + 渗透测试 + CTF misc 高频工具（必须成功）
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        wget \
        git \
        unzip \
        python3 \
        python3-pip \
        python3-venv \
        tmux \
        iproute2 \
        dnsutils \
        netcat-openbsd \
        nmap \
        whatweb \
        gobuster \
        ffuf \
        hydra \
        # CTF misc 常用工具
        imagemagick \
        binwalk \
        steghide \
        exiftool \
        pngcheck \
        foremost \
        # opencv 运行时依赖（libGL.so.1）
        libgl1 \
        libglib2.0-0 \
        # 清理 apt 缓存缩小镜像
    && rm -rf /var/lib/apt/lists/*

# Python CTF/图像处理库（可选，失败不阻断构建）
# 用 opencv-python-headless 替代 opencv-python，避免 GUI 依赖
RUN pip3 install --no-cache-dir --timeout 120 \
        Pillow opencv-python-headless stegano 2>&1 \
    || echo "[WARN] Pillow/opencv/stegano 安装失败，用户可在容器内手动执行: pip3 install Pillow opencv-python-headless stegano"

# nuclei（Go 静态二进制，从 GitHub 下载；可选，失败不阻断构建）
# 使用 ghproxy 镜像加速；如仍失败，用户可在容器内手动下载
ARG NUCLEI_VERSION=3.3.7
RUN set -e; \
    arch=$(uname -m); \
    case "$arch" in \
        x86_64) nfile="nuclei_${NUCLEI_VERSION}_linux_amd64.zip" ;; \
        aarch64) nfile="nuclei_${NUCLEI_VERSION}_linux_arm64.zip" ;; \
        *) echo "[WARN] 不支持架构 $arch，跳过 nuclei"; exit 0 ;; \
    esac; \
    for mirror in \
        "https://ghproxy.net/https://github.com/projectdiscovery/nuclei/releases/download/v${NUCLEI_VERSION}" \
        "https://github.com/projectdiscovery/nuclei/releases/download/v${NUCLEI_VERSION}" \
        "https://gh-proxy.com/https://github.com/projectdiscovery/nuclei/releases/download/v${NUCLEI_VERSION}" \
    ; do \
        echo "[INFO] 尝试下载 nuclei: $mirror/$nfile"; \
        if curl -fsSL --max-time 45 --retry 1 -o /tmp/nuclei.zip "$mirror/$nfile"; then \
            unzip -o /tmp/nuclei.zip -d /usr/local/bin && rm -f /tmp/nuclei.zip && \
            chmod +x /usr/local/bin/nuclei* && \
            echo "[OK] nuclei 已安装" && exit 0; \
        fi; \
    done; \
    echo "[WARN] nuclei 下载失败（GitHub 不可达），用户可在容器内手动安装: apt install -y golang-go && go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest"

# sqlmap（纯 Python，pip 安装；可选，失败不阻断构建）
RUN pip3 install --no-cache-dir --timeout 120 sqlmap 2>&1 \
    || echo "[WARN] sqlmap 安装失败，用户可在容器内手动执行: pip3 install sqlmap"

# 工作区目录（运行时由 baize 挂载宿主工作区到 /workspace）
RUN mkdir -p /workspace
WORKDIR /workspace

# 保持容器常驻（baize 以 --entrypoint sleep 启动时会覆盖此 CMD）
CMD ["sleep", "infinity"]

#!/usr/bin/env bash
#
# 白泽·智脑 (Baize) — 系统工具预装脚本
#
# 在最小化系统（如 Ubuntu Server）上一次性安装白泽 50+ 工具所依赖的
# 全部系统二进制，避免运行时「工具无法使用 / command not found」。
#
# 用法:
#   ./install-tools.sh                 # 全量安装（推荐，含浏览器）
#   ./install-tools.sh --yes           # 免交互（apt 自动 -y）
#   ./install-tools.sh --skip-browser  # 跳过浏览器依赖（无 GUI 服务器）
#   ./install-tools.sh --with-metasploit  # 额外安装 Metasploit（重量级）
#   ./install-tools.sh --make-offline <输出.tar.gz>   # 离线打包：在联网机器上
#                                                     # 收集全部工具包，供内网安装
#   ./install-tools.sh --offline <离线包.tar.gz>      # 内网离线安装（无需联网）
#
# 支持发行版: Debian / Ubuntu / Kali (apt)、CentOS / RHEL / Rocky / Alma (dnf/yum)、Arch (pacman)
# 其他发行版: 只输出手动安装指引，不中断。
#
# 内网部署：在能联网且与目标机同发行版、同架构的机器上执行
#   ./install-tools.sh --make-offline baize-tools-offline.tar.gz
# 把生成的包拷贝进内网后执行
#   ./install-tools.sh --offline baize-tools-offline.tar.gz
#
set -uo pipefail

C_CYAN='\033[0;36m'; C_GREEN='\033[0;32m'; C_YELLOW='\033[1;33m'; C_RED='\033[0;31m'; C_RESET='\033[0m'
log()  { echo -e "${C_GREEN}[tools]${C_RESET} $*"; }
info() { echo -e "${C_CYAN}[tools]${C_RESET} $*"; }
warn() { echo -e "${C_YELLOW}[tools]${C_RESET} $*"; }
err()  { echo -e "${C_RED}[tools]${C_RESET} $*"; }

ASSUME_YES=0; SKIP_BROWSER=0; WITH_METASPLOIT=0
MAKE_OFFLINE=""; OFFLINE_PKG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) ASSUME_YES=1 ;;
    --skip-browser) SKIP_BROWSER=1 ;;
    --with-metasploit) WITH_METASPLOIT=1 ;;
    --make-offline) shift; MAKE_OFFLINE="${1:-}"
      [[ -z "$MAKE_OFFLINE" ]] && { err "--make-offline 需要输出文件路径（如 baize-tools-offline.tar.gz）"; exit 1; } ;;
    --offline) shift; OFFLINE_PKG="${1:-}"
      [[ -z "$OFFLINE_PKG" ]] && { err "--offline 需要离线包路径"; exit 1; } ;;
    *) warn "忽略未知参数: $1" ;;
  esac
  shift
done

APT_OPTS=()
if [[ "$ASSUME_YES" == "1" ]]; then APT_OPTS=(-y); fi

# ---------------------------------------------------------------------------
# 发行版检测与包管理器
# ---------------------------------------------------------------------------
PM=""
install_pkgs() { :; }   # 由下面根据 PM 覆写
update_pkgs() { :; }

if command -v apt-get >/dev/null 2>&1; then
  PM="apt"
  update_pkgs() { apt-get update -qq; }
  install_pkgs() { apt-get install "${APT_OPTS[@]}" "$@" >/dev/null || { err "apt 安装失败: $*"; return 1; }; }
  info "检测到 apt 系发行版 (Debian/Ubuntu/Kali)"
elif command -v dnf >/dev/null 2>&1; then
  PM="dnf"
  update_pkgs() { dnf makecache -q; }
  install_pkgs() { dnf install -y "$@" >/dev/null || { err "dnf 安装失败: $*"; return 1; }; }
  info "检测到 dnf 系发行版 (RHEL/CentOS/Rocky/Alma)"
elif command -v yum >/dev/null 2>&1; then
  PM="yum"
  update_pkgs() { yum makecache -q; }
  install_pkgs() { yum install -y "$@" >/dev/null || { err "yum 安装失败: $*"; return 1; }; }
  info "检测到 yum 系发行版"
elif command -v pacman >/dev/null 2>&1; then
  PM="pacman"
  update_pkgs() { pacman -Sy --noconfirm >/dev/null; }
  install_pkgs() { pacman -S --noconfirm --needed "$@" >/dev/null || { err "pacman 安装失败: $*"; return 1; }; }
  info "检测到 pacman 系发行版 (Arch)"
else
  err "未识别发行版/包管理器，无法自动安装。"
  err "请手动安装以下工具后重试，或在 Debian/Ubuntu/Kali 上运行本脚本:"
  echo "  curl wget git jq unzip python3-pip openssl netcat-openbsd dnsutils whois traceroute bubblewrap"
  echo "  nmap masscan arp-scan tshark sqlmap nikto hydra john hashcat aircrack-ng binutils binwalk"
  echo "  nuclei httpx gobuster ffuf wafw00f (见 README「工具依赖」章节)"
  exit 1
fi

# ---------------------------------------------------------------------------
# 工具组定义（二进制名 -> 发行版包名）
# ---------------------------------------------------------------------------
# core: 基础能力，任何部署都应安装
CORE_PKGS=(curl wget git jq unzip openssl openssh-client netcat-openbsd dnsutils whois traceroute bubblewrap python3-pip)
# recon: 信息收集 / 网络扫描
RECON_PKGS=(nmap masscan arp-scan tshark)
# web: Web 安全测试（部分二进制需特殊渠道，见下方）
WEB_PKGS=(sqlmap nikto hydra)
# password: 口令 / 哈希
PASSWORD_PKGS=(john hashcat hashid)
# forensic: 取证 / 逆向
FORENSIC_PKGS=(binutils binwalk)
# wireless: 无线安全
WIRELESS_PKGS=(aircrack-ng)

# dnf/yum/pacman 与 apt 包名差异表（逐个覆盖）
overrides() {
  case "$PM" in
    apt)
      # Ubuntu ≥22.04 / Debian ≥11 / Kali 用 bind9-dnsutils（dnsutils 已改名）
      CORE_PKGS=(curl wget git jq unzip openssl openssh-client bind9-dnsutils whois traceroute bubblewrap python3-pip)
      ;;
    dnf|yum)
      CORE_PKGS=(curl wget git jq unzip openssl openssh-clients bind-utils whois traceroute bubblewrap python3-pip nmap-ncat)
      RECON_PKGS=(nmap masscan arp-scan wireshark-cli)
      WEB_PKGS=(sqlmap nikto hydra)
      PASSWORD_PKGS=(john john-the-ripper hashcat hashid)
      FORENSIC_PKGS=(binutils binwalk)
      WIRELESS_PKGS=(aircrack-ng)
      ;;
    pacman)
      CORE_PKGS=(curl wget git jq unzip openssl openssh bind-tools whois traceroute bubblewrap python-pip)
      RECON_PKGS=(nmap masscan arp-scan tshark)
      WEB_PKGS=(sqlmap nikto hydra)
      PASSWORD_PKGS=(john hashcat hashid)
      FORENSIC_PKGS=(binutils binwalk)
      WIRELESS_PKGS=(aircrack-ng)
      ;;
  esac
}
overrides

# ---------------------------------------------------------------------------
# 特殊渠道安装（官方仓库没有的二进制）
# ---------------------------------------------------------------------------
GH_DL() {  # gh_dl <目标目录> <repo> <asset_关键字> <安装名>  从 GitHub 最新 release 下载二进制
  local dest="$1" repo="$2" keyword="$3" name="$4" url tag asset
  if [[ "$dest" == "/usr/local/bin" ]] && command -v "$name" >/dev/null 2>&1; then
    log "  ✓ $name 已存在，跳过"; return 0
  fi
  [[ -f "$dest/$name" ]] && { log "  ✓ $name 已在离线包中，跳过"; return 0; }
  tag="$(curl -fsSL "https://api.github.com/repos/${repo}/releases/latest" 2>/dev/null | jq -r '.tag_name // empty')"
  [[ -z "$tag" ]] && { warn "  无法获取 ${repo} 最新版本，跳过 $name（可手动安装）"; return 1; }
  asset="$(curl -fsSL "https://api.github.com/repos/${repo}/releases/latest" 2>/dev/null | jq -r ".assets[].name" | grep -iE "linux.*(amd64|x86_64)" | grep -i "$keyword" | head -1)"
  [[ -z "$asset" ]] && { warn "  未找到适合本机的 ${repo} 安装包，跳过 $name"; return 1; }
  url="https://github.com/${repo}/releases/download/${tag}/${asset}"
  info "  下载 $name <- $url"
  local tmp; tmp="$(mktemp -d)"
  if (cd "$tmp" && curl -fsSL -o "pkg.tar.gz" "$url" && tar xzf pkg.tar.gz); then
    local binfile; binfile="$(find "$tmp" -maxdepth 2 -type f -name "$name" | head -1)"
    if [[ -n "$binfile" ]]; then
      install -m 0755 "$binfile" "$dest/$name" && log "  ✓ $name 已放入 $dest"
    else
      warn "  压缩包中未找到 $name 可执行文件，跳过"
    fi
  else
    warn "  下载/解压失败，跳过 $name"
  fi
  rm -rf "$tmp"
}

install_special() {
  log "安装特殊渠道工具 (nuclei / httpx / gobuster / ffuf / wafw00f)..."
  if command -v apt-get >/dev/null 2>&1; then
    # apt 系：优先尝试 apt 包（Kali 直接可用）
    for p in nuclei httpx gobuster ffuf wafw00f whatweb exploitdb; do
      if apt-cache show "$p" >/dev/null 2>&1; then
        install_pkgs "$p" && log "  ✓ $p (apt)"
      fi
    done
  fi
  command -v nuclei >/dev/null 2>&1 || GH_DL "/usr/local/bin" "projectdiscovery/nuclei" "amd64" "nuclei"
  command -v httpx  >/dev/null 2>&1 || GH_DL "/usr/local/bin" "projectdiscovery/httpx" "amd64" "httpx"
  command -v gobuster >/dev/null 2>&1 || GH_DL "/usr/local/bin" "OJ/gobuster" "linux" "gobuster"
  command -v ffuf   >/dev/null 2>&1 || GH_DL "/usr/local/bin" "ffuf/ffuf" "linux" "ffuf"
  if ! command -v wafw00f >/dev/null 2>&1; then
    info "  安装 wafw00f (pip)..."
    (python3 -m pip install --quiet --break-system-packages wafw00f 2>/dev/null || \
     python3 -m pip install --quiet --user wafw00f 2>/dev/null || \
     warn "  wafw00f 安装失败（可稍后手动 pip install wafw00f）")
    command -v wafw00f >/dev/null 2>&1 && log "  ✓ wafw00f 安装成功"
  fi
}

install_metasploit() {
  info "安装 Metasploit Framework (apt 官方源)..."
  if command -v msfconsole >/dev/null 2>&1; then log "  ✓ msfconsole 已存在"; return 0; fi
  if [[ "$PM" != "apt" ]]; then warn "  仅 apt 系支持自动安装 Metasploit，跳过（可参考 https://docs.metasploit.com/docs/using-metasploit/install.html）"; return 1; fi
  curl -fsSL https://apt.metasploit.com/metasploit-framework.gpg.key 2>/dev/null | gpg --dearmor >/usr/share/keyrings/metasploit-archive-keyring.gpg 2>/dev/null \
    || { warn "  下载 Metasploit 密钥失败，跳过"; return 1; }
  echo "deb [signed-by=/usr/share/keyrings/metasploit-archive-keyring.gpg] http://apt.metasploit.com/ bionic main" \
    >/etc/apt/sources.list.d/metasploit-framework.list
  apt-get update -qq 2>/dev/null || true
  install_pkgs metasploit-framework || warn "  Metasploit 安装失败"
}

install_browser() {
  info "安装浏览器依赖 (playwright + chromium)..."
  if command -v playwright >/dev/null 2>&1 || python3 -c "import playwright" 2>/dev/null; then
    python3 -m playwright install chromium --with-deps >/dev/null 2>&1 \
      || python3 -m playwright install chromium >/dev/null 2>&1 \
      || warn "  chromium 安装失败，可稍后手动执行: python3 -m playwright install chromium --with-deps"
  else
    (python3 -m pip install --quiet --break-system-packages playwright 2>/dev/null || \
     python3 -m pip install --quiet --user playwright 2>/dev/null) && \
      python3 -m playwright install chromium --with-deps >/dev/null 2>&1 \
      || warn "  playwright/chromium 安装失败（浏览器工具将不可用）"
  fi
  python3 -c "import playwright" 2>/dev/null && log "  ✓ playwright 就绪" || warn "  playwright 未安装"
}

# ---------------------------------------------------------------------------
# 分组安装
# ---------------------------------------------------------------------------
install_group() {
  local label="$1"; shift
  local pkgs=("$@")
  [[ ${#pkgs[@]} -eq 0 ]] && return 0
  local need=()
  for p in "${pkgs[@]}"; do
    if command -v "$p" >/dev/null 2>&1; then continue; fi
    need+=("$p")
  done
  if [[ ${#need[@]} -eq 0 ]]; then log "  [$label] 全部已安装"; return 0; fi
  log "安装 [$label]: ${need[*]}"
  install_pkgs "${need[@]}"
}

# ---------------------------------------------------------------------------
# 离线工具包：联网机器上收集全部工具 -> 一个 tar.gz，供内网离线安装
# ---------------------------------------------------------------------------
ALL_TOOLS=("${CORE_PKGS[@]}" "${RECON_PKGS[@]}" "${WEB_PKGS[@]}" \
           "${PASSWORD_PKGS[@]}" "${FORENSIC_PKGS[@]}" "${WIRELESS_PKGS[@]}")

pack_offline() {
  local out="$1"
  local work; work="$(mktemp -d)"
  local debdir="$work/debs" bindir="$work/bin" pipdir="$work/pip"
  mkdir -p "$debdir" "$bindir" "$pipdir"
  info "== 生成离线工具包（务必在「与目标机同发行版、同架构」且能联网的机器上执行）=="

  # 1) 系统包 + 依赖（apt 用 download-only 自动收集依赖树）
  case "$PM" in
    apt)
      update_pkgs || true
      info "下载 ${#ALL_TOOLS[@]} 个工具包及依赖 (apt download-only)..."
      apt-get install --download-only --no-install-recommends -y \
          -o Dir::Cache::archives="$debdir" "${ALL_TOOLS[@]}" >/dev/null 2>&1 \
        || { err "apt 下载失败（请检查网络 / 包源 / 包名）"; return 1; }
      local n; n="$(find "$debdir" -name '*.deb' | wc -l)"
      info "  已收集 $n 个 deb 包"
      ;;
    dnf|yum)
      update_pkgs || true
      (command -v dnf >/dev/null && dnf download --destdir="$debdir" "${ALL_TOOLS[@]}" >/dev/null 2>&1) \
        || (command -v yum >/dev/null && yumdownloader --destdir="$debdir" "${ALL_TOOLS[@]}" >/dev/null 2>&1) \
        || { err "dnf/yum 下载失败（需要 dnf download / yumdownloader 插件）"; return 1; }
      ;;
    pacman)
      update_pkgs || true
      pacman -Sw --noconfirm --cachedir="$debdir" "${ALL_TOOLS[@]}" >/dev/null 2>&1 \
        || { err "pacman 下载失败"; return 1; }
      ;;
  esac

  # 2) 特殊渠道二进制（nuclei / httpx / gobuster / ffuf，静态 Go 二进制）
  info "下载特殊渠道二进制 (nuclei/httpx/gobuster/ffuf)..."
  GH_DL "$bindir" "projectdiscovery/nuclei" "amd64" "nuclei"
  GH_DL "$bindir" "projectdiscovery/httpx" "amd64" "httpx"
  GH_DL "$bindir" "OJ/gobuster" "linux" "gobuster"
  GH_DL "$bindir" "ffuf/ffuf" "linux" "ffuf"

  # 3) wafw00f 及其依赖 (pip wheel，离线 --no-index 安装)
  info "下载 wafw00f (pip wheel)..."
  (python3 -m pip download --break-system-packages -d "$pipdir" wafw00f >/dev/null 2>&1) \
    || (python3 -m pip download -d "$pipdir" wafw00f >/dev/null 2>&1) \
    || warn "  wafw00f 下载失败（可忽略，内网仅此工具不可用）"

  # 4) 打包
  (cd "$work" && tar czf "$out" .) || { err "打包失败"; return 1; }
  local size; size="$(du -sh "$out" | cut -f1)"
  log "离线工具包已生成: $(realpath "$out") ($size)"
  warn "浏览器 (playwright/chromium) 与 Metasploit 体积过大未包含，内网如需浏览器工具请单独处理。"
  info "拷贝到内网机器后执行: ./install-tools.sh --offline $(basename "$out")"
  rm -rf "$work"
}

# ---------------------------------------------------------------------------
# 离线安装：内网环境从离线工具包安装全部工具（全程不联网）
# ---------------------------------------------------------------------------
install_offline() {
  local pkg="$1"
  [[ -f "$pkg" ]] || { err "找不到离线工具包: $pkg"; return 1; }
  local work; work="$(mktemp -d)"
  info "解包离线工具包..."
  tar xzf "$pkg" -C "$work" || { err "解包失败（请确认是 --make-offline 生成的包）"; return 1; }
  local debdir="$work/debs" bindir="$work/bin" pipdir="$work/pip"

  case "$PM" in
    apt)
      if compgen -G "$debdir/*.deb" >/dev/null 2>&1; then
        info "离线安装 apt 包（本地解析依赖，不访问网络）..."
        (cd "$debdir" && apt-get install -y ./*.deb) \
          || { err "apt 离线安装失败：请确认离线包与目标机发行版/架构一致，并检查上方依赖报错"; rm -rf "$work"; return 1; }
      else
        warn "  离线包中无 deb（发行版不匹配？）"
      fi
      ;;
    dnf|yum)
      if compgen -G "$debdir/*.rpm" >/dev/null 2>&1; then
        dnf install -y "$debdir"/*.rpm || { err "dnf 离线安装失败"; rm -rf "$work"; return 1; }
      fi
      ;;
    pacman)
      if compgen -G "$debdir/*.pkg.tar.*" >/dev/null 2>&1; then
        pacman -U --noconfirm "$debdir"/*.pkg.tar.* || { err "pacman 离线安装失败"; rm -rf "$work"; return 1; }
      fi
      ;;
  esac

  # 特殊渠道二进制 -> /usr/local/bin
  if compgen -G "$bindir/*" >/dev/null 2>&1; then
    for f in "$bindir"/*; do
      [[ -f "$f" ]] && { install -m 0755 "$f" "/usr/local/bin/$(basename "$f")" && log "  ✓ $(basename "$f")"; }
    done
  fi

  # wafw00f (离线 wheel)
  if compgen -G "$pipdir/*" >/dev/null 2>&1; then
    info "离线安装 wafw00f (pip --no-index)..."
    (python3 -m pip install --no-index --find-links "$pipdir" --break-system-packages wafw00f >/dev/null 2>&1) \
      || (python3 -m pip install --no-index --find-links "$pipdir" --user wafw00f >/dev/null 2>&1) \
      || warn "  wafw00f 离线安装失败"
  fi
  rm -rf "$work"
}

# ---------------------------------------------------------------------------
# 验证结果
# ---------------------------------------------------------------------------
verify_tools() {
  echo ""
  echo -e "${C_CYAN}═══════════════════════════════════════════════════════════${C_RESET}"
  echo -e "${C_CYAN}  工具安装结果验证${C_RESET}"
  echo -e "${C_CYAN}═══════════════════════════════════════════════════════════${C_RESET}"
  local BINARIES=(curl wget git jq unzip openssl ssh nc dig whois traceroute bwrap \
            nmap masscan arp-scan tshark sqlmap nikto hydra john hashcat hashid \
            strings binwalk exiftool aircrack-ng nuclei httpx gobuster ffuf wafw00f)
  local MISSING=()
  local b
  for b in "${BINARIES[@]}"; do
    if command -v "$b" >/dev/null 2>&1; then
      printf "  ${C_GREEN}✓${C_RESET} %-14s %s\n" "$b" "$(command -v "$b")"
    else
      printf "  ${C_RED}✗${C_RESET} %-14s 未安装\n" "$b"
      MISSING+=("$b")
    fi
  done
  echo ""
  if [[ ${#MISSING[@]} -eq 0 ]]; then
    log "全部核心工具已就绪 ✓"
  else
    warn "以下工具缺失: ${MISSING[*]}"
    warn "可重新运行 ./install-tools.sh，或运行 ./setup.sh --with-tools；安装白泽后可用 baize doctor 复查。"
  fi
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
if [[ -n "$MAKE_OFFLINE" ]]; then
  pack_offline "$MAKE_OFFLINE"
  exit $?
fi
if [[ -n "$OFFLINE_PKG" ]]; then
  install_offline "$OFFLINE_PKG"
  verify_tools
  exit $?
fi

info "白泽工具依赖预装开始 (包管理器: $PM)"
update_pkgs || warn "包源更新失败，继续尝试安装"

log "== 核心工具 ==";        install_group "core" "${CORE_PKGS[@]}"
log "== 信息收集/网络 ==";    install_group "recon" "${RECON_PKGS[@]}"
log "== Web 安全 ==";         install_group "web" "${WEB_PKGS[@]}"
log "== 口令/哈希 ==";        install_group "password" "${PASSWORD_PKGS[@]}"
log "== 取证 ==";             install_group "forensic" "${FORENSIC_PKGS[@]}"
log "== 无线 ==";             install_group "wireless" "${WIRELESS_PKGS[@]}"
install_special
[[ "$WITH_METASPLOIT" == "1" ]] && install_metasploit
[[ "$SKIP_BROWSER" == "0" ]] && install_browser

verify_tools

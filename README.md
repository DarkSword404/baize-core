<div align="center">

<img src="docs/img/favicon.png" width="92" alt="白泽·智脑 logo" />

# 🦄 白泽·智脑 (Baize)

**AI 驱动的多智能体安全操作平台**

> 内置 30+ 专业安全智能体 · 本地化部署 · LLM 无关

[![Version](https://img.shields.io/badge/version-v2.0.0-4C9F38?style=flat-square&logo=github)](https://github.com/DarkSword404/baize-core)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Research_Only-8B5CF6?style=flat-square)](LICENSE)

**白泽·智脑** 是一个基于大型语言模型的本地化 AI 安全助手，面向 **Web 渗透、移动安全、无线/射频、红蓝对抗、应急响应与合规审计** 等场景，提供 30+ 个开箱即用的专用智能体，支持长期记忆、安全护栏、沙箱边界与外部事件接入。

</div>

---

## ✨ 核心特性

| | 特性 | 说明 |
|---|---|---|
| 🧠 | **多智能体协同** | 30+ 专业安全智能体，按需调度、协同作战；Swarm 集群协作模式支持智能体间动态 Handoff |
| 🔌 | **LLM 无关** | 兼容 OpenAI / DeepSeek / 通义千问 / Ollama 等 OpenAI 协议端点，模型热切换 + 多模型路由 fallback |
| 🛠️ | **工具调用** | 内置 50+ 工具（30+ 安全专用 + 静默浏览器 + 8 个共享协作浏览器），智能体自主调用 |
| ⛓️ | **沙箱边界（v1.7.0）** | 安全区 / 危险区工具划分，危险工具执行前审批（`allow` / `approve` / `deny` 三级权限），会话级审批令牌 |
| 🗜️ | **工具输出压缩（v1.7.0）** | 大体积工具输出 LLM 语义摘要，token 压缩 80%+，启动自动注入各 Agent |
| 🖼️ | **多模态图片注入（v1.7.0）** | 工具输出中的截图 / 图片自动提取，构造多模态消息注入 LLM 上下文 |
| 🌐 | **双浏览器体系（v1.6.0）** | 有头共享协作浏览器（人机共用、实时可交互面板、登录态持久化）+ 无头静默浏览器（流水线无人值守） |
| 🔗 | **流水线接入（v1.5.0）** | `baize-orchestration` 模块 entry point 自动加载，会话绑定流水线自动走编排 runner |
| 🧰 | **自定义工具（v1.4.0）** | Web「工具」页在线创建/编辑自定义工具，启动热注册，无需改代码 |
| 🧩 | **Agent 扩展（v1.4.0）** | `state` 运行时状态、`memory` 记忆注入、`hooks` 瀑布式事件链 |
| 🖥️ | **执行环境抽象（v1.4.0）** | 统一执行器接口 + 沙箱隔离，工具可无缝切换 local / docker / ssh 后端 |
| 📋 | **会话日志（v1.4.0）** | append-only 审计日志，模型历史可重建、攻击链可重放（DFIR 取证） |
| 💾 | **记忆库（v1.8.0）** | 长期记忆升级为结构化记忆库：经验 / 任务轨迹 / 事实 / 实体分层沉淀，知识图谱关联，混合检索自动命中 |
| 📨 | **告警研判收件箱（v2.0.0）** | Webhook / Syslog / 文件监听入站告警统一落 SQLite 持久化收件箱（指纹幂等 / 租约消费 / 退避重试 / 死信），编排长驻会话自动 claim 研判 |
| 🔄 | **流水线模板→实例（v2.0.0）** | 编排升级两级模型：模板实例化快照、绑定接收器、并行上限（默认 10），启用/停用与运行历史；每次入站 = 独立 run / 独立对话 |
| 🛡️ | **护栏 Guardrails（v1.3.0）** | 文件化可配置的输入/输出策略，注入防护、敏感信息保护与 SSRF 运行时配置 |
| 📡 | **外部接收器（v1.3.0）** | Webhook / Syslog / 文件监听，打通外部事件源 |
| ⚡ | **实时交互** | SSE 流式输出、会话持久化、美观的暗色 UI |
| 🔒 | **本地化部署** | 全部数据保存在本地，隐私安全可控 |

---

## 🏗️ 技术栈

| 层 | 技术 |
|---|---|
| **后端** | Python 3.11+ · FastAPI · Uvicorn · Pydantic v2 |
| **前端** | React 18 · TypeScript · Vite · Tailwind CSS |
| **浏览器** | Playwright（静默浏览器 + 有头共享协作浏览器） |
| **向量检索** | OpenAI 协议 Embedding（如 `qwen3.7-text-embedding`，1024 维） |
| **存储** | 本地目录（会话 / 经验 / 护栏策略均落盘） |

---

## 🚀 快速开始

### 环境要求

- **Python** 3.11+
- **Node.js** 18+ / npm 9+
- 可用的 **OpenAI 协议 LLM 端点**（OpenAI / DeepSeek / 通义千问 / Ollama 等）

### 安装

```bash
cd baize-core-v2.0.0
./setup.sh    # 创建虚拟环境 + 安装 baize-core + 构建前端
./setup.sh --with-tools   # 推荐：安装时一并预装全部工具依赖（见下节）
```

> **最小化系统（如 Ubuntu Server）提示**：白泽的 50+ 渗透/信息收集工具依赖大量系统二进制
> （nmap / sqlmap / nuclei / hydra 等）。请务必在安装时执行 `./setup.sh --with-tools`，
> 或单独运行 `./install-tools.sh --yes` 一键预装，否则相关工具在被调用时会报
> `command not found`，无法使用。

### 配置模型

模型配置保存在用户目录 `~/.baize/model.json`（Web「设置」页自动生成与管理）：

```json
{
  "base_url": "https://api.deepseek.com/v1",
  "api_key": "sk-xxx",
  "model": "deepseek-chat"
}
```

首次使用需在 Web 界面「设置」页完成模型配置与管理员初始化。

### 启动 / 停止

```bash
./start.sh    # 启动后端 (8001) + 前端 (5173)
./stop.sh     # 停止全部服务
```

- **Web 界面**：<http://localhost:5173>
- **健康检查**：<http://localhost:8001/api/v1/health>

### 工具依赖预装（install-tools.sh）

```bash
./install-tools.sh                 # 全量安装（core/recon/web/password/forensic/wireless + 浏览器）
./install-tools.sh --yes           # 免交互（CI / 无人值守）
./install-tools.sh --skip-browser  # 跳过浏览器依赖（无 GUI 服务器）
./install-tools.sh --with-metasploit  # 额外安装 Metasploit（重量级）
```

- 自动检测发行版：apt（Debian/Ubuntu/Kali）、dnf/yum（RHEL 系）、pacman（Arch）
- 覆盖工具：nmap、masscan、arp-scan、tshark、sqlmap、nikto、hydra、john、hashcat、
  aircrack-ng、binwalk、exiftool、dig、whois、traceroute、bubblewrap 等，以及
  ProjectDiscovery 渠道的 **nuclei / httpx**、**gobuster / ffuf**、**wafw00f**（pip）与
  **Chromium**（playwright，浏览器工具所需）
- 幂等：已安装的工具自动跳过；单个失败不中断整体；结束输出安装结果验证清单

#### 内网 / 离线部署（--make-offline / --offline）

无法访问外网（无 apt 源 / GitHub / PyPI）的内网环境，先用**能联网且与目标机同发行版、
同架构**的机器打一个离线工具包，拷贝进内网后离线安装，全程不联网：

```bash
# ① 联网机器上打包（示例：Ubuntu 22.04 amd64 上给同配置内网机打包）
./install-tools.sh --make-offline baize-tools-offline.tar.gz

# ② 把包拷贝到内网目标机，离线安装
./install-tools.sh --offline baize-tools-offline.tar.gz
```

- 包内收集：全部系统工具包**及其依赖树**（apt 自动收集，解压后 apt 本地安装）+ 静态
  Go 二进制（nuclei/httpx/gobuster/ffuf，装入 `/usr/local/bin`）+ wafw00f 的 pip wheel
- **浏览器（playwright/chromium）与 Metasploit 体积过大未包含**，内网如需浏览器工具
  请单独处理；Metasploit 需离线 deb 源
- 注意：包与目标机必须**发行版一致、架构一致**（如都是 Ubuntu 22.04 x86_64），
  否则 apt 依赖解析会失败

### 环境自检（baize doctor）

部署后可用 `baize doctor` 一键体检，输出每项依赖的就绪/缺失状态与修复命令：

```bash
.venv/bin/baize doctor
# 系统工具 / 运行时 / LLM 模型配置 / 经验向量化配置 / 浏览器依赖
```

### 排障：报错信息说明

对话/流水线出错时，Web 界面会直接显示**详细原因**（异常类型、LLM 端点、HTTP 状态码、
响应内容与排查建议），不再只是"服务器内部错误，请查看服务端日志"；完整 traceback
仍记录在 `logs/backend.log`。常见类型：

| 报错特征 | 含义 | 处理 |
| --- | --- | --- |
| `LLM API 连接异常 [ConnectError]` | 连不上模型端点 | 检查 base_url 连通性、防火墙/代理 |
| `LLM API 返回 HTTP 401/403` | api_key 无效/权限不足 | Web「设置」页核对 api_key |
| `LLM API 返回 HTTP 429` | 触发限流 | 稍后重试或降低并发 |
| `LLM API 返回 HTTP 5xx` | LLM 服务端故障 | 检查 LLM 服务日志 |
| `工具 xxx 执行失败 ... not found` | 系统二进制缺失 | 运行 `./install-tools.sh --yes` 预装 |

---

## ⚙️ 模型配置详解

### 多模型配置字段

| 字段 | 说明 |
|---|---|
| `base_url` | OpenAI 协议端点地址 |
| `api_key` | API 密钥 |
| `model` | 模型名称 |
| `context_max_turns` | 上下文滑动窗口轮数（0 = 不限制） |
| `context_window` | 模型上下文窗口大小（token），自动推导 token 预算 |
| `max_context_tokens` | 上下文 token 预算上限（0 = 不限制） |
| `max_message_chars` | 单条消息最大字符数（0 = 不限制） |
| `enable_context_summary` | 超预算时用 LLM 压缩历史为摘要 |

### 支持提供商

| Provider | 说明 |
|---|---|
| **OpenAI** | 官方 OpenAI API |
| **DeepSeek** | DeepSeek Chat / Reasoner |
| **通义千问** | 阿里云百炼 |
| **Ollama** | 本地 Ollama 服务 |

---

## 🖥️ Web 界面

| 页面 | 功能 |
|---|---|
| 🖥️ **控制台** | 平台概览、会话 / 智能体 / 工具状态统计 |
| 💬 **对话渗透** | 多智能体对话（SSE 流式）、会话级共享浏览器协作侧窗、经验一键提炼 |
| 🔗 **流水线** | 编排流水线编辑与运行（需接入 `baize-orchestration` 模块） |
| 🤖 **智能体** | 30+ 智能体浏览与切换、自定义智能体创建、Swarm 协作模式 |
| 🛠️ **工具管理** | 内置工具查看、自定义工具在线创建 / 编辑 / 测试 / 启停 |
| 📋 **会话管理** | 多会话浏览、重命名、删除、历史检索 |
| 🛡️ **安全护栏** | 注入 / 敏感信息 / 输出合规策略开关、SSRF 防护运行时配置、沙箱权限面板 |
| 💾 **经验库** | 经验浏览、编辑、删除、重新向量化 |
| ⚙️ **设置** | 模型配置（多提供商）、管理员初始化、数据目录 |

---

## 🤖 智能体体系

内置 **30+** 安全智能体，覆盖以下方向：

| 方向 | 智能体 |
|---|---|
| 🌐 **Web 渗透** | Web 渗透 · Web 赏金猎人 · 代码审计 · 漏洞复现 |
| 📱 **移动安全** | Android 静态审计 · Android 业务逻辑测绘 |
| 📶 **无线 / 射频** | Wi-Fi 安全 · SubGHz 射频 · 重放攻击 |
| 🔴 **红队 / 对抗** | 红队 · 漏洞利用专家 · APT 模拟 · 攻防博弈 |
| 🔵 **蓝队 / 应急** | 蓝队 · DFIR · 内存分析 · 网络分析 · DNS/SMTP |
| 🏁 **CTF** | CTF 解题 · Flag 判别 · 挑战策略 |
| 📋 **合规 / 报告** | 合规审计 · 安全报告 · 运维值守 |
| 🔗 **协同支撑** | 分流 · 任务选择 · 推理支撑 · 思维路由 · 经验沉淀 · 方案对抗 |

- **Swarm 集群协作**：Selection / Orchestration 智能体以 `pattern_type='swarm'` 注册，支持红队集群协作、智能体间动态 Handoff
- 所有智能体共享工具调用能力，可通过「智能体」页查看与切换

---

## 🛠️ 工具体系

### 安全工具

内置 **30+** 安全工具（`ToolSpec` + `@register_tool` 动态注册，全部含危险参数黑名单拦截）：

- **核心 9 个**：nmap / nuclei / nikto / sqlmap / gobuster / hydra / tshark / hashcat / metasploit
- **信息收集**：whois / dig / crt.sh / httpx / openssl / whatweb / wafw00f
- **漏洞研究**：searchsploit / NVD CVE 查询
- **爆破枚举**：ffuf / arp-scan / masscan / traceroute
- **无线 / 取证**：airodump / aircrack / exiftool / strings / binwalk / john / hashid 等

### 自定义工具（v1.4.0）

- Web「工具」页在线创建 / 编辑自定义工具（名称、描述、参数 Schema、执行命令），无需改代码
- 启动热注册，支持启停切换与在线测试
- entry point 插件自动发现（`baize.tools` 组），`pip install` 即接入

### 双浏览器体系（v1.6.0）

两套相互独立的浏览器工具，按场景选用：

#### 🕹️ 共享协作浏览器 `shared_browser_*` — 人机共用、可视化

面向**需要登录 / 验证码 / 人工确认**的目标（如渗透测试登录后扫描）：

- **有头可视化**：默认以有头模式启动 Chromium（窗口可见），人工可直接在桌面上观察并操作**同一窗口**（扫码登录、输入验证码、绕过验证、点击确认）
- **登录态持久化**：`launch_persistent_context` + 固定 `user_data_dir`（默认 `~/.baize/shared-browser-profile`），Cookie / LocalStorage 跨会话保留——人工登录一次，AI 后续操作全部复用登录态
- **人工介入工具**：`shared_browser_wait_user` 让 AI 打开登录页后**阻塞等待**，可配置 `success_url_prefix` 检测登录成功跳转自动放行，或由前端面板点击「我已完成」手动放行
- **🎮 实时可交互面板（v1.6.0）**：Web 端共享浏览器面板实时展示浏览器画面（固定视口 1366×768，截图轮询），支持**直接在面板内操作浏览器**——点击 / 滚轮 / 键盘输入 / 前进后退刷新，交互坐标自动映射到浏览器视口
- **🔗 对话绑定（v1.6.0）**：共享浏览器与对话会话绑定，创建会话时开启「共享浏览器协作」开关后 AI 才会注入 `shared_browser_*` 工具；对话侧窗口实时展示浏览器状态
- 无图形界面环境自动降级无头（可用 `BAIZE_SHARED_BROWSER_HEADLESS=1` 强制）

典型流程：`shared_browser_open(登录页)` → `shared_browser_wait_user("请扫码登录", success_url_prefix="https://target/console")` → 人工扫码 → AI 复用登录态继续。

工具清单：`shared_browser_open` / `shared_browser_wait_user` / `shared_browser_snapshot` / `shared_browser_click` / `shared_browser_fill` / `shared_browser_evaluate` / `shared_browser_status` / `shared_browser_close`

#### 🤖 静默浏览器 `browser_*` — 无头、可中断、供流水线静默运行

面向**无需人工介入**的自动化侦察（流水线无人值守场景）：

- **无头静默**：每次调用独立 Chromium 实例，无窗口不打扰，用完即关
- **可中断**：任务被取消 / 超时（单次工具 5 分钟上限）时，浏览器进程在后台被可靠回收（`asyncio.shield` 保护清理），不泄漏、不阻塞流水线
- **SSRF 防护**：禁止访问内网 / 保留地址（除非 `BAIZE_FETCH_ALLOW_INTERNAL=1`）

工具清单：`browser_fetch` / `browser_screenshot` / `browser_click` / `browser_fill` / `browser_evaluate`

> **依赖**：需安装 `playwright` 并执行 `playwright install chromium`。
> 未安装时工具 fail-closed，返回明确提示。

---

## 💾 经验系统（v1.3.0）

为白泽·智脑提供 **长期记忆** 能力：把解决问题的方法沉淀为经验，并在后续对话中自动检索复用。

### 工作流程

1. **沉淀** — 会话结束后可一键「提炼本会话经验」，或由系统自动检测可沉淀内容
2. **向量化** — 经验保存时自动生成 Embedding（1024 维）入库，无需手动重建索引
3. **命中** — 新问题时按语义相似度检索相关经验，附带到上下文供模型参考

### 特性

- 经验支持标题、正文、标签，自动向量化存储
- 检索门槛可配置（默认相似度 ≥ 0.5，避免无关内容误命中）
- Web 端「经验」页可浏览、编辑、删除历史经验，支持手动重新向量化

---

## 🛡️ 护栏 Guardrails（v1.3.0）

以**文件化策略**对模型输入/输出进行前置校验：

- **Prompt 注入防护** — 识别并拦截指令注入尝试
- **敏感信息保护** — 检测身份证号、手机号、密钥等敏感信息
- **输出合规校验** — 拒绝协助非法操作（如未授权渗透、恶意软件编写）
- **SSRF 防护运行时配置（v1.6.0）** — 按会话启用 / 关闭，细粒度控制私网 / 回环 / 链路本地 / 保留地址阻断与 CIDR / 域名白名单，即时生效并 JSON 持久化
- **自定义策略** — 以 YAML 文件定义关键词规则，热加载生效

Web 端「护栏」页可查看、开关各项策略；策略文件位于 `prompts/`。

---

## ⛓️ 沙箱边界（v1.7.0）

在护栏基础上增加**显式沙箱边界**：将工具划分为「安全区」与「危险区」，危险区工具执行前需审批。

- **三级权限**：`allow`（直接允许）/ `approve`（需要审批）/ `deny`（禁止）
- **审批门控**：危险工具首次调用触发审批，通过后发放审批令牌，会话内后续调用自动放行
- **会话级状态**：审批结果按会话持久化，重启不丢失
- **策略可配置**：默认危险工具需审批，支持按工具覆盖权限
- **执行统计**：按会话记录危险工具审批 / 拒绝 / 放行统计

```bash
POST /api/v1/sandbox/check            # 检查工具是否允许执行（返回 allow/approve/deny）
POST /api/v1/sandbox/approve          # 审批通过危险工具（发放会话级审批令牌）
POST /api/v1/sandbox/deny             # 拒绝危险工具执行
GET  /api/v1/sandbox/stats/{session}  # 会话沙箱执行统计
```

---

## 📡 外部接收器（v1.3.0）

打通外部事件源，自动触发智能体响应：

- **Webhook** — 外部系统通过 `POST /api/v1/hook` 推送事件
- **Syslog** — 接收网络设备/服务器日志
- **文件监听** — 监控指定目录新文件自动处理

---

## 🧠 智能增强（v1.7.0）

围绕「更省、更稳」增强核心引擎：

### 🖼️ 多模态图片自动注入

扫描本轮工具输出中引用的图片文件路径（截图、扫描结果图等），自动提取并构造多模态用户消息注入 LLM 上下文——单张最大 5MB，data URL 约 1.37x 原始大小，避免工具截图 / 图片结果丢失；构建消息时输出 content_parts / 图片数概要日志，便于排查多模态链路问题。

### 🗜️ 工具输出压缩器（TokenJuice 风格）

大体积工具输出（如扫描报告、页面源码）先经 LLM **语义摘要**再进入上下文，保留关键信息的同时 token 压缩 80%+，避免简单截断丢失重要数据；启动时自动注入各 Agent。

---

## 🔌 API 一览

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/v1/health` | GET | 健康检查（含版本号） |
| `/api/v1/auth/login` | POST | 管理员登录 |
| `/api/v1/agents` | GET | 智能体列表 |
| `/api/v1/agents/custom` | POST | 创建自定义智能体 |
| `/api/v1/models` | GET | 模型列表 |
| `/api/v1/model-config` | GET/PUT | 模型配置（多模型 + fallback） |
| `/api/v1/sessions` | GET/POST/DELETE | 会话管理 |
| `/api/v1/sessions/{id}/messages/stream` | POST | SSE 流式对话（含图片注入） |
| `/api/v1/sessions/{id}/reset` / `interrupt` / `cancel` | POST | 会话重置 / 中断 / 取消 |
| `/api/v1/sessions/{id}/browser-collab` | PATCH | 会话浏览器协作开关 |
| `/api/v1/sessions/{id}/experience/refine` | POST | 提炼本会话经验 |
| `/api/v1/experiences` | GET/POST/DELETE | 经验系统（v1.3.0） |
| `/api/v1/experiences/reindex` | POST | 经验重新向量化 |
| `/api/v1/guardrails` | GET/PUT | 护栏策略，含 SSRF 防护运行时配置（v1.3.0 / v1.6.0） |
| `/api/v1/guardrails/sandbox` | GET/PUT | 沙箱权限策略 |
| `/api/v1/sandbox/check` / `approve` / `deny` | POST | 沙箱审批流（v1.7.0） |
| `/api/v1/sandbox/stats/{session}` | GET | 沙箱执行统计（v1.7.0） |
| `/api/v1/tools` | GET | 工具列表 |
| `/api/v1/tools/custom` | POST | 创建自定义工具（v1.4.0） |
| `/api/v1/hook` | POST | 接收器 Webhook 入口 |
| `/api/v1/pipelines` | GET/POST | 流水线（v1.5.0，baize-orchestration） |
| `/api/v1/shared-browser/status` | GET | 共享浏览器状态（v1.6.0，含视口信息） |
| `/api/v1/shared-browser/open` / `confirm` | POST | 打开 URL / 人工确认放行 |
| `/api/v1/shared-browser/snapshot` | GET | 共享浏览器实时截图（PNG） |
| `/api/v1/shared-browser/click` / `type` / `key` / `scroll` / `nav` | POST | 面板内实时操作（v1.6.0） |
| `/api/v1/shared-browser/close` | POST | 关闭共享浏览器（保留登录态） |
| `/api/v1/shared-browser/stream` | WS | 浏览器实时画面流 |
| `/api/v1/ux/title` / `ux/summarize` | POST | 会话标题生成 / 内容摘要 |

---

## 🏛️ 架构

```
┌───────────────────────────────────────────────────────────┐
│                      Web 前端 (React)                      │
│   控制台 / 对话 / 流水线 / 智能体 / 工具 / 会话 / 护栏 /     │
│        经验 / 设置 · 共享浏览器可交互面板                    │
└──────────────────────────┬────────────────────────────────┘
                           │ SSE / REST / WebSocket
┌──────────────────────────▼────────────────────────────────┐
│                    baize-core（核心模块）                    │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────────────┐  │
│  │   会话管理    │ │  智能体调度    │ │ 经验系统(向量检索)   │  │
│  └──────────────┘ └──────────────┘ └────────────────────┘  │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────────────┐  │
│  │   工具调用    │ │ 护栏+沙箱边界 │ │ 接收器(webhook/…)   │  │
│  └──────────────┘ └──────────────┘ └────────────────────┘  │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────────────┐  │
│  │ 压缩器/服务注册表 │ │  多模态注入  │ │                     │  │
│  └──────────────┘ └──────────────┘ └────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 📁 目录结构

```
baize-core/
├── src/baize/
│   ├── agents/           # 30+ 安全智能体（含编排 / swarm / 方案对抗）+ 护栏策略实现
│   ├── api/              # FastAPI 路由
│   ├── experiences/      # 经验系统（embedding / retriever / store / refine）
│   ├── receivers/        # 外部接收器（Webhook / Syslog / 文件监听）
│   ├── sdk/              # SDK 与协议封装（models / memory / agent / session_log）
│   ├── tools/            # 安全工具集（registry / security / extended / browser / shared_browser）
│   ├── compressor.py     # 工具输出压缩器（v1.7.0）
│   ├── multimodal.py     # 多模态图片注入（v1.7.0）
│   ├── sandbox.py        # 沙箱边界系统（v1.7.0）
│   ├── services.py       # 全局服务注册表（v1.7.0）
│   ├── executors.py      # 执行环境抽象（local / docker / ssh + 沙箱 fail-closed）
│   ├── config.py         # 全局配置（模型 / 数据目录 / 环境变量）
│   ├── cli.py            # 命令行入口
│   └── util/             # 通用工具
├── web/                  # React 前端
├── prompts/              # 提示词与护栏策略文件
├── docs/                 # 文档（EXTENDING.md / PLUGIN_MARKET.md）
├── examples/             # 插件示例（security-tools-plugin）
├── setup.sh              # 环境安装
└── start.sh / stop.sh    # 启停脚本
```

### 扩展开发

- **工具协议 / 模型抽象 / Agent 扩展 / 执行器** → [docs/EXTENDING.md](docs/EXTENDING.md)
- **插件市场（entry point 机制）** → [docs/PLUGIN_MARKET.md](docs/PLUGIN_MARKET.md)
- **可运行插件示例** → [examples/security-tools-plugin/](examples/security-tools-plugin/)

---

## 🗺️ Roadmap

- [x] **v1.0** 多智能体基础框架
- [x] **v1.1** 工具调用与模型热切换
- [x] **v1.2** 会话管理 + 前端界面优化
- [x] **v1.3** 经验系统（长期记忆）+ 护栏 + 外部接收器
- [x] **v1.4** 自定义工具系统 + Agent 稳定性优化
- [x] **v1.5** 稳定性加固（LLM 重试 / 工具超时 / 异常隔离）+ Swarm 协作 + 流水线接入 + 双浏览器
- [x] **v1.6** 共享浏览器实时可交互面板 + 对话绑定 + SSRF 护栏配置 + 数据目录可配置
- [x] **v1.7** 沙箱边界 + 工具输出压缩 + 多模态图片注入
- [x] **v1.8** 记忆库（经验/轨迹/知识图谱/混合检索）+ 大附件与内存镜像上传
- [x] **v2.0** 编排流水线两级模型 + 告警收件箱长驻自动研判（模块 v1.6.0）

---

## ❓ 常见问题

<details>
<summary><b>Q1：启动后访问 5173 页面打不开？</b></summary>

确认 `npm install` 已完成，前端使用开发服务器，首次启动需要数秒编译。
</details>

<details>
<summary><b>Q2：对话无回复 / 报模型错误？</b></summary>

检查 `~/.baize/model.json` 中 `base_url`、`api_key`、`model` 是否正确，并在「设置」页重新选择模型。
</details>

<details>
<summary><b>Q3：经验一直没有命中？</b></summary>

确认已启用 Embedding 模型（如 `qwen3.7-text-embedding`），新保存的经验会自动生成向量；历史经验可在「经验」页手动触发重新向量化。
</details>

<details>
<summary><b>Q4：共享浏览器提示依赖缺失？</b></summary>

确认已安装 `playwright` 并执行 `playwright install chromium`；未安装时浏览器工具 fail-closed，返回明确提示。
</details>

---

## ⚠️ 安全声明

> 白泽·智脑定位为 **安全研究、授权测试与教育培训** 工具。请确保：
>
> - 所有测试目标均已获得 **明确书面授权**
> - 遵守当地法律法规与目标组织的安全策略
> - 开发者与贡献者不对任何非法使用承担连带责任
>
> **本项目仅限授权环境使用。**

---

## 📝 更新日志

### v2.0.0（当前）

- 📨 **告警持久化收件箱（Alert Inbox）**：Webhook / Syslog / 文件监听等入站数据不再只进内存队列，
  先落 SQLite 持久化收件箱（`src/baize/receivers/inbox.py`）——按接收器指纹幂等（重复投递返回既有 seq）、
  租约消费（claim → lease）、失败退避重试与超限死信；进程崩溃/重启后过期租约自动回收重派，告警不丢
- 🔄 **编排流水线升级两级模型（配合 baize-orchestration v1.6.0）**：模板（图编排定义）→ 实例
  （可启用/停用的具体流水线）；实例创建时快照模板，可绑定接收器、设置并行上限（默认 10），
  支持停用/启用、模板同步与运行历史；每次入站数据触发 = 一次独立 run / 独立对话
- 🤖 **长驻会话自动研判**：编排侧 Supervisor 长驻调度——持续 claim 收件箱告警 → 独立 Worker 执行研判 →
  写回结果；at-least-once + `dedup_key` 幂等兜底，走到"结束对话"节点的 run 自动回收对话（保留摘要）
- 🔗 **接收器与模板联动**：接收器回调改为"先落收件箱"，Webhook 支持 `alert_id` / `event_id` 显式 ID 提升
  参与指纹幂等；内置 SOC 告警研判模板修复——告警内容 / 绑定 agent 运行期实时注入，不再静态化
- 🧩 **Agent 扩展元数据**：自定义 Agent 持久化保留图编排字段白名单（`type` / `category` / `tags` /
  `max_concurrency` 等），流水线实例复用既有 agent 管理链路
- ⚡ **前端编排页重构 + 健壮性**：PipelineEditor 迁移至"模板 → 实例"两级视图（数据接收器管理一并迁入）；
  兼容非安全上下文（`crypto.randomUUID` 降级）；模块列表随后端可达状态轮询刷新，服务重启 / 热装模块后自动恢复
- 🚀 **升级**：版本号统一为 2.0.0（后端 / 前端 / 脚本 / 文档）；编排模块随本版发布 v1.6.0

### v1.8.0

- 🧠 **经验系统重构为「记忆库」子系统**：新增 `src/baize/memory/`（存储 / 自动沉淀 / 轨迹 / 演进 / 知识图谱 / 检索），
  数据按 **经验 / 任务轨迹 / 事实 / 实体** 分层组织；Web「记忆」页重构为四视图：
  经验库、任务轨迹、混合检索、知识图谱
- 🔀 **任务轨迹（Episodes）**：会话过程自动沉淀为结构化轨迹，可逐段回看目标、执行过程与结果
- 🗂️ **经验全生命周期**：经验支持 新建 / 更新 / 作废 / 合并演进，记录置信度、证据与历史版本
  （`supersedes` / `replaced_by` / `history`），已作废或已演进的经验不再参与检索命中
- 🕸️ **事实 / 实体 / 知识图谱**：从会话中抽取关键事实与实体并建立关联，跨会话关系可视化，
  前端新增知识图谱视图
- 🔍 **混合检索**：语义向量 + 关键词统一检索入口，Agent 每回合自动沉淀与检索，越用越聪明
- 🔌 **API 更新**：经验 / 轨迹 / 检索 / 图谱统一收敛为 `/api/v1/memory/*` REST 接口；
  移除旧 `experience_signal` SSE 事件，前端同步简化
- 📦 **大附件与内存镜像支持**：单文件上传上限由 20MB 提升至默认 500MB（环境变量
  `BAIZE_MAX_UPLOAD_MB` 可调），新增常见内存 / 磁盘镜像扩展名（`.raw` / `.img` / `.dmp` /
  `.mem` / `.iso` 等）；上传失败时前端透出服务端具体原因而非笼统 400
- 🚀 **升级**：版本号统一为 1.8.0（后端 / 前端 / 脚本 / 文档）

### v1.7.2

- 🔁 **流式对话断连自动恢复**：识别 openai SDK 3.x 底层 `httpx2` 包的传输层异常（`RemoteProtocolError` 等，与顶层 `httpx` 异常类不互通导致重试失效），按名称动态收集两包异常；重试次数 2→4（共 5 次尝试，退避 1s→2s→4s→8s）；流中断前已产出部分内容时先发 `stream_reset` 标记清空半截缓冲再重试，前端同步清空思考区重新渲染，断流静默恢复、用户无感知
- 🛠️ **工具调用示例修正**：11 个 prompt 文件 91 处 `generic_linux_command` 示例统一为单 `command` 字符串，删除 schema 中不存在的 `interactive=` / `session_id=` / 双位置参数等误导形态；`_run_shell` 容忍 schema 外多余字段，避免 TypeError
- 🔗 **base_url 规范化**：自动去除误填的 `/chat/completions`、`/completions` 端点后缀，仅保留服务根地址，避免路径重复导致 404
- 📦 **内网离线部署**：`install-tools.sh` 支持 `--make-offline` 离线打包与 `--offline` 离线安装，
  一次打包（工具包 + 依赖树 + 静态二进制 + pip wheel）拷贝进内网即可装完，解决无外网环境
  无法安装工具的问题；同时修正 apt 系 `dnsutils` → `bind9-dnsutils` 包名
- 🚀 **升级**：版本号统一为 1.7.2（后端 / 前端 / 脚本 / 文档）

### v1.7.0

- 🖼️ **工具输出图片自动注入**：扫描本轮工具输出中引用的图片文件路径，自动提取并构造多模态用户消息注入 LLM 上下文（单张最大 5MB，data URL 约 1.37x 原始大小），避免工具截图 / 图片结果丢失
- 🔍 **多模态链路日志增强**：构建用户消息时记录 content_parts / 附件数量 / 文本长度，请求按 role / parts / 图片数输出概要日志，便于排查多模态链路问题
- 📄 **前端列表分页**：工具 / 智能体 / 经验 页面接入通用分页组件（Pagination），大数据量下列表分页加载
- ⛓️ **沙箱边界系统**：安全区 / 危险区工具划分，危险工具执行前需审批（`allow` / `approve` / `deny`
  三级权限），会话级审批令牌与状态持久化；新增 `/api/v1/sandbox/check` / `/approve` / `/deny` / `/stats` 端点
- 🗜️ **工具输出压缩器**（TokenJuice 风格）：大体积工具输出 LLM 语义摘要，token 压缩 80%+，
  保留关键信息避免简单截断；启动时自动注入各 Agent
- 🧩 **服务注册表**：全局单例服务（Sandbox / ExperienceStore）统一注册与查找，
  避免修改各 Agent 构造参数
- 🚀 **升级**：版本号统一为 1.7.0（后端 / 前端 / 脚本 / 文档）

### v1.6.0

- 🎮 **共享浏览器实时可交互面板**：Web「共享浏览器」面板从「截图查看」升级为**可实时操作**
  ——固定视口 1366×768，前端截图轮询 + 透明交互层，点击 / 滚轮 / 键盘输入 / 前进后退刷新
  全部坐标映射到浏览器视口，如同操作本地浏览器；支持一键最大化窗口
- 🔗 **共享浏览器与对话绑定**：会话级 `browser_collab` 开关（创建会话时可选、对话内可随时切换），
  仅当开关开启时 AI 才会注入 `shared_browser_*` 工具，避免无关会话乱调工具浪费 token；
  对话侧窗口实时展示浏览器画面与状态
- 🖱️ **共享浏览器交互 API**：新增 `/api/v1/shared-browser/click`、`/type`、`/key`、`/scroll`、
  `/nav` 五个端点，`status` 暴露视口尺寸供前端坐标映射
- 🛡️ **SSRF 护栏运行时配置**：`SSRFGuardrailSettings` 支持按会话启用 / 关闭 SSRF 防护，
  细粒度控制私网 / 回环 / 链路本地 / 保留 / 组播 / 未指定地址阻断与 CIDR / 域名白名单，
  即时生效并 JSON 持久化；前端「护栏」页新增 SSRF 防护配置面板
- 📁 **数据目录可配置**：`BAIZE_DATA_DIR` 环境变量统一重定向 sessions / 自定义工具 / 附件 /
  model.json / guardrails.json 等派生数据目录，`BAIZE_AUTH_DB` 单独指定认证库路径，
  适配沙箱等受限环境
- 🌐 **双浏览器语义强化**：静默浏览器 `browser_*` 与共享协作浏览器 `shared_browser_*`
  明确分离（无头独立实例 vs 有头持久化），后台清理任务防泄漏、不阻塞流水线
- 🧩 **Agent 提示词与护栏增强**：多智能体系统提示词对齐 v1.6 能力；护栏默认值随
  `BAIZE_FETCH_ALLOW_INTERNAL` 环境联动
- 🚀 **升级**：版本号统一为 1.6.0（后端 `__version__` / 前端 `package.json` / 脚本 / 文档）

### v1.5.0

- 🔒 **LLM 调用异常捕获与指数退避重试**：网络抖动 / 超时 / 限流(429) / HTTP 5xx 等临时故障自动重试（1s → 2s，最多 2 次）；404 / 401 / 400 等配置类错误不重试、直接暴露诊断，避免配置损坏时反复无效请求。覆盖非流式工具循环与 SSE 流式主路径（流式仅连接阶段可安全重试，中途断流不重放，防止重复内容）
- 🛡️ **工具执行超时保护**：单次工具调用超 5 分钟即中止并返回超时提示，网络卡住 / subprocess 阻塞等挂起工具不再拖死整轮对话
- 🛡️ **工具执行异常隔离**：单个工具抛异常不再中断整轮对话，转为错误消息返回给模型，由模型决定重试或改道
- 🛡️ **工具参数防御**：模型生成 `null` / 数组 / 字符串等非法参数形态不再抛 `TypeError`，调用链保持健壮
- 🐛 **非流式空回复兜底**：与流式路径对齐，模型空回复时最多强制续写一次，避免用户拿到空响应
- 🕸️ **Swarm 集群协作模式**：Selection / Orchestration Agent 注册 `pattern_type='swarm'`，
  前端新增「Swarm 协作」会话模式（红队集群协作、智能体间动态 Handoff），
  集群本质为后端注册的 swarm 型智能体，可编排多个智能体并行协作
- 🔗 **流水线模块接入（baize-orchestration）**：`_discover_and_load_modules` 扫描
  `baize.modules` entry point 自动加载已安装扩展模块（`pip install baize-orchestration` 即接入）；
  会话绑定流水线时自动走 orchestration runner（提交 run → SSE 事件转发为
  `pipeline_step` / `user_prompt` 审批 / 文本 delta / done），支持人工确认后 `resume_after_confirm`
- 🌐 **双浏览器体系**：
  - 🕹️ **共享协作浏览器 `shared_browser_*`**（新增）：有头持久化、人机共用的可视化浏览器，
    AI 打开登录页后可用 `shared_browser_wait_user` 阻塞等待人工扫码 / 验证码，登录态
    （Cookie / LocalStorage）跨会话持久化，人工登录一次 AI 全程复用；Web 端「共享浏览器」
    面板实时截图 + 人工确认放行
  - 🤖 **静默浏览器 `browser_*`**（增强）：明确无头静默语义、每次独立实例供流水线
    无人值守运行；任务中断 / 超时后浏览器进程后台可靠回收（`asyncio.shield` 保护），
    不泄漏进程、不阻塞流水线
- 🚀 **升级**：版本号统一为 1.5.0（后端 / 前端 / 脚本 / 文档）

### v1.4.0

- 🧰 **自定义工具系统**：`ToolBuilder` 智能体 + Web「工具」页在线创建/编辑自定义工具，
  启动热注册，无需改代码
- 🔌 **标准 Tool 协议 + 动态注册**：`ToolSpec` / `ToolRegistry` / `@register_tool`，
  参数 Schema 从类型注解自动推导；entry point 插件自动发现（`baize.tools` 组）
- 🛠️ **安全工具逐一封装（45 个）**：nmap / nuclei / nikto / sqlmap / gobuster /
  hydra / tshark / hashcat / metasploit 等核心 9 个，加信息收集（whois / dig /
  crt.sh / httpx / openssl / whatweb / wafw00f）、漏洞研究（searchsploit / NVD
  CVE）、爆破枚举（ffuf / arp-scan / masscan / traceroute）、无线（airodump /
  aircrack）、取证（exiftool / strings / binwalk / john / hashid）等 20 个扩展，
  全部含危险参数黑名单拦截
- 🌐 **浏览器自动化工具**：基于 Playwright 的 `browser_fetch` / `browser_screenshot` /
  `browser_click` / `browser_fill` / `browser_evaluate`，页面侦察 / 表单分析 /
  JS 提取，含 SSRF 防护与 fail-closed 依赖检查
- 🧠 **模型层抽象**：`BaseChatModel` / `OpenAICompatibleModel` / `ModelRouter`
  （primary + fallbacks 链式降级），旧 `LLMClient` 完全兼容
- 🧩 **Agent 定义扩展**：`state` 运行时状态、`memory` 记忆注入（`BaseMemory` /
  `InMemoryMemory`）、`hooks` 多处理器瀑布式事件链（`next()` 委托 + 短路拦截，
  对齐 deepseek-harness 的 tools/* 事件）
- 📋 **会话日志（单一事实源）**：`SessionLog` append-only 审计日志，模型历史
  可重建（`derive_messages`）、攻击链可重放（`replay`）、JSONL 落盘，满足
  合规审计与 DFIR 取证
- 🖥️ **安全执行环境抽象 + 沙箱**：`BaseExecutor`（local / docker / ssh）+
  `SandboxMode`（read_only / workspace_write / danger_full_access）fail-closed
  隔离，`EnforcementLevel` 诚实报告，错误双通道分类（sandbox_denied /
  runner_failure），环境变量 `BAIZE_EXEC_*` 无缝切换后端
- ✅ **正式测试套件**：pytest 42 个用例（注册表 / 沙箱 / 会话日志 / 瀑布链 /
  安全工具），`pip install -e .[test]` 后 `pytest` 一键运行
- 🐛 **修复断点**：Agent 别名解析、编排模板工具名校准、schema 类型推导、
  `Union` 导入、bytes 语法等
- 🐛 **Agent 稳定性修复**：30+ 智能体运行时稳健性优化（流式增量处理、工具调用链
  无损重建、空回复兜底续答等）
- 📚 **文档**：`docs/EXTENDING.md`（四大扩展点 + deepseek-harness 对照）、
  `docs/PLUGIN_MARKET.md` 与插件示例 `examples/security-tools-plugin/`

### v1.3.1

- 🐛 **修复 对话中断**：SSE 心跳保活（长时工具执行静默期不再被网络设备断开）；
  流式 chunk 内 content / reasoning / tool_calls 独立处理，工具调用增量不再丢失
- 🔁 **修复「继续」重跑**：会话历史中已执行的工具调用链（function_call /
  function_call_output）无损重建回模型上下文，中断后输入「继续」可基于已有进度
  续跑，不再从头重复执行
- 🐛 **修复 空回复中断**：模型在工具调用后返回空回复时，主动要求其继续完成任务
  而非静默结束
- 💾 **修复 经验库**：agent scope 规范化，消除缓存 key 不一致导致的
  「创建成功但库中不增加」
- 🔒 **安全修复**：附件读取路径穿越防护；文件监听线程/事件循环竞态修复
- 🚀 **升级**：版本号统一为 1.3.1

### v1.3.0

- ✨ **新增 经验系统**：会话经验提炼、自动向量化、语义检索命中（默认阈值 0.5）
- 🛡️ **新增 护栏 Guardrails**：文件化策略、注入防护、敏感信息保护、自定义规则热加载
- 📡 **新增 接收器**：Webhook / Syslog / 文件监听外部输入接入
- 🎨 **前端**：新增「经验」「护栏」页面；经验提炼入口固定在输入框上方
- 🐛 **修复**：检索 query 向量永久缓存导致的跨查询串扰；经验检索门槛过低导致的无关命中
- 🚀 **升级**：版本号统一为 1.3.0

### v1.2.0

- 新增会话管理（多会话 / 重命名 / 删除）
- 前端界面优化（暗色主题、响应式布局）

### v1.1.0

- 工具调用（Function Calling）能力
- 多模型配置与热切换
- 智能体体系扩展

### v1.0.0

- 多智能体基础框架
- SSE 流式对话
- 本地化部署

---

<div align="center">

**白泽·智脑 (Baize)** · 仅供安全研究与授权测试使用

[![Version](https://img.shields.io/badge/version-v2.0.0-4C9F38?style=flat-square)](https://github.com/DarkSword404/baize-core)

</div>

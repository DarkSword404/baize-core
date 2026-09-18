<div align="center">

<img src="docs/img/favicon.png" width="92" alt="白泽·智脑 logo" />

# 🦄 白泽·智脑 (Baize)

**AI 驱动的多智能体安全操作平台**

> 证据黑板编排 · 树状任务调度 · 会话级容器沙箱 · 内置 30+ 专业安全智能体 · 本地化部署 · LLM 无关

[![Version](https://img.shields.io/badge/version-v4.0.0-4C9F38?style=flat-square&logo=github)](https://github.com/DarkSword404/baize-dev/releases/tag/v4.0.0)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Research_Only-8B5CF6?style=flat-square)](LICENSE)

**白泽·智脑** 是一个基于大型语言模型的本地化 AI 安全助手，面向 **Web 渗透、移动安全、无线/射频、红蓝对抗、应急响应与合规审计** 等场景，提供 30+ 个开箱即用的专用智能体。v4.0.0 引入 **证据黑板（Blackboard）+ 树状任务调度 + CVSS 式威胁评分 + 会话级容器沙箱 + 报告系统**，让自动化渗透过程可解释、可收敛、边界可控。

</div>

---

## ✨ 核心特性

| | 特性 | 说明 |
|---|---|---|
| 🧠 | **多智能体协同** | 30+ 专业安全智能体，按需调度、协同作战；Swarm 集群协作模式支持智能体间动态 Handoff |
| 📋 | **证据黑板编排（v3.0+）** | 渗透过程物化为 origin/intent/fact 证据图：每个意图可追溯父证据，产出与状态强联动，攻击地图实时可视化 |
| 🌳 | **树状任务调度（v4.0）** | intent 携带 `phase`（recon/vuln/exploit/post）与 `depends_on`；同阶段原子任务真并行、跨阶段屏障汇聚，不再为凑数量乱并行 |
| 🎯 | **威胁评分与按分调度（v4.0）** | fact 写入即确定性评分（severity × confidence，CVSS 式简化 0-100 分）；高威胁证据链派生任务插队优先 |
| 🛑 | **预算与空转控制（v4.0）** | 预算按**实际工具调用数**计数（非 LLM turn）；ProgressGuard 连续零增量强制收束；失败区分 `budget_exhausted`/`disproven`/`error` |
| 🎯 | **TaskContext 单一上下文（v4.0）** | task_type / 目标 / 成功模式 / 预算从 plan_properties 单一构造，brief/prompt/guard/agent 全部读它；pentest 任务不再被引导找 CTF flag |
| 📦 | **会话级容器沙箱（v4.0）** | 任务按需绑定容器，agent 命令经 `docker exec` 进入隔离环境执行：`--cap-drop=ALL`、只读根、no-new-privileges，绝不 privileged |
| 🚧 | **授权范围护栏（v4.0）** | scope_guard 从任务描述提取授权目标，越界主机/工具自动拦截，网络边界在沙箱内也生效 |
| 📊 | **报告系统（v4.0）** | 证据黑板一键生成结构化渗透报告（模板化 Markdown），含发现分级、证据链与失败方向分类 |
| 🗄️ | **任务归档（v4.0）** | 完结会话可归档（从活跃列表移除、数据保留），支持恢复继续 |
| 🔌 | **LLM 无关** | 兼容 OpenAI / DeepSeek / 通义千问 / Ollama 等 OpenAI 协议端点，模型热切换 + 多模型路由 fallback |
| 🛠️ | **工具调用** | 内置 50+ 工具（30+ 安全专用 + 静默浏览器 + 8 个共享协作浏览器），智能体自主调用 |
| ⛓️ | **沙箱边界** | 安全区 / 危险区工具划分，危险工具执行前审批（`allow` / `approve` / `deny` 三级权限），会话级审批令牌 |
| 🗜️ | **工具输出压缩** | 大体积工具输出 LLM 语义摘要，token 压缩 80%+；截图/图片自动构造多模态消息注入 |
| 🌐 | **双浏览器体系** | 有头共享协作浏览器（人机共用、登录态持久化）+ 无头静默浏览器（流水线无人值守） |
| 🛡️ | **护栏 Guardrails** | 文件化可配置的输入/输出策略，注入防护、敏感信息保护与 SSRF 运行时配置 |
| 💾 | **记忆库** | 经验 / 任务轨迹 / 事实 / 实体分层沉淀，知识图谱关联，混合检索自动命中 |
| 📨 | **告警研判收件箱** | Webhook / Syslog / 文件监听入站告警落 SQLite 持久化收件箱（指纹幂等 / 租约消费 / 死信） |
| ⚡ | **实时交互** | SSE 流式输出、会话持久化、暗色 React UI、攻击地图 Cytoscape 可视化 |
| 🔒 | **本地化部署** | 全部数据保存在本地（默认 `.baize-data/`），隐私安全可控 |

---

## 🏗️ 技术栈

| 层 | 技术 |
|---|---|
| **后端** | Python 3.10+ · FastAPI · Uvicorn · Pydantic v2 |
| **前端** | React 18 · TypeScript · Vite · Tailwind CSS · Cytoscape（攻击地图） |
| **编排** | 证据黑板 Blackboard · Reason LLM 规划 · 树状 phase/depends_on 调度 |
| **隔离** | Docker / Podman 会话级容器（cap-drop ALL + 只读根）· 本地执行器自动回退 |
| **浏览器** | Playwright（静默浏览器 + 有头共享协作浏览器） |
| **存储** | 本地目录 JSON 黑板 / 审计 JSONL · SQLite 告警收件箱 |

---

## 🚀 快速开始

### 环境要求

- **Python** 3.10+
- **Node.js** 18+ / npm 9+
- 可用的 **OpenAI 协议 LLM 端点**（OpenAI / DeepSeek / 通义千问 / Ollama 等）
- （可选，渗透沙箱）**Docker** 或 **Podman**，当前用户具备容器访问权限

### 安装

```bash
git clone https://github.com/DarkSword404/baize-dev.git
cd baize-dev

./setup.sh                 # 创建 .venv + 安装后端 + 安装前端依赖
./setup.sh --with-tools    # 推荐：一并预装 nmap/sqlmap/nuclei 等系统工具
./setup.sh --with-sandbox  # 可选：构建会话级沙箱镜像 baize-sandbox:base
```

`setup.sh` 参数可组合：`./setup.sh --with-tools --with-sandbox`。

> **最小化系统（如 Ubuntu Server）提示**：白泽的渗透/信息收集工具依赖大量系统二进制
> （nmap / sqlmap / nuclei / hydra 等），请执行 `./setup.sh --with-tools` 或单独运行
> `./install-tools.sh --yes` 一键预装，否则相关工具被调用时会报 `command not found`。

#### Docker 权限（容器沙箱）

沙箱镜像构建与运行需要访问容器 daemon。若不想用 root：

```bash
sudo usermod -aG docker $USER   # 加入 docker 组后重新登录
```

- 已在 docker 组但**当前 shell 未生效**时，`start.sh` 会自动经 `sg docker -c` 包装启动后端
- 没有 Docker 也可正常使用：任务默认走**本地执行器**，仅"绑定容器"功能不可用
- 也可使用 rootless **Podman**（`BAIZE_SANDBOX_RUNTIME=podman`）

### 配置模型

模型配置保存在数据目录的 `model.json`（Web「设置」页自动生成与管理；默认路径
`.baize-data/model.json`，可由 `BAIZE_DATA_DIR` 重定向）：

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
./start.sh              # 启动后端 (8001) + 前端 (5173)，启动后自动健康检查
./start.sh --reload     # 开发模式：uvicorn --reload，改代码自动重启
./stop.sh               # 优雅停止（SIGTERM，8s 超时升级 KILL，整进程组回收）
./stop.sh --hard        # 强制停止（SIGKILL）
```

- **Web 界面**：<http://localhost:5173>
- **健康检查**：<http://localhost:8001/api/v1/health>
- 日志：`logs/backend.log`、`logs/frontend.log`；PID：`logs/{backend,frontend}.pid`
- 首次启动自动生成管理员登录凭证并打印在终端（凭证文件已存在则跳过）

### 工具依赖预装（install-tools.sh）

```bash
./install-tools.sh                 # 全量安装（core/recon/web/password/forensic/wireless + 浏览器）
./install-tools.sh --yes           # 免交互（CI / 无人值守）
./install-tools.sh --skip-browser  # 跳过浏览器依赖（无 GUI 服务器）
./install-tools.sh --make-offline baize-tools-offline.tar.gz  # 联网机打离线包
./install-tools.sh --offline baize-tools-offline.tar.gz       # 内网机离线安装
```

自动检测 apt / dnf / yum / pacman；幂等，已装工具自动跳过，单个失败不中断整体。

### 环境自检

```bash
.venv/bin/baize doctor
# 系统工具 / 运行时 / LLM 模型配置 / 记忆向量化 / 浏览器依赖 / 容器运行时
```

---

## 🧭 证据黑板与树状调度（v4.0 核心机制）

渗透任务不再是"一条对话走到黑"，而是一张持续生长的**证据图**：

```
origin（起点）
  ├─[recon] 端口扫描 ──→ fact: 80/8080 开放 [30分]
  ├─[recon] 目录枚举 ──→ fact: /api 为 Spring 网关 [30分]
  └─[recon] 指纹识别 ──→ fact: fastjson 1.2.68 [30分]
                          │
                          ├─[vuln] 验证 JNDI 注入（depends_on: 指纹识别）
                          │        └─→ fact: 预认证 JNDI 注入确认 [85分]
                          │                 │
                          │                 └─[exploit] LDAP 外带利用 ──→ fact: RCE [98分]
                          └─[vuln] 验证弱口令 ──→ fact: safeMode 开启（排除）[10分]
goal（目标）
```

- **节点类型**：`origin`（起点）/ `intent`（探索意图）/ `fact`（证据结论）/ `hint`（人工提示）/ `goal`
- **阶段（phase）**：`recon → vuln → exploit → post → general`，Reason 规划时输出
- **依赖（depends_on）**：跨阶段意图必须等前置完成才会被调度（阶段屏障 + 汇聚再规划）
- **评分（score）**：severity（critical/normal/info/excluded）× confidence（confirmed 1.0 / tentative 0.6）；
  评分只采信 **label 断言**，detail 里的推测文字不能翻案；含"疑似/需复核"自动降置信
- **去重裁决**：所有 intent 生产者（用户提示 / Reason / 编排兜底）统一走 `propose_intent`，
  同 label 同父证据的活跃意图自动去重；"继续/ok"等纯续行词不再物化为垃圾意图
- **收敛控制**：预算耗尽（`budget_exhausted`）与方向证伪（`disproven`）语义分离，
  Reason 对前者可换路继续；连续两波零增量自动收束（`agent_spinning`）

**攻击地图（AttackMap）**：右侧抽屉实时渲染证据树——fact 按威胁分着色（高危红/发现蓝/侦察青/排除灰）、
intent 按阶段着色并标注分数，支持点击节点查看详情、注入人工 Hint 引导方向。

---

## 📦 会话级容器沙箱（v4.0）

容器与任务**解耦**：默认本地执行，复杂渗透任务在 Web「容器」页或会话中主动**绑定容器**。

- 每个绑定任务一个持久容器，agent 的 shell/工具命令经 `exec` 进入执行，工作区挂载持久化
- 运行时安全参数由平台自动注入：

```
--cap-drop=ALL --security-opt=no-new-privileges --read-only
--tmpfs /tmp --network=host -v <workspace>:/workspace -w /workspace
```

绝不使用 `--privileged`，绝不挂载 `docker.sock`；网络可达性由 scope_guard 按授权范围约束。
容器并发上限 10，超出返回 503。

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `BAIZE_SANDBOX_RUNTIME` | 自动探测（docker 优先，其次 podman） | 显式指定容器运行时 |
| `BAIZE_SANDBOX_IMAGE` | `baize-sandbox:base` | 沙箱基础镜像名 |
| `BAIZE_SANDBOX_MAX_CONTAINERS` | `10` | 任务级容器并发上限 |
| `BAIZE_SANDBOX_PIDS_LIMIT` | `1024` | 单容器 PID 上限 |
| `BAIZE_SANDBOX_MEMORY` | `2g` | 单容器内存上限 |

镜像构建见 [docker/baize-sandbox/README.md](docker/baize-sandbox/README.md)，
Dockerfile 预装 nmap / nuclei / sqlmap / gobuster / ffuf / hydra / whatweb 等，容器内 root 可随时补装。

---

## 📊 报告与任务归档（v4.0）

- **报告**：会话任意时刻可基于证据黑板生成报告——按 severity 分级的发现列表、证据链、
  失败方向（预算耗尽/证伪/异常三分类）与修复建议；Web「报告」页浏览/下载 Markdown
- **归档**：完结会话一键归档，从活跃会话列表移除但完整保留黑板/审计/附件，可随时恢复

---

## 🖥️ Web 界面

| 页面 | 功能 |
|---|---|
| 🖥️ **控制台** | 平台概览、会话 / 智能体 / 工具状态统计 |
| 💬 **对话渗透** | 多智能体对话（SSE 流式）、证据攻击地图（phase/score 着色）、共享浏览器协作侧窗 |
| 📦 **容器**（v4.0） | 沙箱容器生命周期：绑定/解绑、启动/停止、并发与资源状态 |
| 📊 **报告**（v4.0） | 证据报告生成、浏览与 Markdown 下载 |
| 🗄️ **任务归档**（v4.0） | 已归档会话浏览与恢复 |
| 🔗 **流水线** | 编排流水线编辑与运行（需接入 `baize-orchestration` 模块） |
| 🤖 **智能体** | 30+ 智能体浏览与切换、自定义智能体创建、Swarm 协作模式 |
| 🛠️ **工具管理** | 内置工具查看、自定义工具在线创建 / 编辑 / 测试 / 启停 |
| 📋 **会话管理** | 多会话浏览、重命名、删除、归档、历史检索 |
| 🛡️ **安全护栏** | 注入 / 敏感信息 / 输出合规策略、SSRF 运行时配置、沙箱权限面板 |
| 💾 **记忆库** | 经验 / 任务轨迹 / 混合检索 / 知识图谱四视图 |
| ⚙️ **设置** | 模型配置（多提供商）、管理员初始化、数据目录 |

---

## 🤖 智能体体系

内置 **30+** 安全智能体，覆盖 Web 渗透、移动安全、无线/射频、红队对抗、蓝队应急、
CTF、合规报告与协同支撑（分流 / 任务选择 / 推理支撑 / 思维路由 / 经验沉淀 / 方案对抗）。
完整列表见 Web「智能体」页。v4.0 起任务类型（pentest/ctf/forensics/general）由
TaskContext 统一裁决执行规则——例如渗透任务不会被引导搜寻 CTF flag。

---

## 🔌 API 一览（v4.0 新增以 🆕 标注）

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/v1/health` | GET | 健康检查（含版本号） |
| `/api/v1/sessions` | GET/POST/DELETE | 会话管理 |
| `/api/v1/sessions/{id}/messages/stream` | POST | SSE 流式对话 |
| 🆕 `/api/v1/sessions/{id}/blackboard` | GET | 证据黑板快照（节点/边/版本） |
| 🆕 `/api/v1/sessions/{id}/blackboard/hints` | POST | 注入人工 Hint |
| 🆕 `/api/v1/sessions/{id}/bind-container` | POST | 会话绑定沙箱容器 |
| 🆕 `/api/v1/sessions/{id}/unbind-container` | POST | 解绑容器 |
| 🆕 `/api/v1/sessions/{id}/archive` | POST | 归档会话 |
| 🆕 `/api/v1/containers` | GET | 容器列表 |
| 🆕 `/api/v1/containers/stats` | GET | 容器并发/资源统计 |
| 🆕 `/api/v1/containers/{name}/start` `/unbind` | POST | 容器启动 / 解绑 |
| 🆕 `/api/v1/reports` | GET/POST | 报告列表 / 生成报告 |
| 🆕 `/api/v1/reports/{id}/download` | GET | 下载报告 Markdown |
| 🆕 `/api/v1/reports/templates` | GET/POST | 报告模板管理 |
| 🆕 `/api/v1/archives` `/archives/{id}` `/restore` | GET/POST | 归档列表 / 恢复 |
| `/api/v1/sandbox/check` `/approve` `/deny` | POST | 危险工具三级审批流 |
| `/api/v1/guardrails` `/guardrails/sandbox` | GET/PUT | 护栏与沙箱权限策略 |
| `/api/v1/agents` `/agents/custom` | GET/POST | 内置/自定义智能体 |
| `/api/v1/tools` `/tools/custom` | GET/POST | 工具系统 |
| `/api/v1/model-config` | GET/PUT | 多模型配置与 fallback |
| `/api/v1/memory/*` | GET/POST | 经验 / 轨迹 / 图谱 |
| `/api/v1/shared-browser/*` | GET/POST/WS | 共享协作浏览器 |
| `/api/v1/hook` | POST | 接收器 Webhook 入口 |

---

## 🏛️ 架构

```
┌───────────────────────────────────────────────────────────┐
│                      Web 前端 (React)                      │
│  对话 · 攻击地图(phase/score) · 容器 · 报告 · 归档 · 护栏   │
└──────────────────────────┬────────────────────────────────┘
                           │ SSE / REST / WebSocket
┌──────────────────────────▼────────────────────────────────┐
│  编排层 conversation_orchestrator                           │
│  Reason LLM 规划 → propose_intent 裁决 → rank/select_wave   │
│  证据黑板 Blackboard（intent/fact/phase/depends_on/score）  │
│  TaskContext（task_type/预算/成功模式）· ProgressGuard      │
└───────────────┬───────────────────────────┬───────────────┘
                │                           │
┌───────────────▼──────────────┐ ┌──────────▼────────────────┐
│ Agent SDK（CodeAct 工具循环） │ │ 执行器 executors          │
│ 50+ 工具 · 压缩器 · 多模态    │ │ local ▸ docker/podman 容器 │
└──────────────────────────────┘ │ scope_guard 授权范围拦截   │
                                 └───────────────────────────┘
```

---

## 📁 目录结构

```
baize-dev/
├── src/baize/
│   ├── pentest/
│   │   ├── conversation_orchestrator.py  # 编排器：Reason 规划/波次调度/收束判定
│   │   ├── blackboard.py                 # 证据黑板（节点/边/去重/排序/评分回填）
│   │   ├── scoring.py                    # 🆕 severity/score/confidence 确定性评分
│   │   ├── task_context.py               # 🆕 任务单一上下文
│   │   ├── dynamic_agent.py              # Agent 工厂（预算/guard 注入）
│   │   ├── agent_tools.py                # 黑板读写工具 + ProgressGuard
│   │   ├── container_runtime.py          # 🆕 docker/podman 生命周期
│   │   ├── container_guard.py / container_registry.py
│   │   ├── scope_guard.py                # 🆕 授权范围提取与拦截
│   │   └── workspace.py                  # 🆕 容器工作区
│   ├── reports/                          # 🆕 报告生成（api/store/templates/tools）
│   ├── api/                              # FastAPI 路由（app/sessions/attachments/archives）
│   ├── sdk/                              # Agent SDK / 模型抽象 / 会话审计日志
│   ├── tools/                            # 安全工具集 / 浏览器 / 自定义工具
│   ├── receivers/                        # Webhook / Syslog / 文件监听 + 告警收件箱
│   └── memory/                           # 记忆库（经验/轨迹/图谱/混合检索）
├── docker/baize-sandbox/                 # 🆕 沙箱基础镜像 Dockerfile
├── web/                                  # React 前端（AttackMap/Containers/Reports/...）
├── docs/                                 # 扩展开发文档
├── examples/                             # 插件示例
├── setup.sh                              # 环境安装（--with-tools / --with-sandbox）
├── install-tools.sh                      # 系统安全工具预装（含离线打包）
├── start.sh / stop.sh                    # 启停脚本（--reload / --hard）
└── update.sh                             # GitHub 更新
```

扩展开发（工具协议 / Agent 扩展 / 执行器 / 插件市场）见 [docs/EXTENDING.md](docs/EXTENDING.md)
与 [docs/PLUGIN_MARKET.md](docs/PLUGIN_MARKET.md)。

### 常用环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `BAIZE_DATA_DIR` | `~/.baize`（start.sh 默认改到项目内 `.baize-data/`） | 全部本地数据根目录 |
| `BACKEND_PORT` / `FRONTEND_PORT` | `8001` / `5173` | 服务端口 |
| `BAIZE_RELOAD` | `0` | 设为 1 等效 `./start.sh --reload` |
| `BAIZE_SANDBOX_RUNTIME` | 自动 | `docker` 或 `podman` |
| `BAIZE_SANDBOX_IMAGE` | `baize-sandbox:base` | 沙箱镜像 |
| `BAIZE_SANDBOX_MAX_CONTAINERS` | `10` | 容器并发上限 |
| `BAIZE_API_REQUIRE_AUTH` | `1` | 是否强制登录认证 |

---

## 🗺️ Roadmap

- [x] **v1.x** 多智能体框架 → 工具系统 → 记忆/护栏/接收器 → Swarm → 双浏览器 → 沙箱边界 → 记忆库
- [x] **v2.0** 编排流水线两级模型 + 告警收件箱长驻自动研判
- [x] **v3.0** 证据黑板（Blackboard）+ 并行分支调度 + 攻击图可视化
- [x] **v4.0** 全链路根因修复：产出-状态联动 / 预算空转控制 / TaskContext / intent 裁决入口；
  树状任务模型（phase/depends_on）+ CVSS 式评分按分调度；会话级容器沙箱 + scope_guard；
  报告系统 + 任务归档

---

## ❓ 常见问题

<details>
<summary><b>Q1：启动后访问 5173 页面打不开？</b></summary>

确认 setup.sh 已完成前端依赖安装（web/node_modules 存在）；vite 开发服务器首次编译需数秒，
查看 logs/frontend.log 排查。
</details>

<details>
<summary><b>Q2：对话无回复 / 报模型错误？</b></summary>

检查数据目录 model.json 的 base_url / api_key / model，并在「设置」页重新选择。
Web 界面会直接展示 HTTP 状态码与排查建议，完整 traceback 在 logs/backend.log。
</details>

<details>
<summary><b>Q3：绑定容器时报 docker 权限错误？</b></summary>

执行 `sudo usermod -aG docker $USER` 后重新登录；未重新登录时 start.sh 会自动用
`sg docker -c` 包装后端。验证：`docker info`。无 Docker 环境可忽略，任务默认本地执行。
</details>

<details>
<summary><b>Q4：攻击地图里节点全是蓝色没有红/青区分？</b></summary>

v4.0 起 fact 写入时才评分。历史会话在服务启动时自动幂等回填评分；若仍无颜色，
确认该会话已在 v4.0 服务下重新打开过（回填发生在编排器加载黑板时）。
</details>

<details>
<summary><b>Q5：stop.sh 提示"拒绝停止以防误杀"？</b></summary>

PID 文件中的进程命令行不含本项目标识（可能是 PID 复用的残留文件）。确认端口上没有
白泽服务后删除 logs/backend.pid（或 frontend.pid）即可；stop.sh 另有端口兜底，
且只杀命令行匹配的本项目进程。
</details>

---

## ⚠️ 安全声明

> 白泽·智脑定位为 **安全研究、授权测试与教育培训** 工具。请确保：
>
> - 所有测试目标均已获得 **明确书面授权**
> - 容器沙箱与 scope_guard 是纵深防御措施，不能替代授权流程本身
> - 遵守当地法律法规与目标组织的安全策略
> - 开发者与贡献者不对任何非法使用承担连带责任
>
> **本项目仅限授权环境使用。**

---

## 📝 更新日志

### v4.0.0（当前）

- 📋 **产出-状态联动（P0 止血）**：修复 `add_fact` 只建派生边不回写 properties 导致编排器
  把有产出的意图全部误标 failed；产出查询双证据源兼容历史数据
- 🛑 **fail 语义三分类**：`fail_intent` 区分 `budget_exhausted`（预算耗尽，可换路）/
  `disproven`（方向证伪）/ `error`（执行异常），Reason、攻击地图、报告全链路展示
- 💰 **真实工具调用预算 + ProgressGuard**：预算按实际工具调用计数（原仅按 LLM turn，
  40 turn 实际可发 140+ 次调用）；只读工具不计预算，连续 12 次零增量强制收束，
  连续两波空转波次直接收束（agent_spinning）
- 🧭 **TaskContext 单一上下文（P1）**：task_type / 目标 / 成功模式 / 预算从 goal 节点
  plan_properties 单一构造，brief/prompt/guard/agent 统一读取；删除渗透任务硬编码
  "拿到 flag"指令，pentest/ctf/forensics 执行规则按类型动态生成
- ⚖️ **intent 单一裁决入口（P1）**：用户提示 / Reason / 编排兜底三个生产者统一走
  `propose_intent`（同 label+同父证据活跃去重，幻觉 fact id 丢弃）；"继续/ok"等
  纯续行词不再创建垃圾意图
- 🌳 **树状任务模型（P2）**：intent 新增 `phase`（recon/vuln/exploit/post/general）与
  `depends_on` 跨阶段依赖；`rank_pending_intents` 按父 fact 威胁分排序、被屏障意图压队尾；
  `_select_wave` 同阶段无依赖任务并行、跨阶段汇聚后再规划；Reason schema 与决策准则同步
- 🎯 **确定性威胁评分（P2）**：新增 scoring.py，severity × confidence → 0-100 分；
  label 断言优先（detail 推测不翻案）、"疑似/需复核"强制降置信、已证实 RCE 98 /
  凭据绕过 85 / PII 泄露 78 / 侦察 30 / 排除 10；历史 fact 启动时幂等回填
- 📦 **会话级容器沙箱**：docker/baize-sandbox 基础镜像 + container_runtime/guard/registry，
  任务按需绑定容器（--cap-drop=ALL / 只读根 / no-new-privileges，绝不 privileged），
  支持 podman rootless
- 🚧 **scope_guard**：任务描述提取授权目标，工具层越界拦截
- 📊 **报告系统 + 任务归档**：证据黑板生成结构化报告（发现分级/证据链/失败分类），
  会话归档与恢复；Web 新增 Reports / TaskArchives / Containers 页面
- 🗺️ **攻击地图升级**：fact 按 severity、intent 按 phase 着色，节点标注分数，
  图例新增阶段/威胁色卡组
- 🔧 **安装与运维**：setup.sh 自动安装前端依赖、新增 `--with-sandbox`；start.sh 支持
  `--reload`、setsid 进程组启动、docker 组自动 `sg` 包装、启动健康检查与沙箱镜像就绪提示；
  stop.sh 进程归属校验 + 进程组回收（覆盖 reloader 子进程）+ 8s 优雅退出；
  update.sh 修正远程仓库地址
- 🚀 **升级**：版本号统一为 4.0.0（pyproject.toml / package.json / __init__.py / cli.py / 脚本 / 文档）

### v3.0.0

- 📋 **证据黑板 Blackboard**：origin/intent/fact/hint/goal 图模型取代扁平任务列表，
  Fact→Fact 派生链可追溯，append-only 版本化快照
- 🌊 **并行分支调度**：同一波次多个独立意图并行执行，波末统一汇聚再规划
- 🗺️ **攻击图可视化**：Cytoscape + ELK 分层布局，BFS 深度分层、派生/前置边语义、
  节点详情与人工 Hint 注入

### v2.0.x

- 📨 告警持久化收件箱（SQLite，指纹幂等 / 租约消费 / 退避重试 / 死信）
- 🔄 编排流水线两级模型（模板 → 实例）+ 长驻会话自动研判
- 🧩 baize 命名空间子包合并（pkgutil.extend_path），扩展模块无需手工合并目录

### v1.x 历史版本

v1.8 记忆库（经验/轨迹/知识图谱/混合检索）· v1.7 沙箱边界+输出压缩+多模态注入 ·
v1.6 共享浏览器可交互面板+SSRF 配置 · v1.5 重试退避+Swarm+流水线接入+双浏览器 ·
v1.4 自定义工具+Agent 扩展+会话审计日志+执行环境抽象 · v1.3 经验系统+护栏+接收器 ·
v1.2 会话管理 · v1.1 工具调用+多模型热切换 · v1.0 多智能体框架

---

<div align="center">

**白泽·智脑 (Baize) v4.0.0** · 仅供安全研究与授权测试使用

[![Version](https://img.shields.io/badge/version-v4.0.0-4C9F38?style=flat-square)](https://github.com/DarkSword404/baize-dev)

</div>

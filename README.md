<div align="center">

<img src="docs/img/favicon.png" width="92" alt="白泽·智脑 logo" />

# 🦄 白泽·智脑 (Baize)

**AI 驱动的多智能体安全操作平台**

> 内置 30+ 专业安全智能体 · 本地化部署 · LLM 无关

[![Version](https://img.shields.io/badge/version-v1.5.0-4C9F38?style=flat-square&logo=github)](https://github.com/DarkSword404/baize-core)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Research_Only-8B5CF6?style=flat-square)](LICENSE)

**白泽·智脑** 是一个基于大型语言模型的本地化 AI 安全助手，面向 **Web 渗透、移动安全、无线/射频、红蓝对抗、应急响应与合规审计** 等场景，提供 30+ 个开箱即用的专用智能体，支持长期记忆、安全护栏与外部事件接入。

</div>

---

## ✨ 核心特性

| | 特性 | 说明 |
|---|---|---|
| 🧠 | **多智能体协同** | 30+ 专业安全智能体，按需调度、协同作战 |
| 🔌 | **LLM 无关** | 兼容 OpenAI / DeepSeek / 通义千问 / Ollama 等 OpenAI 协议端点，模型热切换 |
| 🛠️ | **工具调用** | 内置 45+ 工具（29 个安全专用 + 5 个浏览器自动化），智能体自主调用 |
| 🔌 | **标准 Tool 协议（v1.4.0）** | `ToolSpec` + `@register_tool` 动态注册，entry point 插件自动发现，无需改源码 |
| 🧠 | **模型层抽象（v1.4.0）** | `BaseChatModel` / `ModelRouter` 多模型路由 + 失败 fallback，任意 OpenAI 兼容端点 |
| 🧩 | **Agent 扩展（v1.4.0）** | `state` 运行时状态、`memory` 记忆注入、`hooks` 瀑布式事件链 |
| 🖥️ | **执行环境抽象（v1.4.0）** | 统一执行器接口 + 沙箱隔离，工具可无缝切换 local / docker / ssh 后端 |
| 📋 | **会话日志（v1.4.0）** | append-only 审计日志，模型历史可重建、攻击链可重放（DFIR 取证） |
| 🧰 | **自定义工具（v1.4.0）** | Web「工具」页在线创建/编辑自定义工具，启动热注册，无需改代码 |
| 💾 | **长期记忆（v1.3.0）** | 会话经验自动向量化入库，新问题自动检索命中，越用越聪明 |
| 🛡️ | **护栏 Guardrails（v1.3.0）** | 文件化可配置的输入/输出策略，注入防护与敏感信息保护 |
| 📡 | **外部接收器（v1.3.0）** | Webhook / Syslog / 文件监听，打通外部事件源 |
| ⚡ | **实时交互** | SSE 流式输出、会话持久化、美观的暗色 UI |
| 🔒 | **本地化部署** | 全部数据保存在本地，隐私安全可控 |

---

## 🏗️ 技术栈

| 层 | 技术 |
|---|---|
| **后端** | Python 3.11+ · FastAPI · Uvicorn · Pydantic v2 |
| **前端** | React 18 · TypeScript · Vite · Tailwind CSS |
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
cd baize-core-v1.5.0
./setup.sh    # 创建虚拟环境 + 安装 baize-core + 构建前端
```

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
| 🔗 **协同支撑** | 分流 · 任务选择 · 推理支撑 · 思维路由 · 经验沉淀 |

所有智能体共享工具调用能力，可通过「智能体」页查看与切换。

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
- Web 端「经验」页可浏览、编辑、删除历史经验

```bash
GET    /api/v1/experiences          # 经验列表
POST   /api/v1/experiences          # 创建经验（自动生成向量）
GET    /api/v1/experiences/{id}     # 经验详情
DELETE /api/v1/experiences/{id}     # 删除经验
```

---

## 🛡️ 护栏 Guardrails（v1.3.0）

以**文件化策略**对模型输入/输出进行前置校验：

- **Prompt 注入防护** — 识别并拦截指令注入尝试
- **敏感信息保护** — 检测身份证号、手机号、密钥等敏感信息
- **输出合规校验** — 拒绝协助非法操作（如未授权渗透、恶意软件编写）
- **自定义策略** — 以 YAML 文件定义关键词规则，热加载生效

Web 端「护栏」页可查看、开关各项策略；策略文件位于 `prompts/`。

---

## 📡 外部接收器（v1.3.0）

打通外部事件源，自动触发智能体响应：

- **Webhook** — 外部系统通过 `POST /api/v1/hook` 推送事件
- **Syslog** — 接收网络设备/服务器日志
- **文件监听** — 监控指定目录新文件自动处理

---

## 🔌 API 一览

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/v1/health` | GET | 健康检查（含版本号） |
| `/api/v1/auth/login` | POST | 管理员登录 |
| `/api/v1/agents` | GET | 智能体列表 |
| `/api/v1/models` | GET | 模型列表 |
| `/api/v1/sessions` | GET/POST/DELETE | 会话管理 |
| `/api/v1/chat/stream` | POST | SSE 流式对话 |
| `/api/v1/experiences` | GET/POST/DELETE | 经验系统（v1.3.0） |
| `/api/v1/guardrails` | GET/PUT | 护栏策略（v1.3.0） |
| `/api/v1/hook` | POST | 接收器 Webhook 入口 |

---

## 🏛️ 架构

```
┌─────────────────────────────────────────────────────┐
│                      Web 前端 (React)               │
│        聊天 / 智能体 / 经验 / 护栏 / 设置            │
└────────────────────────┬────────────────────────────┘
                         │ SSE / REST
┌────────────────────────▼────────────────────────────┐
│                 baize-core（核心模块）                │
│  ┌──────────┐ ┌───────────┐ ┌────────────────────┐  │
│  │ 会话管理  │ │ 智能体调度 │ │ 经验系统(向量检索)   │  │
│  └──────────┘ └───────────┘ └────────────────────┘  │
│  ┌──────────┐ ┌───────────┐ ┌────────────────────┐  │
│  │ 工具调用  │ │ 护栏策略   │ │ 接收器(webhook/…)   │  │
│  └──────────┘ └───────────┘ └────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

---

## 📁 目录结构

```
baize-core/
├── src/baize/
│   ├── agents/           # 30+ 安全智能体 + 护栏策略实现
│   ├── api/              # FastAPI 路由
│   ├── experiences/      # 经验系统（embedding / retriever / store / refine）
│   ├── receivers/        # 外部接收器（Webhook / Syslog / 文件监听）
│   ├── sdk/              # SDK 与协议封装（models.py / memory.py / agent.py / session_log.py）
│   ├── tools/            # 安全工具集（registry / security_tools / security_tools_extra / browser_tools）
│   ├── executors.py      # 执行环境抽象（local / docker / ssh + 沙箱 fail-closed）
│   └── util/             # 通用工具
├── web/                  # React 前端
├── prompts/              # 提示词与护栏策略文件
├── docs/                 # 文档（EXTENDING.md / PLUGIN_MARKET.md）
├── examples/             # 插件示例（security-tools-plugin）
├── setup.sh              # 环境安装
├── start.sh / stop.sh    # 启停脚本
└── update.sh             # 更新脚本
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
- [x] **v1.5** 稳定性加固发布（LLM 调用重试、工具超时与异常隔离、空回复兜底）
- [ ] **v1.6** 多智能体并行协作优化 + 外部威胁情报接入

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

### v1.5.0（当前）

- 🔒 **LLM 调用异常捕获与指数退避重试**：网络抖动 / 超时 / 限流(429) / HTTP 5xx 等临时故障自动重试（1s → 2s，最多 2 次）；404 / 401 / 400 等配置类错误不重试、直接暴露诊断，避免配置损坏时反复无效请求。覆盖非流式工具循环与 SSE 流式主路径（流式仅连接阶段可安全重试，中途断流不重放，防止重复内容）
- 🛡️ **工具执行超时保护**：单次工具调用超 5 分钟即中止并返回超时提示，网络卡住 / subprocess 阻塞等挂起工具不再拖死整轮对话
- 🛡️ **工具执行异常隔离**：单个工具抛异常不再中断整轮对话，转为错误消息返回给模型，由模型决定重试或改道
- 🛡️ **工具参数防御**：模型生成 `null` / 数组 / 字符串等非法参数形态不再抛 `TypeError`，调用链保持健壮
- 🐛 **非流式空回复兜底**：与流式路径对齐，模型空回复时最多强制续写一次，避免用户拿到空响应
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

[![Version](https://img.shields.io/badge/version-v1.5.0-4C9F38?style=flat-square)](https://github.com/DarkSword404/baize-core)

</div>

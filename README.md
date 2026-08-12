# 白泽 (Baize) 🐉

**AI 驱动的网络安全 Agent 平台**

白泽（Baize）是一款面向安全研究人员、红蓝队工程师和企业安全运营团队的 AI 驱动网络安全 Agent 平台，通过多智能体协同、大语言模型推理和安全工具编排，实现自动化安全分析、渗透测试辅助、安全运营和知识增强。

---

## ✨ 特性

- 🧠 **推理过程可视化**：支持展示模型返回的推理状态、分析步骤和工具执行轨迹，帮助用户理解 Agent 决策过程
- 🤖 **30 个安全智能体**：按安全生命周期（攻击模拟 / 防御运营 / 安全研发 / 通用编排）分类组织
- 🛠 **11 个安全工具**：命令执行、HTTP 请求、端口扫描、代码执行、SSH、Web 搜索、Shodan 等
- 🛠 **Agent Builder 智能体**：通过对话式创建流程，一键生成自定义安全智能体并即时使用
- 🔧 **工具调用可视化**：完整展示工具调用参数与输出，便于排查智能体行为
- 📜 **可配置滑动窗口上下文**：仅保留最近 N 轮对话作为上下文，显著节省 token
- 🔐 **安全认证**：启动自动生成一次性登录凭证，密码/Token 每次重启重新生成
- 🌐 **B/S 架构**：FastAPI 后端 + React 前端，浏览器即用

---

## 🧰 技术栈

**Frontend**

- React
- TypeScript
- Vite

**Backend**

- FastAPI
- Python
- Pydantic + Uvicorn

**AI**

- OpenAI 兼容 API
- Function Calling
- Agent Workflow
- 流式推理可视化

**Security**

- Nmap（端口扫描）
- Shodan（网络资产检索）
- HTTP Scanner
- 自定义安全工具

---

## 🚀 快速开始

```bash
# 1. 安装（创建虚拟环境 + 安装前后端依赖）
./setup.sh

# 2. 启动（自动输出一次性登录凭证）
./start.sh

# 3. 打开前端
# http://localhost:5173
```

**停止服务：**

```bash
./stop.sh            # 优雅停止
./stop.sh --hard     # 强制停止
```

---

## ⚙️ 配置模型

在 Web 界面 **设置 → 模型配置** 中填写：

| 配置项 | 说明 |
|--------|------|
| Base URL | OpenAI 兼容端点，如 `https://api.example.com/v1` |
| API Key | 接口密钥 |
| Model | 模型名，如 `deepseek-v4-flash` |
| 上下文滑动窗口（轮数） | 保留最近 N 轮对话作为上下文，`0`=不限制（默认），建议 `8–20` |

也可直接编辑 `~/.baize/model.json`：

```json
{
  "base_url": "https://api.example.com/v1",
  "api_key": "sk-...",
  "model": "deepseek-v4-flash",
  "context_max_turns": 15
}
```

### 上下文滑动窗口说明

- 以"轮"为单位（一轮 = 一次 user 提问及其对应的 assistant 回复 / 工具调用组）切分历史
- 仅保留**最近 N 轮**作为上下文，控制每次请求的 token 用量
- 裁剪不会拆散某一轮（工具调用消息与对应提问始终成组保留）
- 当前正在发送的最新消息始终保留

默认 `0` 表示不限制。对长会话建议设为 `8–20`。

---

## 🤖 智能体体系

### 🔴 攻击模拟 Agent

| Agent | 能力 |
|-------|------|
| `Red Team Agent` | 红队攻击模拟 |
| `Web Bounty Agent` | Web 漏洞挖掘与赏金 |
| `Web Application Pentester` | Web 应用渗透测试 |
| `CTF agent` | CTF 解题 |
| `Exploit Expert` | 漏洞利用专家 |
| `APT Agent` | 高级持续性威胁模拟 |
| `Replay Attack Agent` | 重放攻击测试与防重放校验 |
| `Bug Bounty Hunter` | 漏洞赏金猎人 |
| `Sub-GHz / SDR Agent` | Sub-GHz / SDR 射频分析 |

### 🔵 防御运营 Agent

| Agent | 能力 |
|-------|------|
| `Blue Team Agent` | 防御分析 |
| `DFIR Agent` | 数字取证与事件响应 |
| `Network Analyzer` | 网络流量分析 |
| `DNS / SMTP Agent` | 邮件与域名安全 |
| `Compliance Agent` | 风险与合规 |
| `Cybersecurity Triage Agent` | 安全告警分流 |
| `Memory Analysis Agent` | 内存取证分析 |

### 🧬 安全研发 Agent

| Agent | 能力 |
|-------|------|
| `CodeAgent` | 安全编码 |
| `Android SAST Agent` | Android 静态应用安全测试 |
| `Android App Logic Mapper` | Android 应用逻辑映射 |
| `Reverse Engineering Agent` | 逆向工程 |
| `WiFi Security Agent` | Wi-Fi 安全测试 |

### 🧭 通用与编排 Agent

| Agent | 能力 |
|-------|------|
| `Agent Builder` | 对话式创建自定义智能体 |
| `Reporting Agent` | 安全报告 |
| `Continuous Ops Agent` | 连续运维 |
| `Orchestration Agent` | 多智能体编排入口 |
| `Selection Agent` | 智能体选择路由 |
| `Thought Router` | 思考路由 |
| `Reasoner Supporter` | 推理辅助 |
| `Flag Discriminator` | Flag 判定 |
| `Use Cases Agent` | 用例助手 |

> 每个智能体配备**差异化工具集**，仅暴露完成任务所需的工具，减少误用、提升效率。

---

## 🛠 安全工具

| 工具 | 能力 |
|------|------|
| `generic_linux_command` | 在 Linux 环境执行命令 |
| `http_request` | 发起 HTTP 探测与请求（带 SSRF 防护） |
| `port_scan` | TCP 端口与服务探测 |
| `execute_code` | 执行 Python 代码片段 |
| `run_ssh_command_with_credentials` | SSH 连接与远程命令执行 |
| `make_web_search_with_explanation` | Web 在线检索 |
| `shodan_search` | Shodan 网络资产检索 |
| `analyze_task_requirements` | 任务需求分析与智能体推荐 |
| `check_available_agents` | 列出可用智能体 |
| `verify_csv_inventory` | CSV 资产清单验证 |
| `think` | 记录中间推理过程 |

---

## 🏗 架构

```
                    用户
                     │
              Web Console (React)
                     │
             Agent Orchestrator (FastAPI)
                     │
        ┌────────────┴────────────┐
        │                         │
   Agent Router              Security Agents
        │                         │
        └────────────┬────────────┘
                     │
              Tool Manager
                     │
        ┌────────────┼────────────┐
        │            │            │
      Nmap          HTTP         SSH
        │            │            │
       Shodan       PortScan    Execute Code
                     │
              Context Manager
             (滑动窗口上下文)
                     │
              LLM Gateway
                     │
       OpenAI 兼容模型 (base_url)
```

**核心模块：**

- **Agent Orchestrator**：会话管理、认证、模型配置、流式对话（SSE）
- **Agent Router**：按任务需求路由到合适的智能体（`Selection Agent` / `analyze_task_requirements`）
- **Agent Builder**：对话式智能体工厂，自动生成自定义智能体（写入 `~/.baize/custom/agents/`，即时可用）
- **Tool Manager**：安全工具的注册、执行与结果回收
- **Tool Sandbox**：工具执行的隔离与安全防护（如 SSRF 防护）
- **Context Manager**：滑动窗口上下文管理，控制 token 用量
- **LLM Gateway**：统一接入 OpenAI 兼容端点，支持函数调用与流式推理

---

## 📁 目录结构

```
baize-core/
├── src/baize/
│   ├── api/          # FastAPI 后端（认证、会话、模型配置、对话）
│   ├── sdk/          # LLM 客户端 + Agent 框架（独立实现）
│   ├── agents/       # 智能体定义（30 个内置智能体）
│   ├── tools/        # 工具（命令、HTTP、端口扫描）
│   ├── prompts/      # 智能体系统提示词（中文指令）
│   ├── config.py     # 配置
│   └── cli.py        # 命令行入口
├── web/              # React 前端
├── docs/             # 文档
├── start.sh          # 启动脚本
├── stop.sh           # 停止脚本
├── setup.sh          # 安装脚本
└── update.sh         # 更新脚本
```

---

## 🗺 Roadmap

### v1.2（当前 ✅）

- ✅ B/S 架构
- ✅ Agent 框架
- ✅ 30 个安全智能体
- ✅ Agent Builder 对话式创建自定义智能体
- ✅ 工具调用与可视化
- ✅ OpenAI 兼容模型接入
- ✅ 推理过程可视化
- ✅ 滑动窗口上下文管理

---

## ❓ 常见问题

**Q：启动后从哪里获取登录凭证？**
A：运行 `./start.sh` 时会在终端输出一次性生成的用户名、密码和 Token。凭证每次重启自动重新生成。

**Q：如何更换模型？**
A：在 Web 界面 **设置 → 模型配置** 修改 Base URL / API Key / Model 后保存即可。

**Q：为什么看不到模型推理过程？**
A：是否展示推理过程取决于模型本身。部分推理模型在复杂问题下会返回推理状态与中间步骤，简单问题可能直接回答。对话时点击顶栏 **"推理"** 按钮可打开推理时间线查看。

**Q：上下文被裁剪后，模型是否忘记早先对话？**
A：启用滑动窗口后，模型只"记得"最近 N 轮。如需保留更长上下文，可增大轮数或设为 `0`（不限制）。

---

## ⚠️ 安全声明

白泽（Baize）仅用于**合法授权的安全测试、安全研究和防御建设**。

使用者必须确保拥有目标系统的**合法授权**。开发者不承担未经授权使用本项目造成的任何法律责任或安全影响。

请遵守当地法律法规，仅在授权范围内使用本项目。

---

## 🙏 致谢

本项目在架构与智能体设计理念上深受 **CAI**（[aliasrobotics/cai](https://github.com/aliasrobotics/cai)）——一个开源 AI 网络安全框架的启发。感谢 CAI 社区在 AI 安全 Agent 领域的前沿探索与贡献。

白泽在 CAI 的基础上进行了**独立的代码重构**，采用全新的 B/S 架构与自定义实现，智能体指令以中文重新编写，并引入了推理过程可视化、滑动窗口上下文管理等增强能力。致敬并感谢所有开源安全社区的同行者。

---

## 📝 更新日志

### v1.2.0（2026-08-11）

**🐛 Bug 修复**

- 修复对话刷新后出现空消息的问题：思考过程与工具调用消息此前与正常回复混存，刷新后显示为大量空白消息，现已将中间产物（思考 / 工具调用 / 结果）与最终回复分离存储与渲染。
- 修复历史消息污染模型上下文的问题：拼接对话历史时过滤中间产物，避免模型对空消息产生幻觉。

**🚀 新增**

- 内置安全智能体数量提升至 **30 个**，并整体优化智能体指令与工具集配置。
- 新增 **Agent Builder** 智能体：通过对话式创建流程一键生成自定义智能体，创建后立即出现在智能体列表并可直接参与对话。

**📌 变更**

- 版本号升级至 `1.2.0`。

### v1.1.0（2026-08-09）

**🚀 新增：多模态附件能力**

- 支持用户上传多种附件类型：图片、代码、压缩包、文档等。
- 新增附件上传 / 列表 / 删除 API（`/api/v1/sessions/{session_id}/files`）。
- 图片附件直接注入大模型视觉（`image_url`），支持 CTF 图片隐写、报告截图分析等。
- 代码 / 压缩包 / 文档附件通过 Agent 工具按需读取分析。
- 新增 4 个附件工具：`list_attachments`、`read_attachment_file`、`extract_attachment_archive`、`read_extracted_file`。
- 前端新增 📎 附件上传按钮、待发送附件列表、消息内附件展示（带类型图标）。

**🛡 安全加固**

- 上传文件类型白名单校验，不支持的类型直接拒绝。
- 单文件大小上限（默认 20MB）。
- 压缩包解压沙箱隔离（防目录穿越、zip 炸弹防护：限制解压总大小与文件数）。
- 附件工具只能访问当前会话附件目录（路径安全校验）。

**🔧 依赖变更**

- 新增 `python-multipart` 依赖（用于附件上传）。

**📌 变更**

- 版本号升级至 `1.1.0`。

### v1.0.1（2026-08-09）

**🐛 Bug 修复**

- 修复流式对话中断时内容丢失的问题：在对话进行思考但未完成回复时，若切换页面或刷新页面，思考过程、工具调用流程及文本回复不再丢失。
- 会话开始前即持久化 user 提问，确保切换/刷新也不丢。
- 流式过程中实时累积思考过程、工具调用/结果、最终文本，并在连接断开时统一写入会话历史。

**📌 变更**

- 会话存储支持额外字段，可保存推理过程（reasoning）与工具调用（function_call / function_call_output）等中间产物。
- 版本号升级至 `1.0.1`。

### v1.0.0（2026-08-07）

**🚀 首个发布版**

- B/S 架构（FastAPI 后端 + React 前端）
- Agent 框架 + 20+ 安全智能体 + 11 个安全工具
- 推理过程可视化 + 工具调用可视化
- 可配置滑动窗口上下文管理
- 一次性登录凭证安全认证
- 多智能体编排与渗透测试流水线

---

## 📄 许可

MIT License，详见 [LICENSE](LICENSE)。

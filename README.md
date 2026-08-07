# 白泽 (Baize) 🐉

**AI 驱动的网络安全 Agent 平台**

白泽（Baize）是一款面向安全研究人员、红蓝队工程师和企业安全运营团队的 AI 驱动网络安全 Agent 平台，通过多智能体协同、大语言模型推理和安全工具编排，实现自动化安全分析、渗透测试辅助、安全运营和知识增强。

---

## ✨ 特性

- 🧠 **推理过程可视化**：支持展示模型返回的推理状态、分析步骤和工具执行轨迹，帮助用户理解 Agent 决策过程
- 🤖 **20+ 安全智能体**：按安全生命周期（攻击模拟 / 防御运营 / 安全研发）分类组织
- 🛠 **11 个安全工具**：命令执行、HTTP 请求、端口扫描、代码执行、SSH、Web 搜索、Shodan 等
- 🏆 **多智能体编排**：Agent Router 路由 + 渗透测试流水线，支持阶段性协同作战
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

> ⚠️ **安全提示**：`~/.baize/model.json` 保存了你的真实 API Key，位于用户主目录，
> 不会被提交到版本库或发布包中。请勿将其共享或上传。

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
| `web_pentester_agent` | Web 漏洞测试 |
| `redteam_agent` | 红队攻击模拟 |
| `recon_agent` | 信息收集与侦察 |
| `ctf_agent` | CTF 解题 |
| `retester_agent` | 漏洞复测 |
| `subghz_sdr_agent` | Sub-GHz / SDR 射频分析 |

### 🔵 防御运营 Agent

| Agent | 能力 |
|-------|------|
| `blueteam_agent` | 防御分析 |
| `dfir_agent` | 数字取证与事件响应 |
| `network_analysis_agent` | 网络流量分析 |
| `dns_smtp_agent` | 邮件安全 |
| `compliance_agent` | 风险与合规 |

### 🧬 安全研发 Agent

| Agent | 能力 |
|-------|------|
| `codeagent` | 安全编码 |
| `android_sast_agent` | Android 静态应用安全测试 |
| `reverse_engineering_agent` | 逆向工程 |
| `wifi_security_tester` | Wi-Fi 安全测试 |

### 🧭 通用与编排 Agent

| Agent | 能力 |
|-------|------|
| `general_agent` | 通用 AI 助手 |
| `reporting_agent` | 安全报告 |
| `continuous_ops_agent` | 连续运维 |
| `orchestration_agent` | 多智能体编排 |
| `selection_agent` | 智能体选择路由 |

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
- **Agent Router**：按任务需求路由到合适的智能体（`selection_agent` / `analyze_task_requirements`）
- **Tool Manager**：安全工具的注册、执行与结果回收
- **Tool Sandbox**：工具执行的隔离与安全防护（如 SSRF 防护）
- **Context Manager**：滑动窗口上下文管理，控制 token 用量
- **LLM Gateway**：统一接入 OpenAI 兼容端点，支持函数调用与流式推理

---

## 📁 目录结构

```
baize/
├── src/baize/
│   ├── api/       # FastAPI 后端（认证、会话、模型配置、对话）
│   ├── sdk/       # LLM 客户端 + Agent 框架（独立实现）
│   ├── agents/    # 智能体定义（中文指令）
│   ├── tools/     # 工具（命令、HTTP、端口扫描）
│   ├── config.py  # 配置
│   └── cli.py     # 命令行入口
├── web/           # React 前端
├── docs/          # 文档
├── start.sh       # 启动脚本
├── stop.sh        # 停止脚本
└── setup.sh       # 安装脚本
```

---

## 🗺 Roadmap

### v1.0（当前 ✅）

- ✅ B/S 架构
- ✅ Agent 框架
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

## 📄 许可

MIT License，详见 [LICENSE](LICENSE)。

# Baize 扩展指南（EXTENDING）

本文档面向希望为 Baize 增加能力的开发者，覆盖本轮升级引入的四大扩展点：

- [1. 标准 Tool 协议 + 动态注册](#1-标准-tool-协议--动态注册)
- [2. 模型层抽象](#2-模型层抽象)
- [3. Agent 定义扩展（state / memory / hooks）](#3-agent-定义扩展)
- [4. 安全执行环境抽象](#4-安全执行环境抽象)
- [5. 插件市场（entry point 机制）](#5-插件市场)

## 1. 标准 Tool 协议 + 动态注册

**核心模块**：`baize/tools/registry.py`（`ToolSpec` / `ToolRegistry` / `register_tool`）

### ToolSpec — 工具协议

一个工具 = 名称 + 描述 + 处理函数 + 参数 Schema：

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | `str` | 工具唯一名称（模型调用标识） |
| `description` | `str` | 功能描述（供 LLM 选择工具） |
| `handler` | `Callable` | 执行函数（同步或异步均可） |
| `parameters` | `dict \| None` | JSON Schema；`None` 时从函数签名自动推导 |
| `category` | `str` | 分类（general/recon/exploit/web/...） |
| `author` / `version` | `str` | 来源标识 |
| `tags` | `list[str]` | 附加标签 |

### 推荐用法：`@register_tool` 装饰器

```python
from baize.tools import register_tool

@register_tool(
    description="对目标执行端口扫描，返回开放端口列表",
    category="recon",
    tags=["network", "enum"],
)
async def port_scan(host: str, ports: str = "1-1000", timeout: int = 60) -> str:
    """host: 目标主机 IP 或域名。"""
    ...  # 实现扫描逻辑
    return "22/tcp open"
```

- 工具名默认取函数名，参数 Schema 从类型注解自动推导
  （支持 `str` / `int` / `float` / `bool` / `list` / `dict` 与 `Optional`）；
- 支持 `from __future__ import annotations`（注解字符串化）；
- 函数内必须写 `:param:` 风格或 docstring 描述参数，会进入 Schema 的 `description`。

### 动态注册 / 注销 / 查询

```python
from baize.tools import registry

registry.names()                       # 全部工具名
registry.get("port_scan")              # 取单个
registry.by_category("recon")          # 按分类
registry.all()                         # 全部 ToolSpec
registry.unregister("my_tool")         # 注销
spec.to_schema()                       # → OpenAI function calling schema
spec.to_agent_tool()                   # → 运行时 AgentTool
```

注册冲突默认抛 `ValueError`，需覆盖时 `override=True`。

### 旧式 AgentTool 兼容

```python
from baize.sdk import AgentTool
AgentTool(name="x", description="...", parameters={...}, handler=fn)
```

`AgentTool` 依然可用；`ToolSpec` 是其超集，二者可通过 `to_agent_tool()` / `ToolRegistry.to_agent_tools()` 互转。

## 2. 模型层抽象

**核心模块**：`baize/sdk/models.py`

| 类 | 职责 |
|---|---|
| `BaseChatModel` | 抽象基类：`async complete(history, tools) -> CompletionResult`、`async stream(...)` |
| `OpenAICompatibleModel` | 任何 OpenAI Chat Completions 兼容端点（OpenAI/DeepSeek/Ollama/vLLM/网关） |
| `LLMClient` | 旧 API 兼容子类（行为与 `baize.sdk.client.LLMClient` 一致） |
| `ModelRegistry` / `model_registry` | 按名称注册模型提供方工厂 |
| `ModelRouter` | 多模型路由：primary + fallbacks 链式降级 |

### 接入新模型 Provider

```python
from baize.sdk import BaseChatModel, ModelRouter, model_registry
from baize.sdk.models import CompletionResult, CompletionUsage

class MyModel(BaseChatModel):
    async def complete(self, history, *, tools=None, temperature=0.7) -> CompletionResult:
        ...  # 调用你的模型，返回 CompletionResult(content=..., usage=...)
    async def stream(self, history, *, tools=None, temperature=0.7):
        ...  # yield CompletionResult(content=..., tool_calls_delta=[...])

model_registry.register("my_model", lambda config: MyModel())

# Agent 内使用
agent = Agent(name="x", model_provider="my_model", instructions="...")
```

### ModelRouter 降级链

```python
router = ModelRouter(primary="deepseek", fallbacks=["qwen", "ollama"])
agent = Agent(name="x", model_router=router, instructions="...")
# primary 失败 → 依次尝试 fallbacks；全部失败才抛错
```

## 3. Agent 定义扩展

**核心模块**：`baize/sdk/agent.py`（`Agent`）、`baize/sdk/memory.py`

### state — 运行时状态

`Agent.state: dict` 随每次运行合并进 `context_variables` 注入系统指令
（模板中可用 `{state}` 占位符），工具可读写该字典共享中间结果：

```python
agent = Agent(name="recon", state={"scope": "10.0.0.0/24"}, instructions="目标是 {state}")

# 运行后
result = await agent.run("开始侦察")
print(agent.state)  # 工具可向其中写入探测结果
```

### memory — 记忆注入

实现 `BaseMemory`（`load`/`save`/`clear`），或用内置 `InMemoryMemory`：

```python
from baize.sdk import Agent, InMemoryMemory

agent = Agent(
    name="ops",
    instructions="你是运维助手",
    memory=InMemoryMemory(),
    session_id="team-a",          # 按会话隔离记忆
)
await agent.run("检查告警")       # 运行结束自动保存对话摘要
await agent.run("继续排查")       # 下一轮自动注入 [历史记忆] 块
```

自定义持久化（SQLite/Redis/文件）只需继承 `BaseMemory`。

### hooks — 生命周期回调（支持瀑布式事件链）

```python
def on_tool_call(agent, name, args): ...
async def on_tool_result(agent, name, result): ...
def on_done(agent, output): ...

agent = Agent(
    name="audit",
    instructions="...",
    hooks={
        "on_start": lambda a, msg, ctx: print("开始", msg),
        "on_tool_call": on_tool_call,
        "on_tool_result": on_tool_result,
        "on_text": lambda a, t: None,
        "on_done": on_done,
        "on_error": lambda a, e: print("失败", e),
    },
)
```

可用钩子：`on_start` / `on_tool_call` / `on_tool_result` / `on_text` / `on_done` / `on_error`。
同步与异步 callable 皆可；钩子异常仅告警，不中断 Agent 执行。

**多处理器 + 瀑布式事件链**：hook 值可以是 callable **或 callable 列表**，
多个处理器按序执行，实现策略插件叠加（对齐 deepseek-harness 的 tools/* 事件）：

```python
async def audit_hook(agent, name, args, next):
    """记录审计日志后放行（调用 next 继续链）。"""
    logger.info("工具调用: %s %s", name, args)
    return await next()

async def policy_hook(agent, name, args, next):
    """拦截策略：禁止扫描链路本地地址。"""
    if name == "nmap_scan" and "169.254" in args:
        return {"deny": True, "reason": "策略: 禁止扫描链路本地地址"}
    return await next()

agent = Agent(
    name="pentest",
    tools=...,
    hooks={"on_tool_call": [audit_hook, policy_hook]},  # 依次执行，可短路
)
```

瀑布式 `on_tool_call` 处理器签名：`(agent, tool_name, arguments, next)`：

| 行为 | 写法 |
|---|---|
| 继续链 | `return await next()` |
| 改写参数后继续 | `return await next('{"host": "safe"}'')` |
| 拦截本次调用（短路） | `return {"deny": True, "reason": "..."}` |

旧式三参签名 `(agent, name, args)` 自动继续链，向后兼容。

### session_log — 会话日志（单一事实源 / 审计重放）

**核心模块**：`baize/sdk/session_log.py`（`SessionLog` / `SessionEvent`）

配置后，Agent 每次运行自动按序记录 `session/start`、`user/message`、
`agent/request`、`agent/response`、`tool/call`、`tool/result`、`turn/start`、
`turn/end`、`session/end` 事件（append-only，不可修改）：

```python
from baize.sdk import Agent, SessionLog

log = SessionLog(path="/var/log/baize/session.jsonl")  # 可选 JSONL 落盘
agent = Agent(name="audit", tools=..., session_log=log)

result = await agent.run("侦察目标并生成报告")

# 从日志投影模型历史（单一事实源，可重建任意时刻上下文）
messages = log.derive_messages()

# 人类可读审计回放（合规 / DFIR 取证）
for line in log.replay():
    print(line)

# 导出 JSONL 存档
log.save("/backup/session.jsonl")
```

审计要点：

- **可重建**：`derive_messages()` 从日志投影 OpenAI chat 格式历史，
  任何到达模型的内容都能从日志重建；
- **可重放**：`replay()` 生成完整攻击链的人类可读时序（含工具参数与结果、
  拦截原因），满足合规审计与事件取证；
- **可持久化**：构造时传 `path` 即 append-only 落盘，重启后自动续写。

## 4. 安全执行环境抽象

**核心模块**：`baize/executors.py`

| 执行器 | 场景 |
|---|---|
| `LocalExecutor` | 本机 subprocess（默认） |
| `DockerExecutor` | Docker 容器隔离（扫描工具不污染宿主） |
| `SSHExecutor` | 远程主机 / 分布式扫描 |

```python
from baize.executors import build_executor, ExecutorConfig

# 直接构建
executor = build_executor(ExecutorConfig(backend="docker", image="instrumentisto/nmap"))
result = await executor.run("nmap -sV 10.0.0.1", timeout=300)
print(result.returncode, result.stdout[:200], result.timed_out)

# 或在工具内部使用（支持环境变量切换，见下方）
```

**环境变量切换后端**（部署时无需改代码）：

```bash
BAIZE_EXEC_BACKEND=docker          # local | docker | ssh
BAIZE_EXEC_IMAGE=instrumentisto/nmap
BAIZE_EXEC_HOST=10.0.0.5
BAIZE_EXEC_USERNAME=root
BAIZE_EXEC_PORT=22
BAIZE_EXEC_KEY_PATH=~/.ssh/id_rsa
BAIZE_EXEC_SANDBOX=read_only       # read_only | workspace_write | danger_full_access
```

### 沙箱维度（fail-closed，对齐 deepseek-harness Sandbox）

每次执行携带 `SandboxMode` 请求隔离等级，后端**诚实报告** `EnforcementLevel`：

| SandboxMode | 含义 | Local 后端行为 |
|---|---|---|
| `read_only` | 只读隔离，禁止写文件系统 | 有 bwrap 时内核级只读隔离；无则抛 `SandboxUnavailableError` |
| `workspace_write` | 工作区可写（临时文件/报告） | 有 bwrap 时只读挂载 `/` + 工作区可写；无则抛错 |
| `danger_full_access` | 完全访问（本地透传） | 直接执行（enforcement=none，如实报告） |

```python
from baize.executors import SandboxMode

# 请求隔离：有 bwrap 则真实隔离；没有则明确失败，绝不假装隔离
result = await executor.run("nmap -sV 10.0.0.1", sandbox=SandboxMode.READ_ONLY)
print(result.enforcement)   # "full" | "partial" | "none"  —— 诚实报告隔离强度
print(result.error_kind)    # None | "sandbox_denied" | "runner_failure" | "timeout"
```

**错误双通道分类**：`sandbox_denied`（安全机制在起作用，如 EROFS/EACCES/
read-only file system）与 `runner_failure`（执行基础设施故障，如命令不存在）
严格区分，审计时一目了然 —— "基础设施坏了"与"安全机制在工作"绝不混为一谈。

`ExecResult` 提供结构化结果（stdout/stderr/returncode/timed_out/duration/executor/
sandbox/enforcement/error_kind）。
工具开发建议**只通过执行器执行命令**，不直接 `subprocess`，这样同一工具可无缝切换后端。

## 5. 插件市场

见 [PLUGIN_MARKET.md](PLUGIN_MARKET.md) 与可运行示例
`examples/security-tools-plugin/`。

---

## 快速对照（Baize ↔ LangChain / deepseek-harness）

| 能力 | LangChain | deepseek-harness | Baize |
|---|---|---|---|
| 工具协议 | `BaseTool` / `@tool` | `ctx.tools` 注册 | `ToolSpec` / `@register_tool` |
| 工具注册 | `ToolRegistry`（LangChain Hub） | 一切皆插件 | `ToolRegistry` + entry point 自动发现 |
| 模型抽象 | `BaseChatModel` | LLM 适配器接缝 | `BaseChatModel` + `ModelRouter` fallback |
| Agent 内存 | `BaseMemory` / `ConversationBufferMemory` | 会话日志投影 | `BaseMemory` / `InMemoryMemory` |
| 回调/事件 | `BaseCallbackHandler` | 瀑布式事件（tools/*、agent/*） | `hooks` 多处理器 + `next()` 瀑布链 |
| 会话日志 | - | Session Log（单一事实源） | `SessionLog`（append-only + 审计重放） |
| 执行环境 | `RunnableConfig` + 自定义 | `ctx.shell` + fail-closed 沙箱 | `BaseExecutor` + `SandboxMode` fail-closed |
| 安全工具集 | 需自行集成 | 生态扩展 | 29 个内置安全工具 + 浏览器工具 |

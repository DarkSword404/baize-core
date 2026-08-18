# Baize 插件市场（Plugin Market）

Baize 通过 **Python entry point** 实现工具插件机制：插件打包为独立
Python 包，声明 `baize.tools` entry point 组，安装后即被自动发现，
**无需修改 baize 源码**。

## 插件发现机制

`ToolRegistry.discover_entry_points(group="baize.tools")` 在
`baize/tools/__init__.py` 导入时自动执行。每个 entry point 支持三种形态：

1. **可调用对象** `fn(registry) -> None`（推荐，见示例插件的 `register` 函数）；
2. **模块**，加载后查找 `register` / `register_tools` 函数并调用；
3. **模块级属性 `tools`**：`ToolSpec` / `AgentTool` 的可迭代对象。

同一插件不会重复加载（按 entry point 名称去重）。

## 编写插件

最小骨架（完整示例见 `examples/security-tools-plugin/`）：

```
my-plugin/
├── pyproject.toml
├── README.md
└── src/my_tools/
    └── __init__.py        # 工具定义 + register 入口
```

`pyproject.toml` 声明 entry point：

```toml
[project]
name = "my-baize-tools"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["baize-core>=1.3.1"]

[project.entry-points."baize.tools"]
my_tools = "my_tools:register"     # 名称需全局唯一
```

`src/my_tools/__init__.py`：

```python
from baize.tools import register_tool

@register_tool(description="示例工具", category="general", tags=["demo"])
def my_tool(target: str) -> str:
    return f"handled {target}"

def register(registry):
    """entry point 入口：也可在此手动 registry.register(ToolSpec(...))"""
    # 若工具全部用 @register_tool 装饰，这里可以留空或仅做初始化
    pass
```

> 装饰器注册是模块导入副作用 —— entry point 加载模块时即完成注册；
> `register(registry)` 用于需要手动控制（参数校验、动态构造工具）的场景。

## 发布与安装

```bash
# 本地/内部源发布
pip install ./my-plugin
# 或可编辑安装（开发调试）
pip install -e ./my-plugin
```

安装后验证：

```python
from baize.tools import registry
assert "my_tool" in registry.names()   # 已自动发现
```

卸载：`pip uninstall my-baize-tools`，重启进程后工具消失。

## 安全与规范建议

- **命名唯一**：工具名避免与内置工具冲突（冲突默认抛 `ValueError`，
  可 `override=True` 覆盖，但会掩盖同名内置工具）。
- **危险操作显式声明**：涉及命令执行的工具请在 `description` 中注明
  所需授权；参考内置安全工具的黑名单拦截模式（`security_tools.py`，
  危险参数如 sqlmap `--os-shell` 默认拦截，`BAIZE_ALLOW_WEAPONIZED=1`
  显式授权）。
- **执行环境**：命令执行统一走 `baize.executors.build_executor()`，
  让部署方可切换 local/docker/ssh 后端。

## 建议的插件方向（示例）

| 插件 | 覆盖工具 |
|---|---|
| `baize-tools-web` | ffuf/feroxbuster/httpx/wappalyzer 等 Web 侦查 |
| `baize-tools-cloud` | aws/gcp/azure CLI 枚举与策略审计 |
| `baize-tools-forensic` | volatility/strings/binwalk 取证分析 |
| `baize-tools-iot` | 固件分析、MQTT/Modbus 协议探测 |

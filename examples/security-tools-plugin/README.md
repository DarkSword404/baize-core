# Baize 安全工具插件示例

一个最小可用的 Baize 插件，演示三种扩展能力：

1. **标准 Tool 协议** — 使用 `@register_tool` 声明式注册工具；
2. **动态注册机制** — 通过 `baize.tools` entry point 被自动发现，无需修改 baize 源码；
3. **执行环境抽象** — 工具内部通过 `build_executor()` 执行命令，
   可通过环境变量在 local / docker / ssh 三种后端间切换。

提供 3 个工具（均为无害的侦查类演示）：

| 工具 | 说明 |
|---|---|
| `crt_lookup` | 查询 crt.sh 证书透明度日志（子域名枚举辅助） |
| `robots_probe` | 抓取目标站点 robots.txt |
| `example_noop` | 占位工具（演示 `register(registry)` 手动注册） |

> ⚠️ 仅用于演示插件开发模式，请勿对未授权目标使用。

## 安装（两种方式）

### 方式一：安装到 baize 环境中（推荐，entry point 自动发现）

```bash
cd baize-core/examples/security-tools-plugin
pip install -e .          # 装入当前 Python 环境（baize-core 同环境）
```

安装后，任意 `import baize.tools` 都会自动扫描 `baize.tools` entry point 组并注册本插件的工具：

```python
from baize.tools import registry
print(registry.names())  # 输出将包含 crt_lookup / robots_probe / example_noop
```

### 方式二：不安装，手动注册

```python
import sys
sys.path.insert(0, "examples/security-tools-plugin/src")
from baize.tools import registry
import security_tools_plugin
security_tools_plugin.register(registry)
```

## 切换执行后端

工具默认在本机执行（local）。通过环境变量可切换后端，无需改代码：

```bash
export BAIZE_EXEC_BACKEND=docker          # 在 Docker 容器中隔离执行
export BAIZE_EXEC_BACKEND=ssh
export BAIZE_EXEC_HOST=10.0.0.5
export BAIZE_EXEC_USERNAME=root
export BAIZE_EXEC_KEY_PATH=~/.ssh/id_rsa
```

详见 `baize/executors.py` 与 `docs/EXTENDING.md`。

## 开发一个自己的插件

1. 复制本目录为模板，重命名包与 entry point 名称；
2. 在 `__init__.py` 中用 `@register_tool(...)` 定义你的工具；
3. `pip install -e .` 即可被 baize 自动发现。

完整协议见 `docs/EXTENDING.md` 与 `docs/PLUGIN_MARKET.md`。

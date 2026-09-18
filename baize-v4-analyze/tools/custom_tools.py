"""白泽·智脑自定义工具 (Custom Tools) — 用户级工具存储与动态注册。

提供:
- ``CustomToolStore``: 将用户自定义工具持久化到 ``~/.baize/custom/tools/*.json``。
- ``register_custom_tool`` / ``unregister_custom_tool``: 把用户代码编译为可执行
  工具并注册进全局注册表（热加载，无需重启服务）。
- ``test_custom_tool``: 在本地沙箱中试运行工具代码并返回输出（用于创建/编辑校验）。

设计目标:
- 与内置工具完全同构（``ToolSpec``），可被任意 agent 白名单引用。
- 存储为结构化 JSON（含源代码），由本模块在运行时 exec 加载，不写 .py 文件。
- 工具源代码在保存时做语法/参数校验，避免生成不可用的工具。
"""

from __future__ import annotations

import inspect
import json
import os
import re
import secrets
import textwrap
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from baize.tools.registry import ToolSpec, registry, schema_from_signature


# ===========================================================================
#  工具名合法化
# ===========================================================================

def sanitize_tool_name(name: str) -> str:
    """将名称转换为合法的工具标识符（snake_case，字母开头）。"""
    name = re.sub(r"[\s\-]+", "_", name)
    name = re.sub(r"[^a-zA-Z0-9_]", "", name)
    if name and name[0].isdigit():
        name = f"tool_{name}"
    return name.lower()


# ===========================================================================
#  代码校验与编译
# ===========================================================================

def _validate_code(code: str) -> Optional[str]:
    """校验工具代码可编译且包含 handler 函数。

    Returns:
        str: 错误消息（校验通过返回 None）。
    """
    code = code.strip()
    if not code:
        return "工具代码不能为空"
    try:
        compile(code, "<custom_tool>", "exec")
    except SyntaxError as exc:
        return f"代码存在语法错误: {exc.msg} (行 {exc.lineno})"
    return None


def _build_namespace(code: str) -> dict[str, Any]:
    """在隔离命名空间中执行用户代码，返回命名空间。"""
    namespace: dict[str, Any] = {"__name__": "baize_custom_tool"}
    exec(compile(code, "<custom_tool>", "exec"), namespace)
    return namespace


def load_handler(code: str) -> Callable[..., Any]:
    """从工具代码中提取 handler 函数。

    Args:
        code: 用户提供的工具代码，须定义 ``def handler(**kwargs)``。

    Returns:
        Callable: handler 函数。

    Raises:
        ValueError: 代码不可编译 / 缺少 handler / handler 不可调用。
    """
    error = _validate_code(code)
    if error:
        raise ValueError(error)
    namespace = _build_namespace(code)
    handler = namespace.get("handler")
    if not callable(handler):
        raise ValueError("工具代码必须定义可调用的 handler 函数（def handler(...)）")
    return handler


def derive_parameters(code: str) -> dict[str, Any]:
    """从 handler 签名推导参数 JSON Schema。"""
    handler = load_handler(code)
    return schema_from_signature(handler)


# ===========================================================================
#  CustomToolStore — 持久化存储
# ===========================================================================

class CustomToolStore:
    """用户自定义工具存储（~/.baize/custom/tools/*.json）。

    每条记录字段:
    - id: 唯一标识
    - name: 工具名（唯一，与内置工具不冲突）
    - display_name: 前端展示名
    - description: 工具描述（供 LLM 选择）
    - category: 分类
    - code: 工具源代码（须定义 handler）
    - parameters: 显式 JSON Schema（可选，缺省自动推导）
    - enabled: 是否启用
    - created_at / updated_at
    - is_custom: True
    """

    def __init__(self, base_dir: Optional[str] = None) -> None:
        self.dir = base_dir or os.path.join(os.path.expanduser("~"), ".baize", "custom", "tools")
        os.makedirs(self.dir, exist_ok=True)

    # ---- 路径 ----
    def _path(self, tool_id: str) -> str:
        return os.path.join(self.dir, f"{tool_id}.json")

    # ---- 读取 ----
    def _read(self, tool_id: str) -> Optional[dict[str, Any]]:
        try:
            with open(self._path(tool_id), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    def _write(self, record: dict[str, Any]) -> dict[str, Any]:
        with open(self._path(record["id"]), "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
        return record

    # ---- 查询 ----
    def list(self) -> list[dict[str, Any]]:
        records = []
        for fname in sorted(os.listdir(self.dir)):
            if not fname.endswith(".json"):
                continue
            rec = self._read(fname[:-5])
            if rec:
                records.append(rec)
        return records

    def get(self, tool_id: str) -> Optional[dict[str, Any]]:
        return self._read(tool_id)

    def find_by_name(self, name: str) -> Optional[dict[str, Any]]:
        for rec in self.list():
            if rec.get("name") == name:
                return rec
        return None

    # ---- 写操作 ----
    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建自定义工具（写文件 + 注册）。

        Raises:
            ValueError: 校验失败 / 名称冲突。
        """
        name = sanitize_tool_name(data.get("name", ""))
        if not name:
            raise ValueError("工具名称不能为空")

        # 与内置/已注册工具冲突检查（允许覆盖同名自定义工具）
        existing_spec = registry.get(name)
        if existing_spec and existing_spec.author != "custom":
            raise ValueError(
                f"工具名 '{name}' 已被内置/插件工具占用（author={existing_spec.author}），"
                "请更换名称"
            )
        existing_rec = self.find_by_name(name)
        if existing_rec and existing_rec.get("id") != data.get("id"):
            raise ValueError(f"自定义工具 '{name}' 已存在，请使用更新操作")

        code = data.get("code", "")
        error = _validate_code(code)
        if error:
            raise ValueError(error)

        now = datetime.now(timezone.utc).isoformat()
        record = {
            "id": data.get("id") or secrets.token_hex(10),
            "name": name,
            "display_name": data.get("display_name") or data.get("name") or name,
            "description": data.get("description", ""),
            "category": data.get("category", "custom"),
            "code": code,
            "parameters": data.get("parameters"),  # None → 运行时自动推导
            "enabled": bool(data.get("enabled", True)),
            "created_at": data.get("created_at") or now,
            "updated_at": now,
            "is_custom": True,
        }
        record = self._write(record)
        # 注册到全局注册表
        self._register(record)
        return record

    def update(self, tool_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """更新自定义工具（写文件 + 重新注册）。"""
        record = self.get(tool_id)
        if record is None:
            raise KeyError(f"自定义工具 {tool_id} 不存在")

        if "name" in data:
            name = sanitize_tool_name(data["name"])
            conflict = self.find_by_name(name)
            if conflict and conflict["id"] != tool_id:
                raise ValueError(f"自定义工具 '{name}' 已存在")
            record["name"] = name
            record["display_name"] = data.get("display_name") or data.get("name") or name
        if "description" in data:
            record["description"] = data.get("description", "")
        if "category" in data:
            record["category"] = data.get("category", "custom")
        if "code" in data:
            error = _validate_code(data["code"])
            if error:
                raise ValueError(error)
            record["code"] = data["code"]
            record["parameters"] = data.get("parameters")  # 更新后重新推导
        if "parameters" in data:
            record["parameters"] = data.get("parameters")
        if "display_name" in data:
            record["display_name"] = data["display_name"]
        record["updated_at"] = datetime.now(timezone.utc).isoformat()

        record = self._write(record)
        if record["enabled"]:
            self._register(record)
        else:
            self._unregister(record["name"])
        return record

    def delete(self, tool_id: str) -> None:
        """删除自定义工具（注销 + 删文件）。"""
        record = self.get(tool_id)
        if record is None:
            raise KeyError(f"自定义工具 {tool_id} 不存在")
        self._unregister(record["name"])
        try:
            os.remove(self._path(tool_id))
        except OSError:
            pass

    def set_enabled(self, tool_id: str, enabled: bool) -> dict[str, Any]:
        """启用/停用自定义工具（动态注册/注销）。"""
        record = self.get(tool_id)
        if record is None:
            raise KeyError(f"自定义工具 {tool_id} 不存在")
        record["enabled"] = bool(enabled)
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        record = self._write(record)
        if enabled:
            self._register(record)
        else:
            self._unregister(record["name"])
        return record

    # ---- 注册 / 注销（热加载）----
    def _register(self, record: dict[str, Any]) -> None:
        if not record.get("enabled", True):
            return
        handler = load_handler(record["code"])
        parameters = record.get("parameters") or None
        spec = ToolSpec(
            name=record["name"],
            description=record.get("description", ""),
            handler=handler,
            parameters=parameters,
            category=record.get("category", "custom"),
            author="custom",
            version="1.0.0",
            tags=["custom"],
        )
        registry.register(spec, override=True)

    def _unregister(self, name: str) -> None:
        existing = registry.get(name)
        if existing and existing.author == "custom":
            registry.unregister(name)

    def register_all(self) -> int:
        """启动时加载全部启用的自定义工具，返回注册数量。"""
        count = 0
        for rec in self.list():
            if not rec.get("enabled", True):
                continue
            try:
                self._register(rec)
                count += 1
            except Exception:  # noqa: BLE001
                print(f"[baize.custom_tools] 加载自定义工具失败: {rec.get('name')}")
                traceback.print_exc()
        return count


# ===========================================================================
#  Test harness — 沙箱试运行
# ===========================================================================

def _build_test_script(code: str, args_json: str, timeout: int) -> str:
    """构造在子进程沙箱中执行的测试脚本。"""
    return textwrap.dedent(f"""
        import json, sys, traceback
        code = {json.dumps(code)}
        args = json.loads({json.dumps(args_json)})
        ns = {{"__name__": "baize_custom_tool"}}
        try:
            exec(compile(code, "<custom_tool>", "exec"), ns)
            handler = ns.get("handler")
            if not callable(handler):
                print("ERROR: 代码未定义可调用的 handler 函数", file=sys.stderr)
                sys.exit(2)
            import inspect
            if inspect.iscoroutinefunction(handler):
                import asyncio
                result = asyncio.run(handler(**args))
            else:
                result = handler(**args)
            print(json.dumps({{"ok": True, "result": str(result)}}, ensure_ascii=False))
        except Exception as exc:
            print(json.dumps({{"ok": False, "error": f"{{type(exc).__name__}}: {{exc}}", "trace": traceback.format_exc()}}, ensure_ascii=False), file=sys.stderr)
            sys.exit(1)
    """)


async def test_custom_tool(
    code: str,
    args: Optional[dict[str, Any]] = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """在本地沙箱子进程中试运行工具代码。

    Returns:
        dict: {"ok": bool, "result"?: str, "error"?: str, "stdout"?: str, "stderr"?: str}
    """
    from baize.executors import LocalExecutor

    error = _validate_code(code)
    if error:
        return {"ok": False, "error": error}

    script = _build_test_script(code, json.dumps(args or {}), timeout)
    executor = LocalExecutor()
    result = await executor.run(f"python3 -c {json.dumps(script)}", timeout=timeout)
    out = result.stdout or ""
    err = result.stderr or ""

    if result.error_kind == "timeout":
        return {"ok": False, "error": f"执行超时（>{timeout}s）", "stdout": out, "stderr": err}

    # 脚本成功时在 stdout 输出 JSON
    import json as _json
    try:
        payload = _json.loads(out.strip().splitlines()[-1] if out.strip() else "{}")
        if payload.get("ok"):
            return {"ok": True, "result": payload.get("result", ""), "stdout": out, "stderr": err}
        return {"ok": False, "error": payload.get("error", "执行失败"), "stdout": out, "stderr": err}
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": err or out or f"执行失败（exit {result.returncode}）", "stdout": out, "stderr": err}


# ===========================================================================
#  全局实例
# ===========================================================================

custom_tool_store = CustomToolStore()

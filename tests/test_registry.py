"""Tool 注册表与协议测试。"""

from __future__ import annotations

from typing import Optional

import pytest

from baize.tools.registry import ToolRegistry, ToolSpec, register_tool, schema_from_signature


def test_schema_from_signature_basic() -> None:
    def fn(host: str, ports: str = "1-1000", count: int = 3, verbose: bool = False) -> str:
        return host

    schema = schema_from_signature(fn)
    props = schema["properties"]
    assert props["host"]["type"] == "string"
    assert props["ports"]["type"] == "string"
    assert props["ports"]["default"] == "1-1000"
    assert props["count"]["type"] == "integer"
    assert props["verbose"]["type"] == "boolean"
    assert schema["required"] == ["host"]  # 带默认值的不必填


def test_schema_from_signature_optional() -> None:
    def fn(url: str, header: Optional[str] = None) -> str:
        return url

    schema = schema_from_signature(fn)
    assert schema["properties"]["header"]["type"] == "string"
    assert "header" not in schema["required"]


def test_register_and_query() -> None:
    reg = ToolRegistry()
    spec = ToolSpec(name="t1", description="d", handler=lambda a: a)
    reg.register(spec)
    assert reg.get("t1") is spec
    assert "t1" in reg.names()
    assert reg.by_category("general") == [spec]
    assert reg.categories() == ["general"]


def test_register_duplicate_raises() -> None:
    reg = ToolRegistry()
    reg.register(ToolSpec(name="dup", description="a", handler=lambda: "a"))
    with pytest.raises(ValueError, match="已注册"):
        reg.register(ToolSpec(name="dup", description="b", handler=lambda: "b"))
    reg.register(ToolSpec(name="dup", description="c", handler=lambda: "c"), override=True)
    assert reg.get("dup").description == "c"


def test_unregister() -> None:
    reg = ToolRegistry()
    reg.register(ToolSpec(name="x", description="x", handler=lambda: "x"))
    reg.unregister("x")
    assert reg.get("x") is None


def test_register_tool_decorator_derives_schema() -> None:
    from baize.tools.registry import registry as global_registry

    @register_tool(name="test_decorator_tool", description="测试工具", category="security", override=True)
    async def my_tool(target: str, timeout: int = 60) -> str:
        return target

    spec = global_registry.get("test_decorator_tool")
    assert spec is not None
    schema = spec.to_schema()
    func = schema["function"]
    assert func["name"] == "test_decorator_tool"
    params = func["parameters"]
    assert params["properties"]["target"]["type"] == "string"
    assert params["properties"]["timeout"]["type"] == "integer"
    assert params["required"] == ["target"]
    global_registry.unregister("test_decorator_tool")


def test_agent_tool_conversion() -> None:
    reg = ToolRegistry()
    spec = ToolSpec(name="conv", description="d", handler=lambda x: x)
    agent_tool = spec.to_agent_tool()
    assert agent_tool.name == "conv"
    schema = agent_tool.to_schema()
    assert schema["function"]["name"] == "conv"


def test_global_registry_has_security_tools() -> None:
    """全局注册表应包含核心与扩展安全工具。"""
    from baize.tools.registry import registry

    for name in (
        "nmap_scan",
        "whois_lookup",
        "dns_lookup",
        "cve_lookup",
        "browser_fetch",
        "browser_screenshot",
    ):
        assert registry.get(name) is not None, f"缺少工具 {name}"

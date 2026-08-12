"""
YAML 流水线加载器 — 将人可读的 YAML 配置转换为 Pipeline 执行引擎的 dict 格式。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml


def load_pipeline_from_yaml(yaml_path: str) -> dict:
    """从 YAML 文件加载流水线定义。"""
    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_pipeline_from_string(yaml_string: str) -> dict:
    """从 YAML 字符串加载流水线定义。"""
    return yaml.safe_load(yaml_string)


def load_all_builtin_templates() -> list[dict]:
    """加载 templates/ 目录下所有 YAML 模板。"""
    templates_dir = Path(__file__).parent.parent.parent.parent.parent / "templates"
    results = []
    if templates_dir.is_dir():
        for yml_file in sorted(templates_dir.glob("*.yml")):
            try:
                results.append(load_pipeline_from_yaml(str(yml_file)))
            except Exception:
                pass
    return results

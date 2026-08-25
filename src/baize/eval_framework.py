"""评估框架（参考 Apache Maka 的 eval/benchmark 系统）。

提供声明式实验定义与可复现的 benchmark 执行：
- 实验规格：任务集 × 智能体 × 重复次数 → 结果矩阵
- 评分维度：任务完成度、工具调用效率、时间效率、成功率
- 结果持久化：JSON 落盘，支持增量运行与历史对比

用法::

    from baize.eval_framework import EvalSpec, EvalRunner

    spec = EvalSpec(
        name="web_pentest_baseline",
        tasks=[{"input": "扫描 example.com", "expected_keywords": ["端口", "服务"]}],
        agents=["web_pentester"],
        repeats=3,
    )
    runner = EvalRunner()
    results = await runner.run(spec)
    report = runner.generate_report(results)
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


# ---- 数据模型 ----------------------------------------------------------------

@dataclass
class EvalTask:
    """单个评估任务。"""
    input: str
    id: str = ""
    expected_keywords: list[str] = field(default_factory=list)
    expected_tools: list[str] = field(default_factory=list)
    max_turns: int = 10
    tags: list[str] = field(default_factory=list)


@dataclass
class EvalSpec:
    """实验规格。"""
    name: str
    tasks: list[EvalTask] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    repeats: int = 1
    description: str = ""


@dataclass
class EvalResult:
    """单次运行结果。"""
    task_id: str
    task_input: str
    agent: str
    run_index: int
    # 评分
    keyword_hit_rate: float = 0.0    # 关键词命中率
    tool_call_count: int = 0          # 工具调用次数
    duration_seconds: float = 0.0     # 总耗时
    success: bool = False             # 是否完成（非空输出）
    final_output: str = ""
    # 元数据
    error: str = ""
    tool_calls: list[dict] = field(default_factory=list)


@dataclass
class EvalReport:
    """实验报告。"""
    spec_name: str
    total_runs: int
    completed_runs: int
    # 聚合指标
    avg_success_rate: float
    avg_keyword_hit_rate: float
    avg_tool_calls: float
    avg_duration: float
    # 按 agent 分组
    by_agent: dict[str, dict] = field(default_factory=dict)
    # 按 task 分组
    by_task: dict[str, dict] = field(default_factory=dict)
    # 原始结果
    results: list[EvalResult] = field(default_factory=list)


# ---- 评分函数 ----------------------------------------------------------------

def _score_keyword_hit(output: str, keywords: list[str]) -> float:
    if not keywords:
        return 1.0
    hits = sum(1 for kw in keywords if kw.lower() in output.lower())
    return hits / len(keywords)


def _score_tool_efficiency(tool_count: int, min_expected: int) -> float:
    if min_expected <= 0:
        return 1.0
    if tool_count <= 0:
        return 0.0
    return min(1.0, min_expected / max(tool_count, 1))


# ---- 运行器 ------------------------------------------------------------------

class EvalRunner:
    """评估运行器。

    用法::

        runner = EvalRunner(agent_loader=get_agent)
        results = await runner.run(spec)
    """

    def __init__(self, agent_loader=None, data_dir: Optional[Path] = None):
        self._agent_loader = agent_loader
        if data_dir is None:
            import os as _os
            data_dir = Path(_os.environ.get("BAIZE_DATA_DIR", str(Path.home() / ".baize"))) / "evals"
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

    async def run(self, spec: EvalSpec) -> list[EvalResult]:
        results: list[EvalResult] = []
        for task in spec.tasks:
            for agent_name in spec.agents:
                for i in range(spec.repeats):
                    result = await self._run_one(task, agent_name, i)
                    results.append(result)
        self._save_results(spec.name, results)
        return results

    async def _run_one(self, task: EvalTask, agent_name: str, run_index: int) -> EvalResult:
        result = EvalResult(
            task_id=str(uuid.uuid4())[:8],
            task_input=task.input,
            agent=agent_name,
            run_index=run_index,
        )
        started = time.time()

        try:
            agent = None
            if self._agent_loader:
                agent = self._agent_loader(agent_name)
            if agent is None:
                result.error = f"Agent not found: {agent_name}"
                result.duration_seconds = time.time() - started
                return result

            # 运行 agent
            if task.max_turns:
                agent.max_tool_calls = task.max_turns
            run_result = await agent.run(task.input)
            output = run_result.final_output or ""
            result.final_output = output
            result.success = bool(output.strip())
            result.tool_call_count = getattr(run_result, "tool_call_count", 0)
            result.tool_calls = getattr(run_result, "tool_calls", [])

            # 评分
            result.keyword_hit_rate = _score_keyword_hit(output, task.expected_keywords)

        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            result.success = False

        result.duration_seconds = round(time.time() - started, 2)
        return result

    def generate_report(self, results: list[EvalResult], spec_name: str = "") -> EvalReport:
        if not results:
            return EvalReport(spec_name=spec_name, total_runs=0, completed_runs=0,
                            avg_success_rate=0, avg_keyword_hit_rate=0, avg_tool_calls=0, avg_duration=0)

        completed = [r for r in results if not r.error]
        completed_count = len(completed)

        report = EvalReport(
            spec_name=spec_name,
            total_runs=len(results),
            completed_runs=completed_count,
            avg_success_rate=sum(1 for r in results if r.success) / max(len(results), 1),
            avg_keyword_hit_rate=sum(r.keyword_hit_rate for r in results) / max(len(results), 1),
            avg_tool_calls=sum(r.tool_call_count for r in results) / max(len(results), 1),
            avg_duration=sum(r.duration_seconds for r in results) / max(len(results), 1),
            results=results,
        )

        # 按 agent 分组
        agent_groups: dict[str, list[EvalResult]] = {}
        for r in results:
            agent_groups.setdefault(r.agent, []).append(r)
        for agent, group in agent_groups.items():
            report.by_agent[agent] = {
                "runs": len(group),
                "success_rate": sum(1 for r in group if r.success) / max(len(group), 1),
                "avg_keyword_hit": sum(r.keyword_hit_rate for r in group) / max(len(group), 1),
                "avg_tool_calls": sum(r.tool_call_count for r in group) / max(len(group), 1),
                "avg_duration": sum(r.duration_seconds for r in group) / max(len(group), 1),
            }

        # 按 task 分组
        task_groups: dict[str, list[EvalResult]] = {}
        for r in results:
            key = r.task_input[:60]
            task_groups.setdefault(key, []).append(r)
        for task_key, group in task_groups.items():
            report.by_task[task_key] = {
                "runs": len(group),
                "success_rate": sum(1 for r in group if r.success) / max(len(group), 1),
                "avg_keyword_hit": sum(r.keyword_hit_rate for r in group) / max(len(group), 1),
            }

        return report

    def _save_results(self, spec_name: str, results: list[EvalResult]) -> None:
        path = self._data_dir / f"{spec_name}_{time.strftime('%Y%m%d_%H%M%S')}.json"
        data = [asdict(r) for r in results]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_results(self, spec_name: str = "", latest: bool = True) -> list[EvalResult]:
        if latest:
            files = sorted(self._data_dir.glob(f"{spec_name}_*.json"), reverse=True)
            if not files:
                return []
            path = files[0]
        else:
            path = self._data_dir / f"{spec_name}.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [EvalResult(**d) for d in data if isinstance(d, dict)]

    def compare(self, spec_name_a: str, spec_name_b: str) -> dict:
        """对比两次实验的结果。"""
        results_a = self.load_results(spec_name_a)
        results_b = self.load_results(spec_name_b)
        report_a = self.generate_report(results_a, spec_name_a)
        report_b = self.generate_report(results_b, spec_name_b)
        return {
            "a": {"name": spec_name_a, "success_rate": report_a.avg_success_rate, "keyword_hit": report_a.avg_keyword_hit_rate},
            "b": {"name": spec_name_b, "success_rate": report_b.avg_success_rate, "keyword_hit": report_b.avg_keyword_hit_rate},
            "delta_success": report_b.avg_success_rate - report_a.avg_success_rate,
            "delta_keyword_hit": report_b.avg_keyword_hit_rate - report_a.avg_keyword_hit_rate,
        }
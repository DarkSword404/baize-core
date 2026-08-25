"""闭环学习 Skill 系统（参考 Hermes Agent 的 closed learning loop）。

在现有经验系统基础上，增加 Skill 的概念：
- Skill 是可执行的、结构化的知识单元（不只是文本经验）。
- Agent 在任务完成后可自动创建 Skill。
- Skill 在使用中自我改进：每次命中后评估效果，更新质量评分。
- Skill 附带触发条件（when-to-use），使检索更精准。

与 Experience 的区别：
- Experience：文本经验，用于检索注入增强 prompt。
- Skill：结构化知识，含触发条件、使用步骤、效果评分，可自我改进。
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("baize.skill")

# ---- 数据模型 ----------------------------------------------------------------

@dataclass
class Skill:
    """一个可学习的 Skill 条目。"""

    id: str
    title: str
    description: str = ""
    # 结构化知识
    when_to_use: list[str] = field(default_factory=list)    # 触发条件关键词
    steps: list[str] = field(default_factory=list)           # 执行步骤
    prerequisites: list[str] = field(default_factory=list)   # 前置条件
    caveats: list[str] = field(default_factory=list)         # 注意事项/踩坑
    # 关联
    tags: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)      # 涉及的工具
    source_agent: str = ""
    source_session_id: str = ""
    # 质量评分（自我改进核心）
    quality_score: float = 0.5                               # 0.0-1.0
    use_count: int = 0                                        # 被使用次数
    success_count: int = 0                                    # 使用后任务成功次数
    last_used_at: str = ""
    last_improved_at: str = ""
    # 元数据
    created_at: str = ""
    updated_at: str = ""
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Skill":
        data = dict(data)
        data.setdefault("id", f"skill_{uuid.uuid4().hex[:12]}")
        return cls(**data)


# ---- 存储层 ------------------------------------------------------------------

class SkillStore:
    """Skill 持久化存储（JSON 文件 + 内存缓存）。"""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        import os as _os
        if base_dir is None:
            data_dir = _os.environ.get("BAIZE_DATA_DIR", str(Path.home() / ".baize"))
            base_dir = Path(data_dir) / "skills"
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._cache: dict[str, Skill] = {}
        self._loaded = False

    def _path(self) -> Path:
        return self._base_dir / "skills.json"

    def _load(self) -> None:
        if self._loaded:
            return
        path = self._path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            skill = Skill.from_dict(item)
                            self._cache[skill.id] = skill
                elif isinstance(data, dict):
                    for item in data.values():
                        if isinstance(item, dict):
                            skill = Skill.from_dict(item)
                            self._cache[skill.id] = skill
            except (json.JSONDecodeError, OSError):
                pass
        self._loaded = True

    def _save(self) -> None:
        path = self._path()
        data = [s.to_dict() for s in self._cache.values()]
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def list_all(self, include_disabled: bool = False) -> list[Skill]:
        with self._lock:
            self._load()
            skills = list(self._cache.values())
            if not include_disabled:
                skills = [s for s in skills if s.enabled]
            skills.sort(key=lambda s: (s.quality_score, s.use_count), reverse=True)
            return skills

    def get(self, skill_id: str) -> Optional[Skill]:
        with self._lock:
            self._load()
            return self._cache.get(skill_id)

    def find_by_tags(self, tags: list[str]) -> list[Skill]:
        with self._lock:
            self._load()
            result = []
            for skill in self._cache.values():
                if not skill.enabled:
                    continue
                if any(t in skill.tags for t in tags):
                    result.append(skill)
            result.sort(key=lambda s: (s.quality_score, s.use_count), reverse=True)
            return result

    def find_by_triggers(self, triggers: list[str]) -> list[Skill]:
        """根据触发条件关键词匹配 Skill。"""
        with self._lock:
            self._load()
            result = []
            for skill in self._cache.values():
                if not skill.enabled:
                    continue
                score = 0
                for trigger in triggers:
                    trigger_lower = trigger.lower()
                    for condition in skill.when_to_use:
                        if trigger_lower in condition.lower():
                            score += 1
                            break
                    for tag in skill.tags:
                        if trigger_lower in tag.lower():
                            score += 0.5
                            break
                if score > 0:
                    result.append((score, skill))
            result.sort(key=lambda x: (x[0], x[1].quality_score), reverse=True)
            return [s for _, s in result]

    def create(self, skill: Skill) -> Skill:
        with self._lock:
            self._load()
            if not skill.id:
                skill.id = f"skill_{uuid.uuid4().hex[:12]}"
            if not skill.created_at:
                skill.created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            skill.updated_at = skill.created_at
            self._cache[skill.id] = skill
            self._save()
        return skill

    def update(self, skill_id: str, patch: dict) -> Optional[Skill]:
        with self._lock:
            self._load()
            skill = self._cache.get(skill_id)
            if not skill:
                return None
            for k, v in patch.items():
                if hasattr(skill, k):
                    setattr(skill, k, v)
            skill.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._save()
            return skill

    def delete(self, skill_id: str) -> bool:
        with self._lock:
            self._load()
            if skill_id in self._cache:
                del self._cache[skill_id]
                self._save()
                return True
            return False


# ---- Skill 学习器 ------------------------------------------------------------

class SkillLearner:
    """Skill 闭环学习器。

    核心循环（参考 Hermes Agent）：
    1. 任务完成后 → 从经验中创建 Skill 候选。
    2. 检索时 → 匹配触发条件，注入相关 Skill 到 prompt。
    3. 使用后 → 评估效果，更新 quality_score。
    4. 质量过低 → 自动禁用或降级。
    """

    def __init__(self, store: Optional[SkillStore] = None) -> None:
        self.store = store or SkillStore()

    async def learn_from_experience(
        self,
        client,
        agent_key: str,
        session_id: str,
        user_message: str,
        final_output: str,
        tool_events: list[dict],
    ) -> Optional[Skill]:
        """从一次成功任务中学习 Skill。"""
        # 简化的信号检测：有工具调用且有产出 → 值得学习
        if not tool_events or not final_output.strip():
            return None

        try:
            from baize.sdk.client import ChatMessage

            material = (
                f"用户需求：{user_message[:500]}\n"
                f"工具调用：{json.dumps(tool_events[-5:], ensure_ascii=False)[:1500]}\n"
                f"最终输出：{final_output[:1000]}"
            )

            prompt = (
                "你是一名资深安全专家。请从以下任务执行过程中提取可复用的 Skill。\n"
                "输出 JSON：\n"
                "{\n"
                '  "title": "技能名称",\n'
                '  "description": "简短描述",\n'
                '  "when_to_use": ["触发条件1", "触发条件2"],\n'
                '  "steps": ["步骤1", "步骤2", "步骤3"],\n'
                '  "prerequisites": ["前置条件"],\n'
                '  "caveats": ["注意事项"],\n'
                '  "tags": ["tag1", "tag2"]\n'
                "}\n\n"
                f"任务执行过程：\n{material}"
            )

            result = await client.complete(
                [ChatMessage(role="user", content=prompt)],
                tools=None,
            )
            text = result.content or ""

            import re as _re
            match = _re.search(r"\{[\s\S]*\}", text)
            if not match:
                return None

            data = json.loads(match.group(0))
            skill = Skill(
                id=f"skill_{uuid.uuid4().hex[:12]}",
                title=data.get("title", "未命名技能"),
                description=data.get("description", ""),
                when_to_use=data.get("when_to_use", []),
                steps=data.get("steps", []),
                prerequisites=data.get("prerequisites", []),
                caveats=data.get("caveats", []),
                tags=data.get("tags", []),
                tools_used=[e.get("name", "") for e in tool_events],
                source_agent=agent_key,
                source_session_id=session_id,
                quality_score=0.5,
                created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            return self.store.create(skill)

        except Exception as exc:
            logger.warning("Skill 学习失败: %s", exc)
            return None

    def get_applicable_skills(self, task_description: str, tags: list[str] | None = None) -> list[Skill]:
        """获取适用于当前任务的 Skill 列表（用于 prompt 注入）。"""
        triggers = task_description.lower().split()
        if tags:
            triggers.extend(tags)
        return self.store.find_by_triggers(triggers)[:5]

    def format_skills_for_prompt(self, skills: list[Skill]) -> str:
        """将 Skill 列表格式化为 prompt 注入文本。"""
        if not skills:
            return ""
        lines = ["\n## 可复用技能 (Skills)", ""]
        for i, s in enumerate(skills, 1):
            lines.append(f"### {i}. {s.title} (评分: {s.quality_score:.0%})")
            if s.when_to_use:
                lines.append(f"- 触发条件: {'; '.join(s.when_to_use[:3])}")
            if s.steps:
                steps = "\n".join(f"  {j}. {step}" for j, step in enumerate(s.steps[:5], 1))
                lines.append(f"- 步骤:\n{steps}")
            if s.caveats:
                lines.append(f"- 注意: {'; '.join(s.caveats[:3])}")
            lines.append("")
        return "\n".join(lines)

    def record_usage(self, skill_id: str, success: bool) -> None:
        """记录 Skill 使用效果，更新质量评分。

        使用指数加权移动平均更新评分：
        quality = 0.8 * old_quality + 0.2 * (1 if success else 0)
        """
        skill = self.store.get(skill_id)
        if not skill:
            return

        skill.use_count += 1
        if success:
            skill.success_count += 1

        # 指数加权更新
        alpha = 0.2
        new_score = (1 - alpha) * skill.quality_score + alpha * (1.0 if success else 0.0)
        skill.quality_score = round(new_score, 3)
        skill.last_used_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # 自动降级：质量低于 0.3 且使用次数 >= 5 → 禁用
        if skill.quality_score < 0.3 and skill.use_count >= 5:
            skill.enabled = False
            logger.info("Skill 自动降级: %s (评分 %.2f, 使用 %d 次)", skill.title, skill.quality_score, skill.use_count)

        self.store.update(skill_id, {
            "use_count": skill.use_count,
            "success_count": skill.success_count,
            "quality_score": skill.quality_score,
            "last_used_at": skill.last_used_at,
            "enabled": skill.enabled,
        })
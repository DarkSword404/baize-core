"""
白泽·智脑智能体发现工具 — 供 Selection Agent 和 Orchestration Agent 使用

提供三个核心能力:
1. ``check_available_agents`` — 扫描已注册智能体，返回完整目录
2. ``analyze_task_requirements`` — 分析用户任务描述，提取需求特征
3. ``get_agent_number`` — 按名称查找智能体索引

被 ``baize.agents.selection_agent`` 和 ``baize.agents.orchestration_agent`` 引用。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any


# ---------------------------------------------------------------------------
# 智能体目录
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _check_available_agents() -> dict[str, Any]:
    """
    扫描所有已注册的白泽·智脑智能体，返回完整信息目录。

    缓存行为: ``lru_cache(maxsize=1)`` — 智能体目录在会话期内不变，
    避免每次 LLM 工具调用都重新遍历模块（orchestrator 路由等高频场景）。

    Returns:
        Dict 包含:
        - total_agents: int
        - agents: Dict[agent_key, agent_info]
        - indexed_agents: Dict[int, agent_entry]
        - agent_list: List[str]
        - categories: Dict[spec, List[agent_key]]
    """
    from baize.agents import _AGENTS

    agents_info: dict[str, dict] = {}
    for key, agent in _AGENTS.items():
        name = agent.name
        desc = agent.description or ""
        tools = agent.tools or []
        tool_list = []
        for t in tools:
            t_name = getattr(t, "name", str(t))
            # AgentTool 有 name/description/func
            tool_list.append({
                "name": t_name,
                "description": getattr(t, "description", ""),
            })

        agents_info[key] = {
            "name": name,
            "description": desc,
            "module": f"baize.agents.{key}",
            "variable_name": key,
            "tools": tool_list,
            "capabilities": _infer_capabilities(name, desc, tool_list),
            "specialization": _extract_specialization(name, desc),
            "use_cases": _extract_use_cases(desc),
        }

    agent_list = list(agents_info.keys())
    indexed_agents = {}
    for i, agent_key in enumerate(agent_list, 1):
        indexed_agents[i] = {
            "key": agent_key,
            "info": agents_info[agent_key],
        }

    return {
        "total_agents": len(agents_info),
        "agents": agents_info,
        "indexed_agents": indexed_agents,
        "agent_list": agent_list,
        "categories": _categorize_agents(agents_info),
    }


# ---------------------------------------------------------------------------
# 任务需求分析
# ---------------------------------------------------------------------------

_TASK_KEYWORDS: dict[str, list[str]] = {
    "penetration_testing": [
        "pentest", "penetration test", "security assessment", "vulnerability assessment",
        "exploit", "attack", "breach", "hack", "infiltration", "red team",
        "渗透测试", "漏洞利用", "漏洞评估", "攻击", "红队", "渗透",
    ],
    "bug_bounty": [
        "bug bounty", "vulnerability discovery", "web security", "api testing",
        "responsible disclosure", "security bug", "vulnerability hunting",
        "漏洞悬赏", "漏洞发现", "安全漏洞", "众测",
    ],
    "blue_team": [
        "defense", "defensive", "blue team", "monitoring", "detection",
        "incident response", "security monitoring", "threat hunting", "soc",
        "防御", "蓝队", "监控", "检测", "威胁狩猎", "安全运营",
    ],
    "forensics": [
        "forensics", "dfir", "incident response", "digital forensics",
        "investigation", "evidence", "malware analysis", "breach investigation",
        "取证", "数字取证", "应急响应", "调查", "证据",
    ],
    "reverse_engineering": [
        "reverse engineering", "binary analysis", "firmware analysis",
        "disassembly", "decompilation", "malware analysis", "code analysis",
        "逆向", "二进制分析", "固件分析", "反汇编", "反编译",
    ],
    "network_security": [
        "network", "traffic analysis", "packet capture", "network monitoring",
        "protocol analysis", "wireshark", "tcpdump", "network forensics",
        "网络", "流量分析", "数据包", "协议分析",
    ],
    "wireless_security": [
        "wifi", "wireless", "bluetooth", "radio", "rf", "802.11",
        "wireless security", "wifi hacking", "wireless penetration",
        "无线", "蓝牙", "射频",
    ],
    "subghz": [
        "subghz", "sub-ghz", "sub ghz", "sdr", "software defined radio",
        "无线电", "信号分析",
    ],
    "memory_analysis": [
        "memory analysis", "memory forensics", "process analysis",
        "runtime analysis", "memory dump", "heap analysis",
        "内存分析", "内存取证", "进程分析",
    ],
    "ctf": [
        "ctf", "capture the flag", "challenge", "flag", "competition",
        "security challenge", "hacking challenge",
        "夺旗", "挑战赛",
    ],
    "reporting": [
        "report", "documentation", "summary", "findings", "analysis report",
        "security report", "executive summary",
        "报告", "文档", "总结", "输出",
    ],
    "compliance": [
        "compliance", "audit", "regulation", "gdpr", "hipaa", "iso",
        "standard", "pci", "soc2",
        "合规", "审计", "标准",
    ],
    "android": [
        "android", "apk", "mobile app", "mobile application",
        "安卓", "移动应用", "手机",
    ],
}


def analyze_task_requirements(task_description: str) -> dict[str, Any]:
    """分析用户任务描述，提取关键需求和特征。

    Args:
        task_description: 用户的任务描述。

    Returns:
        分析结果字典。
    """
    task_lower = task_description.lower()

    detected_categories: list[str] = []
    confidence_scores: dict[str, float] = {}

    for category, keywords in _TASK_KEYWORDS.items():
        matches = sum(1 for kw in keywords if kw in task_lower)
        if matches > 0:
            detected_categories.append(category)
            confidence_scores[category] = matches / max(len(keywords), 1)

    # 复杂度评估
    complexity_indicators = {
        "simple": ["simple", "basic", "quick", "fast", "easy", "简单", "快速", "基础"],
        "medium": ["comprehensive", "detailed", "thorough", "complete", "详细", "全面"],
        "complex": ["advanced", "deep", "extensive", "sophisticated", "complex", "高级", "深度", "复杂"],
    }

    complexity = "medium"
    for level, indicators in complexity_indicators.items():
        if any(ind in task_lower for ind in indicators):
            complexity = level
            break

    # 是否需要多智能体
    multi_agent_indicators = [
        "comprehensive", "full", "complete", "end-to-end", "multiple",
        "both", "all", "various", "different perspectives",
        "同时", "多个", "全部", "并行", "多角度",
    ]
    needs_multiple = any(ind in task_lower for ind in multi_agent_indicators)

    primary = (
        max(confidence_scores.items(), key=lambda x: x[1])[0]
        if confidence_scores
        else "general"
    )

    return {
        "task_description": task_description,
        "detected_categories": detected_categories,
        "confidence_scores": confidence_scores,
        "complexity": complexity,
        "needs_multiple_agents": needs_multiple,
        "primary_category": primary,
        "recommendations": _generate_recommendations(detected_categories, complexity, needs_multiple),
    }


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _infer_capabilities(name: str, description: str, tools: list[dict]) -> list[str]:
    """从智能体名称/描述/工具推断能力标签。"""
    caps: list[str] = []
    text = (f"{name} {description}").lower()
    mapping = {
        "penetration_testing": ["渗透测试", "漏洞利用", "攻击"],
        "defensive_security": ["防御", "监控", "检测", "蓝队"],
        "network_analysis": ["网络", "流量", "数据包"],
        "web_security": ["web", "网站", "http"],
        "reverse_engineering": ["逆向", "二进制", "反汇编"],
        "forensics": ["取证", "调查", "evidence"],
        "wireless": ["wifi", "无线", "蓝牙", "subghz"],
        "memory_analysis": ["内存", "进程"],
        "reporting": ["报告", "文档", "report"],
        "ctf": ["ctf", "挑战"],
        "compliance": ["合规", "审计", "标准"],
    }
    for cap, keywords in mapping.items():
        if any(kw in text for kw in keywords):
            caps.append(cap)
    if tools:
        caps.append("工具执行")
    return caps


def _extract_specialization(name: str, description: str) -> str:
    specializations = {
        "red team": ["red team", "penetration", "exploit", "attack", "红队", "渗透"],
        "blue team": ["blue team", "defense", "monitoring", "protection", "蓝队", "防御"],
        "bug bounty": ["bug bounty", "vulnerability discovery", "web security", "漏洞悬赏"],
        "forensics": ["forensics", "dfir", "investigation", "incident response", "取证"],
        "reverse engineering": ["reverse engineering", "binary analysis", "firmware", "逆向"],
        "network security": ["network", "traffic", "protocol", "packet", "网络"],
        "wireless": ["wifi", "wireless", "radio", "rf", "无线", "射频"],
        "memory analysis": ["memory", "process", "runtime", "内存"],
        "reporting": ["report", "documentation", "summary", "报告"],
        "ctf": ["ctf", "challenge", "flag", "夺旗"],
        "compliance": ["compliance", "audit", "合规", "审计"],
        "subghz": ["subghz", "sdr", "sub ghz", "无线电"],
        "android": ["android", "apk", "mobile", "安卓", "移动"],
        "orchestration": ["orchestrat", "selection", "编排", "选择", "builder", "元"],
        "general": ["general", "basic", "tool", "command", "通用", "基础"],
    }

    text = f"{(name or '')} {(description or '')}".lower()
    for spec, keywords in specializations.items():
        if any(kw in text for kw in keywords):
            return spec
    return "general"


def _extract_use_cases(description: str) -> list[str]:
    use_cases: list[str] = []
    desc_lower = (description or "").lower()
    patterns = {
        "Penetration Testing": ["penetration", "pentest", "security assessment"],
        "Vulnerability Assessment": ["vulnerability", "security testing", "weakness"],
        "Network Analysis": ["network", "traffic", "protocol"],
        "Web Security": ["web", "api", "application"],
        "System Analysis": ["system", "host", "server"],
        "Malware Analysis": ["malware", "binary", "reverse"],
        "Incident Response": ["incident", "response", "investigation"],
        "Compliance": ["compliance", "audit", "standard"],
        "CTF Challenges": ["ctf", "challenge", "flag"],
        "Reporting": ["report", "documentation", "findings"],
        "Code Generation": ["code", "generate", "creation", "builder"],
    }
    for uc, keywords in patterns.items():
        if any(kw in desc_lower for kw in keywords):
            use_cases.append(uc)
    return use_cases


def _categorize_agents(agents_info: dict[str, Any]) -> dict[str, list[str]]:
    categories: dict[str, list[str]] = {}
    for agent_name, info in agents_info.items():
        spec = info.get("specialization", "general")
        categories.setdefault(spec, []).append(agent_name)
    return categories


def _generate_recommendations(
    categories: list[str], complexity: str, needs_multiple: bool
) -> list[str]:
    recommendations: list[str] = []
    mapping = {
        "penetration_testing": "Red Team Agent 适合综合渗透测试",
        "bug_bounty": "Bug Bounter Agent 适合漏洞发现/众测",
        "blue_team": "Blue Team Agent 适合防御分析与监测",
        "forensics": "DFIR Agent 适合数字取证与应急响应",
        "network_security": "Network Analyzer Agent 适合网络流量分析",
        "wireless_security": "WiFi Security Agent 适合无线安全测试",
        "subghz": "Sub-GHz Agent 适合无线电/SDR 分析",
        "reverse_engineering": "Reverse Engineering Agent 适合二进制/固件逆向",
        "memory_analysis": "Memory Analysis Agent 适合内存运行分析",
        "reporting": "Reporting Agent 适合生成安全报告",
        "compliance": "Compliance Agent 适合合规审计",
        "android": "Android SAST Agent 适合安卓应用静态分析",
        "ctf": "CTF Agent 适合 CTF 挑战",
    }
    for cat in categories:
        if cat in mapping:
            recommendations.append(mapping[cat])

    if needs_multiple:
        recommendations.append("任务复杂，建议使用多智能体并行或编排模式")

    if complexity == "complex":
        recommendations.append("可使用 Orchestration Agent 统筹调度多个专项智能体")

    return recommendations


def _get_agent_number(agent_name: str) -> dict[str, Any]:
    """按名称查询智能体索引编号。

    Args:
        agent_name: 智能体名称/键名。

    Returns:
        包含 agent_number / found / description 的字典。
    """
    agents_data = _check_available_agents()
    indexed = agents_data.get("indexed_agents", {})
    key_lower = agent_name.strip().lower()

    for number, agent_data in indexed.items():
        if agent_data["key"].lower() == key_lower:
            info = agent_data["info"]
            return {
                "agent_number": number,
                "agent_key": agent_data["key"],
                "agent_name": info.get("name", agent_data["key"]),
                "command": f"/agent {number}",
                "found": True,
                "description": info.get("description", ""),
            }

    return {
        "found": False,
        "message": f"未找到智能体 '{agent_name}'",
        "total_agents": agents_data.get("total_agents", 0),
    }


# ---------------------------------------------------------------------------
# 工具适配器 — 供 SDK Agent 使用的纯函数
# ---------------------------------------------------------------------------

def tool_check_available_agents() -> str:
    """列出所有可用智能体及其详细信息。

    供 Selection Agent 的 tools 参数引用。

    Returns:
        JSON 格式的智能体目录字符串（便于 LLM 解析）。
    """
    import json
    from baize.agents import aliases_for

    data = _check_available_agents()
    summary = {
        "total_agents": data["total_agents"],
        "categories": data["categories"],
        "agent_list": [
            {
                "key": k,
                "name": v["name"],
                "aliases": aliases_for(v["name"]),
                "specialization": v["specialization"],
                "description": v["description"][:120],
            }
            for k, v in data["agents"].items()
        ],
        "usage_hint": "agent_type 字段可填 name（显示名）或 aliases 中任一值（注册键名），两者都会被接受。",
    }
    return json.dumps(summary, ensure_ascii=False, indent=2)


def tool_analyze_task_requirements(task_description: str) -> str:
    """分析用户任务描述并推荐智能体。

    Args:
        task_description: 用户任务描述。

    Returns:
        JSON 格式的分析结果。
    """
    import json
    result = analyze_task_requirements(task_description)
    return json.dumps(result, ensure_ascii=False, indent=2)


def tool_get_agent_number(agent_name: str) -> str:
    """查询指定智能体的索引。

    Args:
        agent_name: 智能体名称。

    Returns:
        JSON 格式的查询结果。
    """
    import json
    result = _get_agent_number(agent_name)
    return json.dumps(result, ensure_ascii=False, indent=2)


# 注册为 Baize AgentTool 可用的元数据
AGENT_DISCOVERY_TOOLS = [
    {
        "name": "check_available_agents",
        "description": (
            "列出所有已注册的白泽·智脑智能体及其能力、专长、可用工具。"
            "在以下场景使用: 用户询问有哪些智能体、需要选择合适的智能体、"
            "需要了解智能体的具体能力范围。"
        ),
        "func": tool_check_available_agents,
    },
    {
        "name": "analyze_task_requirements",
        "description": (
            "分析用户描述的任务需求，自动识别所需的安全能力类型、复杂度、"
            "是否需要多智能体协作，并给出智能体推荐建议。"
            "在以下场景使用: 用户描述任务模糊、需要帮助选择最佳智能体。"
        ),
        "func": tool_analyze_task_requirements,
    },
    {
        "name": "get_agent_number",
        "description": (
            "按名称查找指定智能体的索引编号和详细描述。"
            "在以下场景使用: 需要确认某个智能体是否存在、获取智能体编号。"
        ),
        "func": tool_get_agent_number,
    },
]

"""内置报告模板。

每个模板定义：
- id / name / description / keywords：供 agent 自动选择
- sections：章节大纲（标题 + 填写指引），指导 agent 分段填充
- skeleton()：带占位指引的 Markdown 骨架
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ReportSection:
    """报告章节定义。"""

    title: str
    guidance: str  # 给 agent 的填写指引


@dataclass
class ReportTemplate:
    """报告模板。"""

    id: str
    name: str
    description: str
    keywords: list[str] = field(default_factory=list)
    sections: list[ReportSection] = field(default_factory=list)

    def outline(self) -> str:
        """章节大纲文本（注入工具返回，指导 agent）。"""
        lines = [f"模板「{self.name}」({self.id}) 章节大纲："]
        for i, s in enumerate(self.sections, 1):
            lines.append(f"{i}. {s.title} — {s.guidance}")
        return "\n".join(lines)

    def skeleton(self, title: str = "") -> str:
        """生成带占位指引的 Markdown 骨架。"""
        lines = [f"# {title or self.name}", ""]
        for s in self.sections:
            lines.append(f"## {s.title}")
            lines.append("")
            lines.append(f"<!-- {s.guidance} -->")
            lines.append("")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "keywords": self.keywords,
            "sections": [
                {"title": s.title, "guidance": s.guidance}
                for s in self.sections
            ],
        }


# ===========================================================================
#  模板一：漏洞利用详细报告
# ===========================================================================

_VULN_EXPLOIT = ReportTemplate(
    id="vuln_exploit",
    name="漏洞利用详细报告",
    description=(
        "单个漏洞的深度利用报告：从漏洞原理、影响资产、复现步骤、完整利用链、"
        "利用成果到修复建议。适用于用户要求「XX漏洞的详细漏洞利用报告」。"
    ),
    keywords=[
        "漏洞利用", "利用报告", "漏洞详情", "vuln", "exploit",
        "复现", "利用方式", "exp", "rce", "注入",
    ],
    sections=[
        ReportSection("漏洞概述", (
            "漏洞名称、CVE/CNVD 编号（如有）、漏洞类型（CWE）、CVSS 评分与向量、"
            "危害等级、发现时间、报告人。"
        )),
        ReportSection("影响范围", (
            "受影响资产（域名/IP/端口/URL 端点）、组件与版本、"
            "前提条件（是否需要认证/网络位置/用户交互）、影响面（机密性/完整性/可用性）。"
        )),
        ReportSection("漏洞原理", (
            "根因分析：代码/组件层面为什么存在漏洞，触发链路（source→sink），"
            "涉及的类/函数/协议机制（如 fastjson autoType、JNDI lookup 流程）。"
        )),
        ReportSection("环境信息", (
            "目标环境指纹（OS/中间件/语言版本/框架版本）、攻击机环境、"
            "网络连通性（出站/入站可达性验证结果）。"
        )),
        ReportSection("复现步骤", (
            "完整可复现流程：原始 HTTP 请求报文（方法/路径/头/Body）、"
            "payload 逐字段解释、目标响应、判定成功的证据（回连日志/DNSLog/响应差异）。"
        )),
        ReportSection("利用过程", (
            "端到端利用链：每一步的尝试、成功路径与失败路径（失败路径要记录原因，"
            "如黑名单拦截/JDK 版本限制），使用的工具与命令，关键 payload 代码块，"
            "关键证据截图/报文占位。"
        )),
        ReportSection("利用成果", (
            "最终获取的权限/数据（命令执行回显、webshell/内存马路径、主机名/身份标识），"
            "可进一步横向移动的凭据/入口，敏感信息打码处理。"
        )),
        ReportSection("失败尝试与约束", (
            "已验证不可行的路径及原因（如 JdbcRowSetImpl 被黑名单拦截、"
            "trustURLCodebase 默认关闭），避免后续重复测试。"
        )),
        ReportSection("修复建议", (
            "升级方案（具体版本号）、配置缓解（safeMode/白名单）、"
            "临时防护措施（WAF 规则/网络隔离）、验证修复的方法。"
        )),
        ReportSection("时间线", (
            "关键事件时间线：发现→验证→利用成功的时间点列表。"
        )),
    ],
)


# ===========================================================================
#  模板二：渗透测试报告
# ===========================================================================

_PENTEST_REPORT = ReportTemplate(
    id="pentest_report",
    name="渗透测试报告",
    description=(
        "一次完整渗透测试项目的综合报告：授权范围、风险汇总、"
        "全部漏洞发现、攻击链路径与整改建议。适用于专项渗透/红队评估的交付报告。"
    ),
    keywords=[
        "渗透测试", "渗透报告", "安全评估", "红队", "pentest",
        "测试报告", "专项报告", "项目报告",
    ],
    sections=[
        ReportSection("项目概述", (
            "项目背景、测试性质（黑盒/灰盒/白盒）、授权范围、测试时间窗口、"
            "测试团队、保密声明。"
        )),
        ReportSection("测试范围", (
            "授权资产清单（域名/IP段/应用/接口），明确排除范围，"
            "测试账号与权限级别，测试约束（时间/手段/DoS 限制）。"
        )),
        ReportSection("风险等级汇总", (
            "漏洞统计表：按严重/高/中/低/信息分级统计数量；"
            "Markdown 表格列出：编号 | 漏洞名称 | 风险等级 | 影响资产 | 状态。"
        )),
        ReportSection("整体攻击路径", (
            "从外网入口到最终目标的完整攻击链概述（侦察→入口→利用→提权→横向），"
            "可用编号引用下方详细发现，说明每一跳的前提与产出。"
        )),
        ReportSection("详细漏洞发现", (
            "每个漏洞一个小节，固定字段：漏洞描述、影响资产、复现过程（请求/响应证据）、"
            "危害分析、整改建议。按风险等级从高到低排列，编号 V-01/V-02...。"
        )),
        ReportSection("已验证攻击链", (
            "实际打通的利用链细节：如「未授权接口→fastjson JNDI→远程类加载 RCE→"
            "内存马植入→内网可达」，含关键证据与影响范围。"
        )),
        ReportSection("未成功方向与遗留风险", (
            "已尝试但未打通的方向及原因、受工具/时间约束未覆盖的测试面、"
            "目标侧已有的防护措施。"
        )),
        ReportSection("整改建议总览", (
            "按优先级排列：紧急修复（1周内）/短期加固（1月内）/长期体系化建设；"
            "每条建议对应具体漏洞编号。"
        )),
        ReportSection("附录", (
            "测试工具清单、原始请求存档位置、参考资料、报告声明。"
        )),
    ],
)


# ===========================================================================
#  模板三：CTF Writeup
# ===========================================================================

_CTF_WRITEUP = ReportTemplate(
    id="ctf_writeup",
    name="CTF Writeup",
    description=(
        "CTF 题目的解题复盘：题目信息、分析过程、解题步骤、完整 exp、"
        "flag 与知识点总结。适用于 Web/Pwn/逆向等各类题目的 writeup。"
    ),
    keywords=[
        "ctf", "writeup", "wp", "题目", "flag", "解题",
        "复盘", "题解",
    ],
    sections=[
        ReportSection("题目信息", (
            "题目名称、分类（Web/Pwn/Reverse/Crypto/Misc）、分值、难度、"
            "题目描述、提供的附件/链接、比赛名称。"
        )),
        ReportSection("信息收集与分析", (
            "初始探测过程：端口/目录扫描、指纹识别、源码审计发现、"
            "关键响应与可疑点，记录分析思路而不只是结论。"
        )),
        ReportSection("漏洞分析", (
            "题目考点与漏洞原理：漏洞类型、触发条件、"
            "源码/二进制中的关键位置（函数名/行号/反编译片段）。"
        )),
        ReportSection("解题过程", (
            "按步骤记录利用过程：每一步的请求/命令、响应反馈、思路调整，"
            "包括走过的弯路（简要说明为什么走不通）。"
        )),
        ReportSection("EXP / Payload", (
            "完整可运行的利用代码或 payload（代码块，含注释），"
            "标注运行环境与依赖。"
        )),
        ReportSection("Flag", (
            "最终 flag 值（flag{...}）及获取时的响应证据。"
        )),
        ReportSection("知识点总结", (
            "本题涉及的技术点、通用利用模式、同类题目的识别特征、"
            "防御视角的成因与修复。"
        )),
        ReportSection("参考资料", (
            "参考的官方文档、漏洞公告、技术博客链接。"
        )),
    ],
)


_BUILTIN_TEMPLATES: list[ReportTemplate] = [
    _VULN_EXPLOIT,
    _PENTEST_REPORT,
    _CTF_WRITEUP,
]


def get_builtin_templates() -> list[ReportTemplate]:
    """返回全部内置模板。"""
    return list(_BUILTIN_TEMPLATES)


def get_template_by_id(template_id: str) -> ReportTemplate | None:
    """按 id 获取模板。"""
    for t in _BUILTIN_TEMPLATES:
        if t.id == template_id:
            return t
    return None


def pick_template(query: str) -> ReportTemplate:
    """根据用户请求文本自动选择最匹配的模板（无匹配时默认漏洞利用报告）。"""
    text = (query or "").lower()
    best: tuple[int, ReportTemplate] | None = None
    for tpl in _BUILTIN_TEMPLATES:
        score = sum(1 for kw in tpl.keywords if kw.lower() in text)
        if score > 0 and (best is None or score > best[0]):
            best = (score, tpl)
    return best[1] if best else _VULN_EXPLOIT

"""邮件安全智能体（DNS/SMTP）。

专注评估邮件配置安全：SPF/DMARC/DKIM 漏洞检测。
指令为 Baize 独立编写的中文版本。
"""

from __future__ import annotations

from baize.sdk.agent import Agent
from baize.tools import mail_tools


def _check_mail_spoofing(domain: str) -> str:
    """检查域名邮件伪造防护配置（SPF/DMARC/DKIM）。"""
    import subprocess
    result = []
    # SPF
    try:
        spf = subprocess.run(["dig", "+short", "TXT", domain], capture_output=True, text=True, timeout=15)
        txts = [l for l in spf.stdout.splitlines() if "v=spf1" in l]
        result.append(f"SPF: {', '.join(txts) if txts else '未找到 SPF 记录（存在伪造风险）'}")
    except Exception:  # noqa: BLE001
        result.append("SPF: 查询失败")
    # DMARC
    try:
        dmarc = subprocess.run(["dig", "+short", "TXT", f"_dmarc.{domain}"], capture_output=True, text=True, timeout=15)
        dm = [l for l in dmarc.stdout.splitlines() if "v=DMARC1" in l]
        result.append(f"DMARC: {', '.join(dm) if dm else '未找到 DMARC 记录'}")
    except Exception:  # noqa: BLE001
        result.append("DMARC: 查询失败")
    # DKIM 提示
    result.append("DKIM: 需要已知选择器才能验证，请提供选择器（如 default）")
    return "\n".join(result)


def _execute_cli(command: str) -> str:
    import subprocess
    try:
        proc = subprocess.run(["/bin/bash", "-c", command], capture_output=True, text=True, timeout=60)
        return ((proc.stdout or "") + (proc.stderr or "")).strip()
    except Exception as e:  # noqa: BLE001
        return f"(错误: {e})"


MAIL_INSTRUCTIONS = """\
你是白泽（Baize）的邮件安全专家，专注评估邮件服务的反伪造配置。

## 核心能力
- 检查 SPF、DMARC、DKIM 记录
- 评估邮件伪造（spoofing）风险
- 检测开放中继等配置问题
- 提出邮件安全加固建议

## 工作方法
1. 收集目标域名
2. 检查 SPF/DMARC/DKIM 记录
3. 评估伪造风险等级
4. 输出加固建议

## 合规
- 仅评估用户授权的域名
"""


dns_smtp_agent = Agent(
    name="dns_smtp_agent",
    description="邮件安全专家：SPF/DMARC/DKIM 配置评估、伪造风险检测。",
    instructions=MAIL_INSTRUCTIONS,
    tools=mail_tools(),
)

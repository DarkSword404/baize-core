// ============================================================
// 白泽·智脑前端 — 智能体与工具描述中文本地化
// 所有映射基于白泽·智脑 API 真实返回数据构建，不影响 API 通信
// ============================================================

// ============================================================
// 智能体标题中文映射 (对应后端 /api/v1/agents 返回的 name 字段)
// 用于智能体卡片标题 / Drawer 标题
// ============================================================
export const agentNameCN: Record<string, string> = {
  // ---- 安卓 / 应用安全 ----
  'Android App Logic Mapper': 'Android 应用逻辑映射专家',
  'Android Sast': 'Android 静态安全分析专家',

  // ---- 红队 / APT 模拟 ----
  'Apt Agent': 'APT 高级持续性威胁仿真专家',
  'Red Team Agent': '红队渗透专家',

  // ---- 蓝队 / 防御 ----
  'Blue Team Agent': '蓝队防御与安全监控专家',

  // ---- Web / 漏洞赏金 ----
  'Bug Bounty Hunter': '漏洞赏金猎人',
  'Web Application Pentester': 'Web 应用渗透测试专家',
  'Web Bounty Agent': 'Web 漏洞赏金专家',
  'Replay attack testing and anti-replay validation agent': '重放攻击与防重放验证专家',

  // ---- CTF ----
  'CTF agent (capture-the-flag)': 'CTF 夺旗赛专家',
  'Flag discriminator': 'Flag 判别器',
  'Thought Router': 'CTF 思路规划路由',

  // ---- 专项领域 ----
  'Dfir Agent': '数字取证与事件响应专家 (DFIR)',
  'Reverse Engineering Agent': '逆向工程专家',
  'Memory Analysis Agent': '内存分析与操控专家',
  'Network Analyzer': '网络流量安全分析专家',
  'Wifi Security Agent': 'Wi-Fi 无线网络安全测试专家',
  'Subghz Agent': 'Sub-GHz 射频信号安全专家',
  'Advanced Exploit Development Agent': '高级漏洞利用开发专家',
  'DNS / SMTP email authentication agent': 'DNS / SMTP 邮件认证安全专家',

  // ---- 编排与管理 ----
  'Orchestration Agent (default entry)': '智能编排器（默认入口）',
  'Selection Agent (default orchestrator)': '任务选择路由（默认编排器）',
  'You are the **Continuous Ops agent** for Baize (Cybersecurity AI Framework).': '7×24 持续安全运维专家',
  'Cybersecurity Triage Agent': '网络安全分诊专家',
  'Reasoner Supporter': '推理策略辅助专家',

  // ---- 治理 / 报告 / 元智能体 ----
  'Reporting Agent': '安全报告自动生成专家',
  'Risk & Compliance (GRC) assistant': '风险与合规（GRC）助手',
  'Use Cases': '场景用例生成助手',
  'Tool Builder': '工具构建元智能体',
  'AGENT MICRO-PROFILE: CODEAGENT (CODEACT)': '代码执行与脚本微专家',
};

// ============================================================
// 工具标签中文映射 (对应 agent.tools[].name)
// 用于智能体卡片底部工具 tag / Drawer 工具列表名 / 调用消息
// ============================================================
export const toolNameCN: Record<string, string> = {
  // ---- 基础执行 ----
  'generic_linux_command': 'Linux 命令执行',
  'execute_code': '代码执行',
  'execute_cli_command': 'CLI 命令执行',
  'run_ssh_command_with_credentials': 'SSH 远程命令',

  // ---- 情报 & Web ----
  'make_web_search_with_explanation': '带解释网页搜索',
  'shodan_search': 'Shodan 资产搜索',
  'shodan_host_info': 'Shodan 主机详情',
  'fetch_url': 'URL 内容获取',
  'http_request': 'HTTP 请求',
  'web_request_framework': 'HTTP 请求安全分析',

  // ---- 渗透 / 扫描 ----
  'port_scan': '端口扫描',

  // ---- 共享协作浏览器 ----
  'shared_browser_open': '打开浏览器',
  'shared_browser_wait_user': '等待用户操作',
  'shared_browser_snapshot': '浏览器截图',
  'shared_browser_click': '浏览器点击',
  'shared_browser_fill': '浏览器输入',
  'shared_browser_evaluate': '浏览器脚本执行',
  'shared_browser_status': '浏览器状态',
  'shared_browser_close': '关闭浏览器',

  // ---- 思考 / 规划 / 记录 ----
  'think': '思考',
  'thought': 'CTF 思路记录',
  'Todo_list': '任务待办列表',
  'write_key_findings': '写入关键发现',
  'read_key_findings': '读取关键发现',
  'null_tool': '占位空工具',

  // ---- 多智能体编排 / 路由 ----
  'check_available_agents': '查询可用智能体',
  'analyze_task_requirements': '分析任务需求',
  'list_available_specialists': '列出可用专项专家',
  'run_specialist': '委派单个专家',
  'run_parallel_specialists': '并行委派多位专家',
  'run_dual_approach_contest': '双方案对比竞赛',
  'get_agent_number': '获取智能体编号',

  // ---- 流量 / 邮件 ----
  'capture_remote_traffic': '捕获远程流量',
  'remote_capture_session': '远程流量捕获会话',
  'check_mail_spoofing_vulnerability': '邮件欺骗漏洞检测',
  'verify_csv_inventory': 'CSV 资产清单核对',

  // ---- 应用分析 / Agent Builder ----
  'app_mapper': '应用逻辑与架构映射',
  'generate_system_prompt': '生成系统提示词',
  'list_available_tools': '列出可用工具',
  'save_agent_file': '保存智能体文件',
};

export const agentDescCN: Record<string, string> = {
  // ---- 安卓 / 应用安全 ----
  'Android App Logic Mapper':
    '专注于 Android 应用逻辑分析，理解运行机理并输出完整架构映射的智能体。',
  'Android Sast':
    '专注于 Android 应用静态安全测试（SAST）与漏洞发现的智能体。',

  // ---- 红队 / APT 模拟 ----
  'Apt Agent':
    'APT 高级持续性威胁仿真专家，模拟真实 APT 组织多阶段、隐蔽式攻击链，' +
    '严格遵循 MITRE ATT&CK 框架，覆盖侦察、初始入侵、持久化、横向移动与数据窃取全流程。',
  'Red Team Agent':
    '红队攻击模拟专家，覆盖渗透测试、漏洞利用、权限提升、横向移动及 APT 式对手仿真，' +
    '严格遵循 MITRE ATT&CK 框架，精通全攻击链操作。',

  // ---- 蓝队 / 防御 ----
  'Blue Team Agent':
    '蓝队防御与安全监控专家，精通系统加固、威胁检测、安全事件响应与日志分析。',

  // ---- Web / 漏洞赏金 ----
  'Bug Bounty Hunter':
    '漏洞赏金猎人，自主规划→执行→观察→调整，发现 Web 应用安全漏洞并输出可复现的漏洞报告。',
  'Web Application Pentester':
    'Web 应用渗透测试专家，覆盖全方位 Web/API 安全测试、漏洞赏金狩猎、PoC 开发与负责任披露全流程。',
  'Web Bounty Agent':
    'Web 漏洞赏金专家，具备完全自主的 Web 安全测试与漏洞研究能力，覆盖信息收集、漏洞发现与利用全过程。',
  'Replay attack testing and anti-replay validation agent':
    '重放攻击与防重放验证专家，捕获→修改→重放→观察结果→调整策略，' +
    '验证会话令牌、签名与时间戳等防护机制的有效性。',

  // ---- CTF ----
  'CTF agent (capture-the-flag)':
    'CTF 夺旗赛专家，通过通用 Linux 命令执行渗透测试与漏洞利用，攻克各类安全挑战。',
  'Flag discriminator':
    'Flag 判别器，从工具输出中精准提取 CTF 竞赛 Flag。',
  'Thought Router':
    'CTF 思路规划路由，分析当前进度并规划下一步策略，辅助 CTF Boot2Root 挑战。',

  // ---- 专项领域 ----
  'Dfir Agent':
    '数字取证与事件响应（DFIR）专家，覆盖磁盘取证、内存分析（Volatility/VolShell）、' +
    '网络流量分析（PCAP/tshark/zeek）、恶意软件分类及事后入侵调查。',
  'Reverse Engineering Agent':
    '逆向工程专家，精通二进制分析、固件分析、反汇编与反编译，' +
    '熟练运用 Ghidra、Binwalk 等逆向分析工具进行漏洞发现。',
  'Memory Analysis Agent':
    '内存分析与操控专家，专注运行时内存检查、监控与修改，用于安全测试与漏洞研究。',
  'Network Analyzer':
    '网络流量安全分析专家，在 SOC 环境中对网络流量进行深度包检测、协议分析与异常检测。',
  'Wifi Security Agent':
    'Wi-Fi 无线网络安全测试专家，精通无线攻击、密码恢复、通信干扰与 Wi-Fi 渗透测试。',
  'Subghz Agent':
    'Sub-GHz 射频信号安全专家（基于 HackRF One），精通 IoT、汽车、工业及无线安全场景下的' +
    '信号捕获、重放与协议分析。',
  'Advanced Exploit Development Agent':
    '高级漏洞利用开发专家，专注于 Boot2Root 场景下的漏洞利用编写与 PoC 开发。',
  'DNS / SMTP email authentication agent':
    'DNS/SMTP 邮件认证安全专家，检测域名 SPF、DMARC、DKIM 配置，' +
    '评估邮件欺骗与邮件安全防护能力。',

  // ---- 编排与管理 ----
  'Orchestration Agent (default entry)':
    '白泽·智脑默认编排器，采用广度优先多智能体委派策略：先并行派发多个侦察兵，' +
    '可选双方案对比竞赛，再按需委派专项跟进，直至达成用户目标。',
  'Selection Agent (default orchestrator)':
    '白泽·智脑编排路由器，将网络安全任务自动路由到最合适的专项智能体，' +
    '也可纯会话式回答"该用哪个智能体"类元问题。',
  'You are the **Continuous Ops agent** for Baize (Cybersecurity AI Framework).':
    '7×24 持续安全运维专家，支持长期周期性安全监控任务编排，提供 CLI 向导验证任务间隔、' +
    'tmux 后台运行与权限策略配置。',
  'Cybersecurity Triage Agent':
    '网络安全分诊专家，快速复现→观察→判定，对安全告警与漏洞报告进行初步验证与分类。',
  'Reasoner Supporter':
    '推理策略辅助专家，为渗透测试任务提供深度分析与策略规划支持。',

  // ---- 治理 / 报告 / 元智能体 ----
  'Reporting Agent':
    '安全报告自动生成专家，从证据中提取关键信息，自动生成结构化的 HTML 安全报告。',
  'Risk & Compliance (GRC) assistant':
    '风险与合规（GRC）助手，将安全控制映射到 NIS2、EU CRA、ISO/IEC 27001、' +
    'IEC 62443、OWASP 等框架，提供基于证据的差距分析（非法律建议）。',
  'Use Cases':
    '安全场景用例生成助手，展示白泽·智脑在各种安全场景、CTF 挑战与攻防演练中的实战能力。',
  'Tool Builder':
    '白泽·智脑元智能体，根据用户需求快速创建新工具，支持代码生成、沙箱试运行与热注册。',
  'AGENT MICRO-PROFILE: CODEAGENT (CODEACT)':
    '代码执行与脚本微专家，在安全测试中提供代码执行与自动化脚本能力。',

  // ---- 模式 / 多智能体组合 (key 保持旧名以兼容 pattern agents) ----
  'offsec_pattern':
    '漏洞赏金与红队集群攻击模式，为攻击性安全操作提供多上下文并行调度。',
  'blue_team_red_team_shared_context':
    '红蓝队共享上下文协同模式，双方在统一上下文中协同执行安全评估。',
  'blue_team_red_team_split_context':
    '红蓝队独立上下文综合评估模式，双方以不同视角并行执行全面的安全评估。',
  'meta_agent':
    '白泽·智脑元智能体开关，启用后激活全局 TUI 编排器（BAIZE_META_AGENT=True）。',
};

export const toolDescCN: Record<string, string> = {
  'generic_linux_command':
    '通用 Linux 命令执行，自动检测容器/CTF/SSH 环境，支持会话管理与输出捕获。',
  'execute_code':
    '代码创建、存储与执行工具，支持 Python/Perl 等多种语言，可指定工作目录与超时时间。',
  'run_ssh_command_with_credentials':
    '通过 SSH 密码认证在远程主机上执行命令。',
  'fetch_url':
    '获取单个 URL 内容并解析为 LLM 可读格式（HTML→Markdown、PDF→文本、JSON 美化）。',
  'shodan_search':
    '按查询条件搜索 Shodan 数据库获取互联网资产情报。',
  'shodan_host_info':
    '获取指定主机的 Shodan 详细信息与资产情报。',
  'think':
    '安全策略推理与深度思考工具，用于复杂分析或缓存记忆场景。',
  'thought':
    'CTF Boot2Root 场景专用思路与分析记录工具。',
  'web_request_framework':
    'HTTP 请求/响应详细安全分析工具，用于 Web 安全测试。',
  'Todo_list':
    '更新当前智能体的任务计划（待办列表）管理工具。',
  'write_key_findings':
    '将关键发现持久化写入 state.txt 文件，跟踪重要 CTF/渗透进展信息。',
  'read_key_findings':
    '从 state.txt 文件读取关键发现，检索已记录的渗透数据。',
  'null_tool':
    '占位工具（无实际操作），用于纯分析型智能体。',
  'check_available_agents':
    '查询 白泽·智脑系统中所有可用智能体及其详细信息。',
  'execute_cli_command':
    '执行 CLI 命令并返回输出结果。',
  'run_specialist':
    '委派单个专项智能体执行子任务，编排器保持主控权。',
  'run_parallel_specialists':
    '并行委派 2–4 个专项智能体执行独立的子任务，编排器保持主控权。',
  'run_dual_approach_contest':
    '对同一任务启动两个并行探索方案进行对比竞赛（最多 2 个智能体）。',
  'get_agent_number':
    '获取指定智能体的编号索引，便于命令快捷引用。',
  'capture_remote_traffic':
    '捕获远程虚拟机的网络流量，返回可供 tshark 读取的数据流。',
  'remote_capture_session':
    '远程流量捕获上下文管理器，自动清理资源。',
  'check_mail_spoofing_vulnerability':
    '检查域名是否存在邮件欺骗漏洞，自动检测 SPF、DMARC、DKIM 记录配置。',
  'analyze_task_requirements':
    '分析用户任务描述，提取关键需求与特征。',
  'verify_csv_inventory':
    '核对 CSV/文本文件中的资产 ID 清单与智能体输出中提到的 ID。',
  'app_mapper':
    '应用逻辑分析与架构映射工具，理解应用运行逻辑并输出完整功能图谱。',
};

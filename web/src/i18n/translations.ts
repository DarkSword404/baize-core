// ============================================================
// 白泽前端 — 智能体与工具描述中文本地化
// 所有映射基于白泽 API 真实返回数据构建，不影响 API 通信
// ============================================================

export const agentDescCN: Record<string, string> = {
  // ---- 应用安全 ----
  'AndroidSAST':
    '专注于 Android 应用静态应用安全测试（SAST）与漏洞发现的智能体。',
  'AppLogicMapper':
    '专注于应用逻辑分析，理解运行机理并输出完整架构映射的智能体。',

  // ---- 蓝队 / 防御 ----
  'Blue Team Agent':
    '专注于系统防御与安全监控的智能体，精通网络安全防护与事件响应。',

  // ---- 红队 / 攻击 (含 APT 模拟) ----
  'Red Team Agent':
    '红队攻击模拟智能体，覆盖渗透测试、漏洞利用、权限提升及 APT 式对手仿真，' +
    '严格遵循 MITRE ATT&CK 框架，精通全攻击链操作。',

  // ---- Web 安全 (含漏洞赏金 + 重放攻击) ----
  'Web App Pentester':
    'Web/API 安全专家，覆盖全方位渗透测试、漏洞赏金狩猎、PoC 开发、重放攻击' +
    '及负责任披露全流程。',

  'Retester Agent':
    '专注于漏洞验证与分类的智能体，精通漏洞可利用性判定，消除误报。',

  // ---- CTF ----
  'CTF agent':
    '专注 CTF 安全挑战攻克的智能体，通过通用 Linux 命令执行渗透测试与漏洞利用。',
  'Flag discriminator':
    '专注于从输出中提取 Flag 的智能体，用于 CTF 竞赛辅助。',
  'ThoughtAgent':
    '专注于安全评估或 CTF 挑战中下一步策略分析与任务规划的智能体。',

  // ---- 专项领域 ----
  'Wi-Fi Security Tester':
    '专注于 Wi-Fi 网络安全测试与渗透的智能体，精通无线攻击、密码恢复与通信干扰。',
  'DFIR Agent':
    '统一定制取证与事件响应（DFIR）平台：磁盘取证、内存分析（Volatility/VolShell）、' +
    '网络流量分析（PCAP/tshark/zeek）、恶意软件分类及事后入侵调查。',
  'Reverse Engineering Specialist':
    '专注于二进制分析与逆向工程的智能体，精通固件分析、二进制反汇编与反编译，' +
    '熟练运用 Ghidra、Binwalk 等逆向分析工具进行漏洞发现。',
  'Sub-GHz SDR Specialist':
    '专注于 Sub-GHz 射频信号安全分析的智能体（基于 HackRF One），' +
    '精通 IoT、汽车、工业及无线安全场景下的信号捕获、重放与协议分析。',
  'DNS_SMTP_Agent':
    '专注于邮件欺骗与 DMARC 安全评估的智能体，检测域名的 SPF、DMARC、DKIM 配置。',

  // ---- 编排与管理 ----
  'Orchestration Agent':
    '白泽默认编排器，采用广度优先多智能体委派策略：先并行派发多个侦察兵，' +
    '可选双方案对比竞赛，再按需委派专项跟进，直至达成用户目标。',
  'Selection Agent':
    '白泽编排路由器，将网络安全任务自动路由到最合适的专项智能体，' +
    '也可纯会话式回答"该用哪个智能体"类元问题。',
  'Continuous Ops Agent':
    '7×24 小时持续安全监控任务编排智能体，提供 CLI 向导验证任务间隔、tmux 后台运行、' +
    '权限策略，然后启动 Selection-Agent 工作循环。',
  'Red team manager':
    '红队策略管理与任务规划智能体，分析并规划安全评估或 CTF 挑战的后续步骤。',

  // ---- 治理与报告 ----
  'Risk & Compliance Agent':
    '治理与合规支持智能体，将安全控制映射到 NIS2、EU CRA、ISO/IEC 27001、' +
    'IEC 62443、OWASP 等框架，提供基于证据的差距分析（非法律建议）。',
  'reporting agent':
    'HTML 格式安全报告自动生成智能体。',
  'Use Case Agent':
    '高质量网络安全案例研究生成智能体，展示白泽在各种安全场景、CTF 挑战与' +
    '网络安全演练中的实战能力。',

  // ---- 模式 / 多智能体组合 ----
  'offsec_pattern':
    '漏洞赏金与红队集群攻击模式，为攻击性安全操作提供多上下文并行调度。',
  'blue_team_red_team_shared_context':
    '红蓝队共享上下文协同模式，双方在统一上下文中协同执行安全评估。',
  'blue_team_red_team_split_context':
    '红蓝队独立上下文综合评估模式，双方以不同视角并行执行全面的安全评估。',
  'meta_agent':
    '白泽元智能体开关，启用后激活全局 TUI 编排器（BAIZE_META_AGENT=True）。',
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
    '查询 白泽系统中所有可用智能体及其详细信息。',
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

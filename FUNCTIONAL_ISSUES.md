# 白泽·智脑 (baize-core v4.0.0) 功能缺陷评估报告

> 评估目标：`https://github.com/DarkSword404/baize-core`
> 评估日期：2026-09-18
> 评估方式：只读源码审查，未修改任何文件
> 评估范围：后端 API/编排/工具/会话 + 前端 UI/交互/工程化 + 跨维度性能/可用性/兼容性

---

## 一、概览

### 1.1 严重程度定义

| 等级 | 说明 |
|---|---|
| **严重** | 核心功能不可用 / 数据丢失 / 部署阻塞 / 安全性实质破坏 |
| **高** | 影响生产稳定性 / 关键场景失效 / 不可恢复 / 体验严重受损 |
| **中** | 影响可维护性 / 边界场景退化 / 体验受损 |
| **低** | 优化项 / 文档缺失 / 防御性不足 |

### 1.2 缺陷统计

| 维度 | 严重 | 高 | 中 | 低 | 小计 |
|---|---|---|---|---|---|
| 后端（API/编排/工具/会话/可观测性） | 3 | 12 | 22 | 4 | 41 |
| 前端（UI/交互/工程化/状态管理） | 3 | 7 | 6 | 3 | 20（含 1 项设计说明） |
| 跨维度（性能/可用性/边界/兼容性/可维护性/完整性） | 3 | 7 | 11 | 4 | 26（含 1 项设计说明） |
| **合计（去重后）** | **8** | **25** | **38** | **11** | **82** |

### 1.3 核心结论

1. **3 个致命缺陷**集中在编排循环与容器执行：`asyncio.timeout` 在声明的 Python 3.10 上不可用、`except BaseException` 吞掉 `CancelledError` 破坏超时取消、`DockerExecutor` 超时只杀本地客户端导致容器孤儿。
2. **前端三大可用性风险**：全局无 `ErrorBoundary`、SSE 无断线重连、长会话无虚拟列表，共同构成流式对话可用性的核心风险链。
3. **工程化短板集中**：前后端测试覆盖近乎为零、Python 依赖无锁文件、前端 `any` 类型泛滥、生产 `console.log` 残留。
4. **可观测性缺失**：无 metrics 端点、日志无轮转/无结构化、健康检查仅返回 ok。
5. **功能完整性缺口**：无 API 限流、无 RBAC、报告无 PDF/Word 导出、无多用户协同。

---

## 二、后端功能缺陷

### 2.1 智能体编排模块（`src/baize/pentest/conversation_orchestrator.py`）

#### 🔴 B-01｜`asyncio.timeout` 在声明的 Python 3.10 上不可用【严重】
- **位置**：`conversation_orchestrator.py:2304`；`pyproject.toml:6` (`requires-python = ">=3.10"`)
- **描述**：`async with asyncio.timeout(wave_budget)` 使用了 Python 3.11 才引入的 `asyncio.timeout`。在 Python 3.10 环境下运行到该行会抛 `AttributeError`，整轮编排立即失败。
- **影响**：Python 3.10 环境（许多企业默认 Python）下，对话编排第一条波次就崩溃，前端只见通用错误，问题难定位。
- **建议**：将 `requires-python` 提到 `>=3.11`（与 `cli.py:82-83` 的 doctor 检查一致），或改用 `asyncio.wait_for(...)`；补 CI 矩阵跑 3.10。

#### 🔴 B-02｜`except BaseException` 吞掉 `CancelledError`，破坏超时取消【严重】
- **位置**：`conversation_orchestrator.py:2286-2291`
- **描述**：`_pump` 协程的 `try` 块用 `except BaseException as ex` 捕获，包括 `asyncio.CancelledError`（Python 3.8+ 继承自 `BaseException`）。当外层 `asyncio.timeout(wave_budget)` 触发取消时，`_pump` 收到 `CancelledError` 后被静默吞掉，阻止了取消信号级联。
- **影响**：波次超时后 agent 协程实际未被取消，仍在后台继续跑工具/调 LLM/抢浏览器锁，超时只是"假性"触发。
- **建议**：改为 `except Exception`（不含 `CancelledError`），或显式 `except asyncio.CancelledError: raise`。

#### 🟠 B-03｜Reason LLM 调用失败被静默降级为空 dict，触发停滞收束【高】
- **位置**：`conversation_orchestrator.py:2064-2066`
- **描述**：LLM 单次失败（限流/超时/网络抖动）后 `reason_json = {}`，后续判定为"Reason 未提议新方向"，与"真正无方向可提"无法区分，直接累积 `stagnation_count`。
- **影响**：一次 LLM 抖动就让正在执行的渗透任务被判定为"已穷尽攻击面"提前结束。
- **建议**：区分"LLM 调用失败"与"LLM 返回空"——前者重试 2~3 次仍失败再降级，且不计入 `stagnation_count`。

#### 🟠 B-04｜停滞收束前的"替代路径检查"会无限重置 stagnation_count【高】
- **位置**：`conversation_orchestrator.py:2174-2193`、`2562-2581`
- **描述**：当 `stagnation_count >= stagnation_limit` 时调用 `_llm_check_alternatives`，只要 LLM 任意输出一个 intent，就 `stagnation_count = 0; continue`，形成 stagnation→alternatives→执行→无新发现→stagnation 的死循环，最终被 `ABSOLUTE_MAX_ROUNDS=12` 兜底。
- **影响**：单次会话最坏能消耗 12 轮 × 36+ 次 LLM 调用，预算被烧光才结束。
- **建议**：引入"alternatives 触发次数"上限（如 2 次）；重置时只减半不清零。

#### 🟡 B-05｜`_pump` 兜底 `agent.run()` 在流式产出为空时重复执行【中】
- **位置**：`conversation_orchestrator.py:2275-2285`
- **描述**：当 `run_stream` 流正常结束但 `w["output"]` 为空，代码会兜底调 `await agent.run(...)` 重新执行一遍，相当于同一个 intent 被执行两次。
- **影响**：工具调用、token 全部翻倍；scoped pentest 中可能对目标重复扫描。
- **建议**：先检查 `w["records"]` 与 `blackboard.facts_for_intent()`，已有产出则不再兜底。

#### 🟡 B-06｜`MAX_PARALLEL=3` 为硬编码常量，无法自适应【中】
- **位置**：`conversation_orchestrator.py:43,66,2102`
- **描述**：并行度硬编码为 3，无法按硬件/LLM 配额自适应。
- **影响**：大规模 recon 阶段并行度受限，任务耗时倍增。
- **建议**：改为读取 `BAIZE_ORCH_MAX_PARALLEL` 环境变量，按 LLM provider RPM 限额动态计算。

#### 🟡 B-07｜无工具调用循环检测（同工具同参数连续调用不熔断）【中】
- **位置**：`conversation_orchestrator.py`（pump 循环）
- **描述**：模型陷入"调用-失败-重复调用"死循环时无熔断，持续消耗 LLM 配额直至 timeout。
- **建议**：记录 `(tool, args_hash)` 调用历史，连续重复 ≥3 次时注入"请改变策略"提示。

### 2.2 容器执行模块（`src/baize/executors.py`）

#### 🔴 B-08｜`DockerExecutor` 超时只杀本地 docker 客户端，容器变孤儿【严重】
- **位置**：`executors.py:408-413`
- **描述**：`proc.kill()` 杀的是本地 `docker run` 进程，容器本身在 dockerd 那边继续运行（`--rm` 只在容器主进程退出后清理）。
- **影响**：超时的扫描容器长期占用宿主资源；累计后导致 Docker daemon 资源耗尽。
- **建议**：超时时通过 `docker rm -f baize-run-<run_id>` 主动清理；或改用 `--name` 后超时直接 `docker rm -f`。

#### 🟠 B-09｜`DockerExecutor` 用 `shlex.split(command)` 拆分命令，丢失 shell 语法【高】
- **位置**：`executors.py:392`
- **描述**：`args += shlex.split(command)` 直接把命令拆成 argv，shell 管道、重定向、变量展开全部失效。例如 `nmap -sV target | grep open` 中 `|` 变成 nmap 的位置参数。
- **影响**：所有依赖管道/重定向的安全工具命令在 docker 后端下都跑不对，与 LocalExecutor 的 `/bin/bash -c` 行为不一致。
- **建议**：统一用 `['sh','-c',command]` 模式，保持与 LocalExecutor 一致的 shell 语义。

#### 🟠 B-10｜`LocalExecutor._wrap_bwrap` 超时时 `killpg` 失效留孤儿【高】
- **位置**：`executors.py:255-269、327-345`
- **描述**：bwrap 用 `--new-session` 创建新会话，Python 端 `start_new_session=True` 也设置了进程组。两层 new-session 叠加后，bwrap 子进程的 pgid 与 `proc.pid` 不同步，`_terminate_tree` 退到 `proc.kill()` 只杀 bwrap 主进程，bwrap 内子进程变孤儿。
- **建议**：超时时通过 cgroup ID 主动清理；或不用 `--new-session` 改用 `setsid`。

#### 🟡 B-11｜`DockerExecutor` 默认 `--network host` 破坏容器隔离承诺【中】
- **位置**：`executors.py:368、387`
- **描述**：注释说"容器天然提供 full 隔离"，但默认 `--network host` 让容器与宿主共享网络命名空间。
- **建议**：默认改 `network="bridge"`；提供 `network=host` 的显式开关。

#### 🟡 B-12｜`DockerExecutor.docker_cmd` 默认硬编码 `"docker"`【中】
- **位置**：`executors.py:370,384`
- **描述**：podman 环境下若用户未显式传 `docker_cmd="podman"`，DockerExecutor 直接报 "docker command not found"。
- **建议**：启动时统一探测运行时（podman 优先于 docker），将结果注入所有 executor。

#### 🟡 B-13｜`DEFAULT_TOOL_EXEC_TIMEOUT=300s` 固定，无按工具类别分级【中】
- **位置**：`executors.py:82,95`
- **描述**：nmap 全端口扫描需 >5 分钟被强制中断；简单 whois 反而等满 300s 才返回错误。
- **建议**：支持工具级 timeout 配置（`tool_timeout_overrides` 字典）。

#### 🟡 B-14｜5 个 Executor 类集中在单文件，无抽象基类复用【中】
- **位置**：`executors.py`（Local/Docker/SSH/Tmux/SessionContainer）
- **描述**：5 个 Executor 类集中在单文件，timeout 处理、stderr 捕获逻辑各写一遍。
- **建议**：抽取 `BaseExecutor._run_with_timeout` 模板方法，子类只实现 `build_command`。

### 2.3 API 与会话管理（`src/baize/api/`）

#### 🟠 B-15｜SSE 单飞取消逻辑漏掉 pipeline 路径【高】
- **位置**：`api/app.py:2128-2135`、`1844-2472`
- **描述**：会话单飞表 `_active_session_tasks` 仅在 `use_conversation_orchestrator` 分支中加锁注册/取消旧任务；pipeline 分支只依赖外部 `runner.subscribe_events()` 的取消语义，无同等保护。
- **影响**：用户重发消息时旧 pipeline 协程仍可能跑，与新模式重叠烧 token。
- **建议**：把取消逻辑提到 `event_source` 入口处统一执行。

#### 🟠 B-16｜Webhook 路由 catch-all 无幂等保证，重启窗口期数据静默丢失【高】
- **位置**：`api/app.py:767-769`、`receivers/manager.py:141-158`
- **描述**`/api/v1/hook/{path:path}` 捕获所有方法，但 `accept_webhook` 在 `loop is None` 时直接返回 `False` 且不持久化重试；启动/重启窗口内的 webhook 被静默丢弃。
- **影响**：服务重启期间接收到的告警数据永久丢失。
- **建议**：loop 不可用时把数据落到本地 spool 目录，启动后回放；为 catch-all 增加简单限流。

#### 🟠 B-17｜`SessionManager.bind_container` 在 async 上下文调 `asyncio.run` 会崩【高】
- **位置**：`api/sessions.py:281、293、315`
- **描述**：`asyncio.run(mgr.ensure_container(session_id))` 在 async 函数里调用会抛 `RuntimeError`。
- **建议**：改为 `async def`，或用 `asyncio.run_coroutine_threadsafe`。

#### 🟠 B-18｜`_load_all` 静默吞掉损坏的 session JSON，用户数据"凭空消失"【高】
- **位置**：`api/sessions.py:146-177`
- **描述**：单个 session 文件损坏时直接 `continue`，没有 logger.warning，前端列表里这个 session 不存在。
- **影响**：用户历史任务可能因单次磁盘故障被静默删除。
- **建议**：损坏文件移到 `sessions/.broken/`，logger.error 记录文件名与原因。

#### 🟠 B-19｜`AttachmentStore.save_attachment` 整文件入内存，无流式上传【高】
- **位置**：`api/attachments.py:181-225`
- **描述**：把整个文件作为 bytes 参数接收，默认上限 500MB。并发上传时进程内存占用 = 并发度 × 500MB。
- **影响**：并发上传 4 个 500MB 文件即可让 2GB 内存的容器 OOM。
- **建议**：API 层用 `UploadFile` 流式读取，分块 `f.write(chunk)`。

#### 🟠 B-20｜无任何 metrics 端点，关键运营指标不可见【高】
- **位置**：缺失（全仓无 `prometheus`/`metrics` 相关代码）
- **描述**：没有 `/metrics`、没有 Prometheus exporter、没有内置计数器。无法观测活跃 session 数、pending run 队列深度、LLM 调用 P99 延迟、工具失败率、SSE 连接数等。
- **建议**：引入 `prometheus_client`，暴露 `/metrics`。

#### 🟠 B-21｜日志无轮转、无结构化字段，仅 basicConfig 到 stdout【高】
- **位置**：`cli.py:178-185`、`api/app.py:623-627`
- **描述**：`logging.basicConfig(stream=sys.stdout)` 配置日志，无 `RotatingFileHandler`、无 JSON 格式化、无 trace_id/session_id 注入。
- **影响**：事故复盘时无法关联同一 session 跨模块的事件链；重启即丢。
- **建议**：用 `structlog` 输出 JSON，字段含 `session_id`/`run_id`；配置 `RotatingFileHandler`。

#### 🟡 B-22｜健康检查端点只回 ok，不做任何依赖巡检【中】
- **位置**：`api/app.py:774-776`
- **描述**：`/api/v1/health` 仅返回 `{"status":"ok"}`，未检查 LLM 配置、SQLite、附件目录、Docker runtime 等。
- **建议**：拆分 `/live`（进程存活）和 `/ready`（依赖就绪），后者探：模型配置存在性 + SQLite 可写 + 关键目录可访问。

#### 🟡 B-23｜WebSocket 鉴权用 `?token=` query，token 泄露到日志/Referer【中】
- **位置**：`api/app.py:910-915`
- **描述**：token 通过 query 泄露到 access log、浏览器历史、Referer 头，违反注释自己写的"避免泄露到浏览器历史/日志"原则。
- **建议**：改用首帧鉴权或子协议 `X-Baize-API-Key`；access log 默认脱敏 `?token=`。

#### 🟡 B-24｜SSE `[DONE]` 兜底双重 yield，部分客户端触发协议错误【中】
- **位置**：`api/app.py:2384-2399`
- **描述**：`finally` 块无条件再 `yield "data: [DONE]\n\n"`，但正常路径里多处已 yield 过 `[DONE]` 并 `return`。
- **建议**：用 `done_sent` 标志统一控制；`finally` 块只做 `aclose`。

#### 🟡 B-25｜`save_assistant_draft` 在流式热路径里做 O(N) 反向遍历【中】
- **位置**：`api/sessions.py:446-474`
- **描述**：每次 `_persist_draft` 调用都 `for m in reversed(session.messages)` 查找最近一个 `draft=True` 的 assistant 消息。长会话每轮都 O(N)。
- **建议**：维护 `draft_index` 缓存位置。

#### 🟡 B-26｜`extract_archive` 文件数达上限时静默截断【中】
- **位置**：`api/attachments.py:380-405`
- **描述**：break 后无错误返回，前端无任何"压缩包被截断"提示。
- **建议**：超限时返回 `{"ok": False, "error": "压缩包文件数超限"}`。

#### 🟡 B-27｜`AttachmentStore._save_index` 非原子写，并发上传丢条目【中】
- **位置**：`api/attachments.py:222-224、236-239`
- **描述**：两个并发上传同时读到旧 index，各自加一条，后写的覆盖先写的。
- **建议**：用文件锁或 `threading.Lock` 保护整个 read-modify-write。

#### 🟡 B-28｜`ArchiveManager.delete_archive` 只删 JSON，工作区与附件目录残留【中】
- **位置**：`api/archives.py:114-120`
- **描述**：`delete_archive` 仅 `f.unlink()` 删 JSON，`files/` 与 `workspace/` 仍残留，成为永久孤儿。
- **建议**：接受 `purge_files=True` 选项，同步清理整目录。

#### 🟡 B-29｜`_print_credentials` 在启动时把 token 打到 stdout【中】
- **位置**：`api/app.py:713`
- **描述**：服务启动时把 API token / 登录凭证打到 stdout，容器化部署时 stdout 进容器日志，被运维平台永久存档。
- **建议**：默认写 `~/.baize/credentials.txt`（0600）+ stdout 只显示路径；`BAIZE_PRINT_TOKENS=1` 才回显。

#### 🟢 B-30｜SSE 心跳包装器 cancel 后未 await，异常被吞【低】
- **位置**：`api/app.py:529-559`
- **建议**：cancel 后 `await asyncio.gather(*tasks, return_exceptions=True)` 收集异常。

### 2.4 编排流水线（`src/baize/orchestration/`）

#### 🟠 B-31｜`_send_webhook` 失败仅 warn 不重试，关键回调丢失【高】
- **位置**：`orchestration/runner.py:462-478`
- **描述**：webhook 调用一次性 httpx.post，失败 `logger.warning` 后吞掉。pipeline 完成/失败事件对外部系统至关重要。
- **影响**：webhook 端点短暂不可用时整轮 pipeline 状态永久丢失。
- **建议**：失败 webhook 入持久化队列，后台 worker 指数退避重试。

#### 🟡 B-32｜`pipeline` 模式 `cache_pipeline` 是进程级类变量，多 worker 不共享【中】
- **位置**：`orchestration/runner.py:453-459`
- **描述**：多 worker 部署时每个进程独立缓存，`recover_interrupted` 在另一个 worker 上恢复时 `_get_pipeline_def` 返回 None。
- **建议**：把 pipeline 定义持久化到 RunStore，缓存改 LRU。

#### 🟡 B-33｜Parallel 节点 `gather(return_exceptions=True)` 后无失败传播【中】
- **位置**：`orchestration/nodes/parallel.py:48-58`
- **描述**：1/N 分支失败时节点状态仍是 done，下游基于不完整数据继续跑。
- **建议**：节点末尾按 branches 状态聚合：全成功→done；全失败→`_record_failed`。

#### 🟡 B-34｜`recover_interrupted` 不验证 context 完整性【中】
- **位置**：`orchestration/runner.py:220-241`
- **描述**：服务重启后扫描 `status in (pending, running)` 的 run，把 `rec.context` 原样传进去，不校验。
- **建议**：恢复时先 schema 校验 context，缺失必填字段直接 `update_status("failed")`。

#### 🟡 B-35｜`RunStore` 事件追加是 O(N²) 全量重写【中】
- **位置**：`orchestration/run_store.py:226-233`
- **描述**：每次 `add_event` 都重写整个 events JSON blob。200 个事件的 pipeline 触发 20100 次字符串拼接。
- **建议**：把 events 拆到独立表 `run_events(run_id, seq, event_json)`。

#### 🟢 B-36｜`_push_event` 队列满时 `put_nowait` 静默丢事件【低】
- **位置**：`orchestration/runner.py:440-447`
- **建议**：丢事件时 logger.warning + overflow 计数。

#### 🟢 B-37｜`PipelineInstanceStore.update` 改 `max_concurrency` 不影响已运行实例【低】
- **位置**：`orchestration/instance_store.py:145-168`
- **建议**：文档明确"修改仅对新 run 生效"。

### 2.5 工具系统（`src/baize/tools/`）

#### 🟡 B-38｜`ToolRegistry` 声称线程安全但实际无锁【中】
- **位置**：`tools/registry.py:148-203`
- **描述**：`register`/`unregister`/`get`/`all` 都没加锁，并发调用时偶发丢失工具或 `RuntimeError: dictionary changed size during iteration`。
- **建议**：补 `threading.RLock`。

#### 🟡 B-39｜自定义工具 `update` 修改 `name` 时旧名注册项不清理【中】
- **位置**：`tools/custom_tools.py:211-238`
- **描述**：改完后调 `_register(record)` 用新名注册，但旧名仍留在 `registry._tools` 里。
- **建议**：改名时先 `self._unregister(old_name)` 再 `_register(record)`。

#### 🟡 B-40｜自定义工具 `exec` 命名空间不限制 `__builtins__`【中】
- **位置**：`tools/custom_tools.py:63-67、270-282`
- **描述**：`load_handler` 用 `exec(compile(code, ...), namespace)` 加载用户代码，namespace 只设 `__name__`，不限制 builtins。用户可 `import os; os.system(...)`，绕过沙箱审批。
- **建议**：在 `_register` 时把 spec 的 `category` 标记为 `dangerous`，或文档明确警告自定义工具 = 完全信任。

#### 🟢 B-41｜`_register_all` 用 `print` + `traceback.print_exc` 输出错误【低】
- **位置**：`tools/custom_tools.py:298-300`
- **建议**：改用 `logger.error(..., exc_info=True)`。

### 2.6 报告与数据存储（`src/baize/reports/`）

#### 🟡 B-42｜`ReportStore.get_content` 在锁外读文件，删除竞态抛 FileNotFoundError【中】
- **位置**：`reports/store.py:134-141`
- **建议**：文件读放在锁内；或锁外捕获 `FileNotFoundError` 返回 None。

#### 🟡 B-43｜`ReportStore._load_all` 单条损坏条目让全量索引失效【中】
- **位置**：`reports/store.py:83-95`
- **描述**：`from_dict` 在 `data["id"]` 缺失时抛 `KeyError`，外层 `except` 把整个索引加载失败，所有报告不可见。
- **建议**：`from_dict` 缺失 id 时返回 None 跳过；外层 `try` 缩到单条 `for` 内部。

#### 🟠 B-44｜报告仅支持 `.md` 落盘，无 PDF/Word/HTML 导出【高】
- **位置**：`reports/store.py:80`
- **描述**：渗透测试交付物不符合甲方常见格式要求（甲方通常要 PDF/Word）。
- **建议**：集成 `weasyprint`（PDF）/`python-docx`（Word）；增加 `GET /reports/{id}.pdf` 端点。

### 2.7 接收器（`src/baize/receivers/`）

#### 🟡 B-45｜`receiver` 任务创建后不持引用，可能被 GC【中】
- **位置**：`receivers/manager.py:91、95`
- **描述**：`asyncio.create_task(receiver.run(self._on_data))` 返回的 task 未保存到任何集合。Python 文档明确：未引用的 task 可能被 GC 回收。
- **建议**：`self._tasks: set[asyncio.Task] = set()`，`task.add_done_callback(self._tasks.discard)`。

#### 🟡 B-46｜`disable_receiver` 只改配置，不取消运行中的 receiver 任务【中】
- **位置**：`receivers/manager.py:193-195`
- **描述**：用户停用 receiver 后端口仍占用；继续接收数据但 UI 显示已停用。
- **建议**：维护 `receiver_id -> task` 映射，`disable_receiver` 时 `task.cancel()`。

#### 🟡 B-47｜`stop()` 清队列不 cancel receiver 任务，重启后任务重叠【中】
- **位置**：`receivers/manager.py:70-83`
- **建议**：`stop()` 内 `for t in self._tasks: t.cancel()`。

#### 🟡 B-48｜`accept_webhook` 在 loop 不可用时静默丢数据【中】
- **位置**：`receivers/manager.py:154-158`
- **建议**：loop 不可用时把数据写本地 spool 文件，启动后回放。

#### 🟢 B-49｜`_on_data` 统计更新非原子，并发丢计数【低】
- **位置**：`receivers/manager.py:124-128`
- **建议**：`_store.update` 改支持 `total_received_delta=1` 走 SQL `UPDATE ... SET total_received = total_received + 1`。

### 2.8 LLM 客户端（`src/baize/sdk/`）

#### 🟡 B-50｜`LLMClient` 不区分限流/网络/认证失败，全部走默认 retry【中】
- **位置**：`sdk/client.py:24-25、95-102`
- **描述**：429 时反复触发更严格的限流；401 时延迟报错 3 次浪费 token 配额。
- **建议**：显式 try/except，捕获 `RateLimitError` 走指数退避（30/60/120s），`AuthenticationError` 立即抛出不重试。

#### 🟠 B-51｜LLM 工具调用 `arguments` JSON 解析失败仅吞异常，不反馈模型【高】
- **位置**：`sdk/agent.py:285-289`
- **描述**：模型生成畸形 JSON 时静默退化为"无参数调用"，工具以默认参数执行可能命中错误目标。
- **建议**：解析失败时构造 `tool_result` 错误反馈回传模型让其修正。

#### 🟡 B-52｜单条消息截断为固定字符数，未按 token 估算【中】
- **位置**：`sdk/agent.py:46` (`DEFAULT_MAX_MESSAGE_CHARS=80000`)
- **描述**：截断点可能切断多字节中文/JSON 结构，导致后续模型解析失败。
- **建议**：按 tokenizer 估算 token 数截断，截断点选在最近的语义边界。

#### 🟠 B-53｜无 LLM 响应缓存【高】
- **位置**：`sdk/agent.py`（全文件）
- **描述**：相同 prompt + 温度=0 仍重复调用，重复的 Reason/Intent 判断、相同目标二次扫描浪费 LLM 配额。
- **建议**：增加可配置的响应缓存（key=hash(messages+tools+temperature=0)），TTL 可设。

#### 🟢 B-54｜LLM provider 错误类型映射不全【低】
- **位置**：`sdk/agent.py`
- **描述**：重试逻辑仅处理 `openai.APITimeoutError/APIConnectionError/RateLimitError`，非 OpenAI provider 的错误类型未覆盖。
- **建议**：抽象 `ProviderErrorMapper`，按 provider 配置错误类型映射。

---

## 三、前端功能缺陷

### 3.1 错误处理与边界

#### 🔴 F-01｜全局无 ErrorBoundary【严重】
- **位置**：`web/src/App.tsx` L26-55；全仓无任何 `ErrorBoundary`/`componentDidCatch`
- **描述**：任何子组件渲染期抛错（如后端返回非预期数据结构、cytoscape 初始化失败）会导致整棵 React 树白屏崩溃，无法恢复。
- **影响**：单点故障——一个页面/组件异常即拖垮整个应用，用户丢失流式会话状态。
- **建议**：在 `App` 外层及各路由页外层包 `ErrorBoundary`，提供降级 UI + 重试按钮；尤其 Chat、PipelineEditor、AttackMap。

#### 🔴 F-02｜SSE 流式对话无断线重连/恢复【严重】
- **位置**：`web/src/api/client.ts` L430-568（`streamMessage`）；`web/src/pages/Chat.tsx`
- **描述**：`fetch` SSE 仅在 `catch` 中调 `onError`，无自动重连、无断点续传、无指数退避。`Browser.tsx` 的 WebSocket 有 1500ms 重连，但 SSE 没有。
- **影响**：网络抖动/代理超时/服务重启时流式中断，已生成内容丢失，用户必须手动重发，长任务无法恢复。
- **建议**：增加可配置重连（指数退避 + 最大次数），或改用支持断点续传的游标（`last-event-id`）；中断时给出明确提示与"重试"按钮。

#### 🟠 F-03｜`catch (err: any)` 统一取 `err.message`，无错误类型区分【高】
- **位置**：全仓 60+ 处（Containers/Guardrails/Reports/Settings/Sessions/Tools/Experiences/TaskArchives/Chat/AttackMap/PipelineEditor 等）
- **描述**：`err.message` 可能为 `undefined`（如 `TypeError`、`AbortError`）造成 toast 空白；网络错误不重试直接失败；401 之外的 5xx 仅弹 toast 无退避重试。
- **建议**：封装统一错误处理工具：判别 `err.name`/状态码，网络错误做有限重试，`err.message ?? '未知错误'` 兜底。

### 3.2 交互体验

#### 🔴 F-04｜长会话无虚拟列表，所有消息一次性渲染 DOM【严重】
- **位置**：`web/src/pages/Chat.tsx`（`messages.map`）；全仓无 `react-window`/`react-virtuoso`/`@tanstack/react-virtual`
- **描述**：所有消息一次性渲染 DOM，无分页/窗口化/内存释放。
- **影响**：会话数百条消息后渲染卡顿、滚动掉帧、内存持续增长；移动端更明显。
- **建议**：引入虚拟滚动（如 `@tanstack/react-virtual`），仅渲染视口内消息。

#### 🟠 F-05｜手写正则 markdown 解析 + 代码块无语法高亮、无复制按钮【高】
- **位置**：`web/src/components/ChatMessage.tsx` L205+；`web/src/pages/Reports.tsx` L504；全仓无 `highlight.js`/`prism`/`shiki`
- **描述**：手写正则解析 markdown，表格/嵌套列表/链接/图片等边缘语法易解析错乱；代码块无语言着色、无一键复制。
- **建议**：改用 `react-markdown`+`rehype` 插件 + `shiki`/`prism` 高亮；代码块加复制按钮组件。

#### 🟠 F-06｜大量列表用数组下标作 `key={i}`/`key={idx}`【高】
- **位置**：`AttackMap.tsx` L888/894/900/906/951；`Guardrails.tsx` L421/475；`PipelineEditor.tsx` L1149/1170/2248/3283/3305；`TaskArchives.tsx` L205；`Chat.tsx` L849/1131；`Sessions.tsx` L192；`Tools.tsx` L464；`Experiences.tsx` L460；`MemoryGraphView.tsx` L142/187
- **描述**：列表增删/重排时 React diff 复用错误节点，导致状态错位、输入框串值、动画错乱。
- **建议**：用稳定唯一字段（id）作 key；无 id 时基于内容哈希。

#### 🟡 F-07｜自动滚到底逻辑仅在"接近底部 120px"时触发【中】
- **位置**：`web/src/pages/Chat.tsx` L251/259/1079
- **描述**：流式中途用户上滚后新内容到达不会提示"有新消息"。
- **建议**：增加"新消息"提示按钮（点击回到底部）。

#### 🟢 F-08｜浏览器帧渲染失败被静默吞掉【低】
- **位置**：`web/src/pages/Browser.tsx` L79-97（`// 静默`）、L267 `console.debug`
- **建议**：失败计数超阈值时 toast 提示"画面传输异常"；debug 日志加 DEV 守卫。

### 3.3 构建与工程化

#### 🟠 F-09｜零测试覆盖（单元/集成/E2E 全缺）【高】
- **位置**：`web/src/` 下测试文件数为 0（`vitest.config.ts` 存在但无任何 `*.test.*`/`*.spec.*`）
- **影响**：回归无保障，重构高风险；SSE 解析、markdown 解析、坐标映射等核心逻辑无任何用例锁定。
- **建议**：至少为 `api/client.ts` 的 SSE 解析、`renderMarkdown`、Browser 坐标映射补单元测试。

#### 🟠 F-10｜无代码分割/懒加载，整应用打进单一 bundle【高】
- **位置**：`web/src/App.tsx` L6-14（全部静态 import）；全仓无 `React.lazy`/`Suspense`
- **影响**：首屏体积大，PipelineEditor（3000+ 行）、AttackMap（cytoscape）、Experiences 等重页面拖慢首屏。
- **建议**：按路由 `React.lazy` 拆分，cytoscape/markdown 等重组件动态 import。

#### 🟠 F-11｜`any` 类型泛滥，尤以 PipelineEditor 为甚【高】
- **位置**：`pages/PipelineEditor.tsx`（30+ 处 `: any`/`as any`，如 L198/268/480/835/1067/1107/1395-1408/1707-1731）；`pages/Chat.tsx` L20/47/90/229；`components/AttackMap.tsx` L279/333/381/905/950；`context/AppContext.tsx` L9/15
- **影响**：类型保护形同虚设，后端字段变更无编译期告警，运行时易出 `undefined` 错误。
- **建议**：补全 `types/index.ts` 中 Pipeline/CanvasNode/Branch 等类型，消除 `as any` 断言。

#### 🟠 F-12｜生产代码残留大量 `console.log/debug`【高】
- **位置**：`api/client.ts` L441/449/476/480/488/550/556/560/564；`pages/Chat.tsx` L544/606/693/708；`pages/Browser.tsx` L267
- **描述**：SSE 每帧、每次发送/打断都打日志。
- **影响**：控制台噪声大，泄露内部状态与会话片段；影响性能与体积。
- **建议**：接入统一日志门面或 `import.meta.env.DEV` 守卫，生产构建剔除。

#### 🟡 F-13｜网络请求无缓存/去重/竞态控制【中】
- **位置**：`web/src/api/client.ts`；`context/AppContext.tsx` L95+
- **描述**：并发多会话 SSE 时 `setMessages` 闭包捕获的 `activeSessionId` 可能与实际流不一致；切换会话瞬间消息可能写入错误会话。
- **建议**：引入轻量请求层（`@tanstack/react-query`）处理缓存/去重/竞态；SSE 写入按 session id 显式传参。

#### 🟡 F-14｜认证 token 与会话状态存 localStorage，无跨 tab 同步【中】
- **位置**：`api/client.ts` L57/61/83/109/640/646；`context/AppContext.tsx` L11/16
- **描述**：多 tab 打开时 token 失效/登出不同步（无 `storage` 事件监听）。
- **建议**：监听 `window.addEventListener('storage', …)` 同步登出；评估 token 存储方案（httpOnly cookie）。

#### 🟡 F-15｜cytoscape 图谱无大数据量虚拟化/分页【中】
- **位置**：`components/AttackMap.tsx` L544/593；`pages/PipelineEditor.tsx`；`components/MemoryGraphView.tsx`
- **描述**：节点数百+时一次性入图；ELK 布局已需超时回退。
- **建议**：对超阈值节点做分页/聚类/折叠子图；布局改异步+进度提示。

#### 🟢 F-16｜API base 优先读 localStorage 而非构建期 env【低】
- **位置**：`vite.config.ts`；`api/client.ts` L57
- **建议**：统一用 `import.meta.env.VITE_API_BASE` 作默认，localStorage 仅作覆盖。

### 3.4 UI 完整性

#### ℹ️ F-17｜无国际化框架，UI 文案全部硬编码中文【设计说明，非缺陷】
- **说明**：该系统面向中文用户设计，不做国际化是合理的工程选择。引入 i18n 框架会增加不必要的复杂度和维护成本。

#### 🟡 F-18｜无障碍覆盖严重不足【中】
- **位置**：全仓 `aria-*` 仅 5 处（`Sidebar.tsx` L95/96/103、`Layout.tsx` L31、`Browser.tsx` L462、`CreateSessionModal.tsx` L192）
- **描述**：模态无焦点陷阱、表单无 `aria-invalid`/`aria-describedby`、图标按钮多数无 `aria-label`、键盘导航不完整。
- **建议**：系统化补 aria 标签、模态焦点陷阱、`Esc` 关闭；可用 `eslint-plugin-jsx-a11y`。

#### 🟡 F-19｜无 404 页面，未知路径静默重定向到 dashboard【中】
- **位置**：`App.tsx` L49 `<Route path="*" element={<Navigate to="/dashboard" replace />} />`；`/chat` 路由渲染 null
- **建议**：提供显式 404 NotFound 页；`/chat` 路由行为加注释或显式重定向。

#### 🟡 F-20｜`dangerouslySetInnerHTML` 注入手写解析 HTML【中】
- **位置**：`components/ChatMessage.tsx` L71/167；`pages/Reports.tsx` L504
- **描述**：虽已先转义，但手写解析器对转义后再插值的边界依赖正则，后续维护一旦引入未转义分支即引入注入面。
- **建议**：评估迁移到 `react-markdown`（默认转义+安全），移除手写 HTML 注入路径。

---

## 四、跨维度缺陷

### 4.1 功能完整性缺口

#### 🟠 X-01｜无 API 限流机制【高】
- **位置**：`api/app.py`（全文件）
- **描述**：无 token bucket / fixed window / per-IP 限流。单用户高频请求可打满 LLM provider 配额。
- **建议**：接入 `slowapi` 或自实现 per-session/IP 限流中间件。

#### 🟠 X-02｜无 RBAC，所有 `/api/v1/*` 端点无角色校验【高】
- **位置**：`api/app.py`（全文件）
- **描述**：多人环境下任意调用方可触发敏感工具执行、查看他人会话历史。
- **建议**：增加认证中间件 + 角色（admin/operator/viewer）+ 端点权限矩阵。

#### ℹ️ X-03｜无任务模板/playbook 复用机制【设计说明，非缺陷】
- **说明**：该系统有意不使用预设任务模板/playbook，而是让 LLM 基于黑板状态、工具反馈和授权范围自主推理决策。这是核心设计理念——发挥 LLM 的发散性思维能力，避免固定模板限制 agent 的策略选择空间。模板化会导致 agent 行为趋同、丧失对未知场景的适应性，与"AI 自主渗透"的目标相悖。
- **设计优势**：LLM 可根据实时发现动态调整攻击路径，发现模板无法覆盖的攻击链；对未知漏洞和组合利用有更强的探索能力。

#### 🟢 X-04｜仅 human-agent 浏览器协作开关，无多用户协同编辑/共享会话【低】
- **建议**：支持 session 共享 token + 实时事件广播（基于现有 SSE 扩展）。

### 4.2 可用性与部署

#### ℹ️ X-05｜安装脚本仅 bash 实现，面向 Linux 设计【设计说明，非缺陷】
- **位置**：`setup.sh`（全文件）
- **说明**：该系统为面向 Linux 的渗透测试平台，bash 安装脚本是合理的设计选择。Windows 用户可通过 WSL 使用；macOS 下建议通过 brew 安装 bash 5.x（系统自带 bash 3.x 可能不兼容 `set -euo pipefail`）。
- **建议（可选优化）**：在 README 中明确标注"面向 Linux 设计，macOS 需 brew install bash，Windows 需 WSL"；`start.sh` 开头加 `bash --version` 检测，低于 4.x 时提示升级。

#### 🟡 X-06｜无独立配置项参考文档【中】
- **位置**：`docs/`（仅 EXTENDING.md、PLUGIN_MARKET.md、README.md）
- **描述**：环境变量、model 配置、sandbox 策略均散落代码注释，用户无法离线查阅。
- **建议**：生成 `docs/CONFIGURATION.md`，从代码中提取所有 `os.environ.get` 汇总。

#### 🟡 X-07｜仅会话级 restore，无全量备份/恢复机制【中】
- **位置**：`api/app.py:1631` (`/archives/{id}/restore`)
- **建议**：CLI 增加 `baize backup --out file.tar` / `baize restore --in file.tar`。

#### 🟡 X-08｜单进程架构，无水平扩展支持【中】
- **位置**：`api/app.py`（无集群相关代码）
- **描述**：SessionManager/ReportStore 为进程内单例 + 文件锁，多实例部署时文件锁竞争 + 内存状态不共享。
- **建议**：抽象存储层（Redis/Postgres 后端可选），状态外置后方可多副本部署。

#### 🟢 X-09｜服务管理脚本无 systemd/launchd 集成，无开机自启【低】
- **建议**：提供 `baize.service` systemd unit 文件示例。

### 4.3 性能缺陷

#### 🔴 X-10｜`Blackboard._nodes` 无节点数上限、无 LRU 淘汰、无 TTL【严重】
- **位置**：`pentest/blackboard.py:65-80`
- **描述**：长会话（>2 小时）的 Fact/Intent 节点持续累积，内存增长无界，最终 OOM 导致会话崩溃。
- **建议**：增加 `max_nodes` 配置（默认 5000），超限时触发旧 Fact 摘要压缩或落盘归档。

#### 🟡 X-11｜`BAIZE_SANDBOX_MAX_CONTAINERS=10` 全局共享，无按 session 隔离【中】
- **位置**：`pentest/container_registry.py:44,51`
- **描述**：10 个并发会话各启动 1 容器即达上限，第 11 个会话降级为本地执行。
- **建议**：改为 per-session 上限 + 全局软上限；超限时排队等待而非降级。

### 4.4 兼容性缺陷

#### 🟡 X-12｜Python 版本未 CI 矩阵验证【中】
- **位置**：`pyproject.toml` (`requires-python = ">=3.10"`)
- **描述**：使用 `match` 语法（3.10+）但 3.10 早期版本的 `str | None` 类型提示在运行时可能为 TypeError。
- **建议**：增加 CI 多版本测试矩阵；验证 3.10 实际运行。

#### 🟢 X-13｜`--network host` 在非 Linux 环境下不可用【低】
- **位置**：`executors.py:384`
- **说明**：该系统面向 Linux 设计，`--network host` 在 Linux 下是合理的默认值。仅在 macOS/WSL 等非原生 Linux 环境下可能不兼容。
- **建议（可选）**：检测非 Linux 时改用 `--network bridge` 并提示端口映射限制。

### 4.5 可维护性缺陷

#### 🔴 X-14｜测试覆盖极低，单测仅 `test_dynamic_loop.py` 1 个文件【严重】
- **位置**：全仓库
- **描述**：核心模块（executors、blackboard、reports、API 层）零回归测试，重构即破。
- **建议**：至少为 `Blackboard`/`DockerExecutor`/`ReportStore`/`stream_message` 补充单元测试。

#### 🟡 X-15｜`app.py` 单文件 133KB+，路由/SSE/会话/归档全堆【中】
- **位置**：`api/app.py`
- **影响**：文件过大难以审查，新增端点易引发冲突。
- **建议**：按域拆分：`api/routes/sessions.py`、`api/routes/reports.py`、`api/sse.py`。

#### 🟢 X-16｜关键模块内部私有方法缺类型注解【低】
- **位置**：`pentest/conversation_orchestrator.py` 等
- **建议**：补充 `_select_wave`、`_pump` 等私有方法的参数/返回类型注解。

---

## 五、优先修复路线图

### P0：立即修复（影响核心功能可用性）

| 编号 | 缺陷 | 模块 |
|---|---|---|
| B-01 | `asyncio.timeout` 在 Python 3.10 不可用 | 编排 |
| B-02 | `except BaseException` 吞掉 `CancelledError` | 编排 |
| B-08 | `DockerExecutor` 超时只杀本地客户端，容器变孤儿 | 执行器 |
| F-01 | 全局无 ErrorBoundary | 前端 |
| F-02 | SSE 流式对话无断线重连 | 前端 |
| F-04 | 长会话无虚拟列表 | 前端 |
| X-10 | Blackboard 内存无上限 | 编排 |
| X-14 | 测试覆盖极低 | 全局 |

### P1：近期修复（影响生产稳定性）

| 编号 | 缺陷 | 模块 |
|---|---|---|
| B-03 | Reason LLM 调用失败被静默降级 | 编排 |
| B-04 | alternatives 无限重置 stagnation | 编排 |
| B-09 | `DockerExecutor` 用 `shlex.split` 拆命令 | 执行器 |
| B-10 | bwrap 超时 killpg 失效 | 执行器 |
| B-15 | SSE 单飞取消漏掉 pipeline 路径 | API |
| B-16 | Webhook 重启窗口期数据丢失 | API |
| B-17 | `bind_container` 在 async 上下文调 `asyncio.run` | API |
| B-18 | 损坏 session JSON 静默吞掉 | API |
| B-19 | 附件整文件入内存 OOM | API |
| B-20 | 无 metrics 端点 | 可观测性 |
| B-21 | 日志无轮转/无结构化 | 可观测性 |
| B-31 | webhook 失败不重试 | 编排 |
| B-44 | 报告无 PDF/Word 导出 | 报告 |
| B-51 | LLM 工具 JSON 解析失败不反馈 | SDK |
| B-53 | 无 LLM 响应缓存 | SDK |
| F-09 | 前端零测试覆盖 | 前端 |
| F-10 | 无代码分割/懒加载 | 前端 |
| F-11 | `any` 类型泛滥 | 前端 |
| F-12 | 生产 console.log 残留 | 前端 |
| F-17 | ~~无国际化框架~~（设计说明，非缺陷） | 前端 |
| X-01 | 无 API 限流 | API |
| X-02 | 无 RBAC | API |

### P2：中期优化（体验与可维护性）

| 编号 | 缺陷 | 模块 |
|---|---|---|
| B-05 ~ B-07 | 编排循环优化 | 编排 |
| B-11 ~ B-14 | 执行器优化 | 执行器 |
| B-22 ~ B-30 | API 与会话优化 | API |
| B-32 ~ B-37 | 编排流水线优化 | 编排 |
| B-38 ~ B-43 | 工具与报告优化 | 工具/报告 |
| B-45 ~ B-50 | 接收器与 LLM 优化 | 接收器/SDK |
| B-52 | 消息截断改 token 估算 | SDK |
| F-03 ~ F-08 | 前端交互优化 | 前端 |
| F-13 ~ F-16 | 前端工程化优化 | 前端 |
| F-18 ~ F-20 | 前端 UI 完整性 | 前端 |
| X-06 ~ X-08 | 部署与运维 | 部署 |
| X-11 | 容器并发按 session 隔离 | 执行器 |
| X-12 | Python 版本 CI 矩阵 | 兼容性 |
| X-15 | app.py 按域拆分 | 可维护性 |

### P3：长期演进（架构升级）

| 编号 | 缺陷 | 模块 |
|---|---|---|
| B-54 | LLM provider 错误类型映射 | SDK |
| X-03 | ~~任务模板/playbook 库~~（设计说明，非缺陷） | 完整性 |
| X-04 | 多用户协同 | 完整性 |
| X-08 | 水平扩展（状态外置） | 架构 |
| X-09 | systemd 集成 | 部署 |
| X-13 | macOS/Windows 网络兼容 | 兼容性 |
| X-16 | 私有方法类型注解 | 可维护性 |

---

## 六、做得较好的点（正向确认）

为客观呈现，以下列出项目中表现良好的方面，作为正向参考：

### 6.1 安全设计
- **凭证管理**：管理员凭证用 `secrets.token_urlsafe()` 随机生成，密码哈希 PBKDF2-HMAC-SHA256 200k 迭代+随机盐
- **Webhook 校验**：`hmac.compare_digest` 防时序攻击，请求头白名单过滤敏感头
- **`.gitignore` 完备**：排除 `.env*`/`*.key`/`api_auth.json`/`*.cookie`/`.baize-data/`，git 历史无敏感提交

### 6.2 容器加固
- 严格 `--cap-drop=ALL` + `--security-opt=no-new-privileges` + `--read-only` + `--tmpfs /tmp` + pids/memory 限制
- `container_guard.py` 扫描 16 类逃逸特征（含 base64 解码归一化）
- `LocalExecutor` 默认 `bwrap --unshare-all` 真隔离，缺失时 fail-closed

### 6.3 前端亮点
- **401 统一处理**：`AuthGuard.tsx` L16-17 统一事件处理
- **Browser WebSocket 重连**：L123 有 1500ms 重连
- **取消进行中任务**：`AbortController`（Chat.tsx L547/682、Tools.tsx L485）
- **流式增量 flush**：`chunkText` 在 `[DONE]` 前 flush（client.ts L477-484）

### 6.4 业务设计
- **scope_guard.py**：从黑板 goal/origin 提取授权范围，工具层硬拦截越界目标
- **SSRF 护栏**：`guardrails.py` 默认阻断私网/回环/链路本地
- **附件防穿越**：`_safe_join` 用 `.resolve()`+`.relative_to()` 校验

---

## 七、附录：关键文件路径索引

### 后端核心
- `src/baize/pentest/conversation_orchestrator.py` — 编排循环（B-01~B-07）
- `src/baize/executors.py` — 执行器（B-08~B-14）
- `src/baize/api/app.py` — API 主入口（B-15~B-30）
- `src/baize/api/sessions.py` — 会话管理（B-17~B-18, B-25）
- `src/baize/api/attachments.py` — 附件管理（B-19, B-26~B-27）
- `src/baize/orchestration/runner.py` — 流水线 runner（B-31~B-34）
- `src/baize/orchestration/run_store.py` — RunStore（B-35）
- `src/baize/tools/registry.py` — 工具注册表（B-38）
- `src/baize/tools/custom_tools.py` — 自定义工具（B-39~B-41）
- `src/baize/reports/store.py` — 报告存储（B-42~B-44）
- `src/baize/receivers/manager.py` — 接收器管理（B-45~B-49）
- `src/baize/sdk/client.py` — LLM 客户端（B-50）
- `src/baize/sdk/agent.py` — Agent SDK（B-51~B-54）
- `src/baize/pentest/blackboard.py` — 黑板（X-10）

### 前端核心
- `web/src/App.tsx` — 路由与入口（F-01, F-19）
- `web/src/api/client.ts` — API 客户端（F-02, F-12~F-14, F-16）
- `web/src/pages/Chat.tsx` — 会话页（F-02, F-04, F-07）
- `web/src/components/ChatMessage.tsx` — 消息渲染（F-05, F-20）
- `web/src/pages/PipelineEditor.tsx` — 流水线编辑器（F-06, F-11）
- `web/src/components/AttackMap.tsx` — 攻击图（F-06, F-15）
- `web/src/pages/Browser.tsx` — 浏览器协作（F-08）

### 部署与配置
- `setup.sh` — 安装脚本（X-05）
- `start.sh`/`stop.sh` — 服务管理（X-09）
- `pyproject.toml` — Python 依赖（X-12）
- `docs/` — 文档目录（X-06）

---

*报告结束。本次评估共识别 82 项功能缺陷（去重后），其中严重 8 项、高 25 项、中 38 项、低 11 项，另含 3 项设计说明（X-03、X-05、F-17）。所有发现均基于只读源码审查，未修改任何文件。*

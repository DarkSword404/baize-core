# 白泽·智脑 (Baize) v4.0.0 文档

本目录包含白泽·智脑安全操作平台的文档与素材。

## 快速链接

- 使用指南见根目录 [README.md](../README.md)
- 沙箱镜像说明见 [../docker/baize-sandbox/README.md](../docker/baize-sandbox/README.md)
- 架构图见 [img/](img/)

## v4.0.0 版本亮点

1. **证据黑板 + 树状调度**：intent 携带 phase（recon/vuln/exploit/post）与 depends_on，
   同阶段原子任务并行、跨阶段屏障汇聚；fact 按 CVSS 式确定性评分，高威胁链优先调度。
2. **预算与空转控制**：预算按实际工具调用计数，ProgressGuard 连续零增量强制收束；
   失败语义区分预算耗尽 / 证伪 / 异常。
3. **TaskContext 单一上下文**：任务类型与预算从 plan_properties 单一构造，
   pentest/ctf/forensics 执行规则按类型裁决。
4. **会话级容器沙箱**：任务按需绑定 docker/podman 容器（cap-drop ALL + 只读根），
   scope_guard 按授权范围拦截越界访问。
5. **报告与归档**：证据黑板一键生成结构化报告，完结会话可归档/恢复。
6. **攻击地图升级**：fact 按威胁分、intent 按阶段着色。

## v3.0.0 版本亮点

1. **证据黑板 Blackboard**：origin/intent/fact 图模型，派生链可追溯。
2. **并行分支调度**：多意图同波并行、波末汇聚再规划。
3. **攻击图可视化**：Cytoscape + ELK 分层布局。

## 快速开始

```bash
./setup.sh --with-tools --with-sandbox   # 安装环境 + 安全工具 + 沙箱镜像
./start.sh                                # 启动服务（后端 8001 / 前端 5173）
./start.sh --reload                       # 开发模式（代码热重载）
./stop.sh                                 # 停止服务（--hard 强制）
```

详见根目录 [README.md](../README.md)。

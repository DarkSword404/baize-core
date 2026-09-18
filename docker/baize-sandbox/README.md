# baize-sandbox 基础镜像

白泽平台任务级容器沙箱的基础镜像。用户在「任务管理」中按需将任务与容器
绑定后，agent 的所有 shell/工具命令通过 `exec` 进入执行；未绑定容器的
任务走本地执行器（适用于告警研判等简单任务）。

> **架构变更说明（任务-容器解耦）**
>
> 自任务-容器解耦改造起，容器不再随会话自动创建，而是由用户在任务页
> 面主动点击「绑定容器」触发。简单任务默认本地运行工具，复杂任务
> （如渗透测试）按需绑定容器。容器并发上限为 10（由
> `BAIZE_SANDBOX_MAX_CONTAINERS` 控制）。

## 构建

```bash
# podman（推荐，rootless）
podman build -t baize-sandbox:base -f docker/baize-sandbox/Dockerfile .

# 或 docker
docker build -t baize-sandbox:base -f docker/baize-sandbox/Dockerfile .
```

## 预装工具

| 类别 | 工具 |
|---|---|
| 系统 | curl, wget, git, tmux, iproute2, dnsutils, netcat |
| 语言 | python3 + pip |
| 扫描 | nmap, whatweb, gobuster, ffuf |
| 爆破 | hydra |
| 漏洞 | nuclei, sqlmap |

未预装的工具（如 metasploit、masscan 等）可由 agent 在容器内直接
`apt-get install` 或下载静态二进制（容器内 root，无需 sudo）。

## 运行时安全参数（由 baize 自动注入，无需手动指定）

```
--cap-drop=ALL                      # 丢弃所有 capabilities
--security-opt=no-new-privileges    # 禁止 setuid 提权
--read-only                         # 根文件系统只读
--tmpfs /tmp                        # /tmp 走内存
--network=host                      # 渗透需访问任意目标（网络边界由 scope_guard 负责）
-v <workspace>:/workspace           # 工作区持久化
-w /workspace                       # 默认工作目录
```

**绝不**使用 `--privileged`，**绝不**挂载 `/var/run/docker.sock`。

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `BAIZE_SANDBOX` | `local` | **语义变更**：旧版设为 `container` 会全局强制每个会话起容器；新版仅作部署提示，容器绑定改为任务级用户主动触发（前端「绑定容器」按钮 / REST `POST /sessions/{id}/bind-container`）。保留该变量仅为兼容旧部署脚本检测，不再控制执行路由 |
| `BAIZE_SANDBOX_RUNTIME` | 自动探测 | 显式指定 `podman` 或 `docker` |
| `BAIZE_SANDBOX_IMAGE` | `baize-sandbox:base` | 基础镜像名 |
| `BAIZE_SANDBOX_MAX_CONTAINERS` | `10` | 任务级容器并发上限，超出时 bind-container 返回 503 |

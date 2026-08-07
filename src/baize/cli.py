"""Baize 命令行入口。

启动 Web API 服务（B/S 架构的后端）。
"""

from __future__ import annotations

import sys


def main() -> None:
    """启动 Baize 后端服务。"""
    import uvicorn

    host = "0.0.0.0"
    port = 8001
    uvicorn.run(
        "baize.api.app:create_baize_api_app",
        factory=True,
        host=host,
        port=port,
    )


if __name__ == "__main__":
    main()

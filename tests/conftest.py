"""pytest 共享夹具。

确保测试可从源码目录导入 baize（editable 安装时无需；非安装时兜底加 src 到 path）。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

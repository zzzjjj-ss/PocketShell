# SPDX-License-Identifier: MPL-2.0
"""便携入口：直接运行本文件即可（无需安装、无需 cd、**不依赖目录名**）。

用法：
    python __main__.py "你的问题"
    python __main__.py --repl

本文件位于 <包目录>/__main__.py。运行时：
1. 把包目录的父目录加入 sys.path（常规 `python -m pocketshell` 场景）；
2. 若父目录下没有名为 pocketshell 的包（目录被改名，如 pocketshell-latest），
   自动把当前目录注册为 "pocketshell" 包，因此从任何目录、任何目录名下都能运行。
"""

import importlib.util
import os
import sys

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_PKG_DIR)

if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 兜底：把当前目录注册为 "pocketshell" 包（无条件，只要它确实是包）。
# 不检查父目录下是否有别的 agent 目录——那可能是旧版残留或残缺目录，
# 直接跑 __main__.py 时，本目录就是真正的包，注册它永远正确。
if "pocketshell" not in sys.modules:
    init_py = os.path.join(_PKG_DIR, "__init__.py")
    if os.path.isfile(init_py):
        spec = importlib.util.spec_from_file_location("pocketshell", init_py)
        module = importlib.util.module_from_spec(spec)
        module.__path__ = [_PKG_DIR]  # 使 pocketshell.cli 等子模块可被 import
        sys.modules["pocketshell"] = module
        spec.loader.exec_module(module)

from pocketshell.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

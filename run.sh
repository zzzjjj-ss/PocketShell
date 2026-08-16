#!/usr/bin/env bash
# 便携启动脚本：直接运行 pocketshell/__main__.py（自动定位包，不改变调用者目录）
exec python3 "$(dirname "$0")/pocketshell/__main__.py" "$@"

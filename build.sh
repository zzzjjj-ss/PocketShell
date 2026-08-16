#!/bin/sh
# ============================================================
# PocketShell 构建脚本(可复现发布)
# 产出(在工作区根):
#   pocketshell.zip          便携版 - 需系统 Python 3.10+,Windows/Linux/macOS
#   pocketshell-green.zip    GREEN 版 - 内置 Windows Python 3.13,仅 Windows
# 用法: sh build.sh
# ============================================================
set -e
cd "$(dirname "$0")"
ROOT=$(pwd)
BUILD="$ROOT/_build"
rm -rf "$BUILD"
mkdir -p "$BUILD"

echo "[1/3] 便携版 pocketshell.zip ..."
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
rm -f "$ROOT/pocketshell.zip"
zip -r "$ROOT/pocketshell.zip" \
    run.bat run.sh README.md LICENSE pyproject.toml pocketshell setup \
    -x '*/__pycache__/*' '*.pyc' 'pocketshell/tests/*' > /dev/null

echo "[2/3] 下载/解压 Windows embeddable Python 3.13.15 ..."
EMBED_VER=3.13.15
EMBED="python-${EMBED_VER}-embed-amd64.zip"
if [ ! -f "$BUILD/$EMBED" ]; then
    curl -sL -m 300 -o "$BUILD/$EMBED" \
        "https://mirrors.huaweicloud.com/python/${EMBED_VER}/${EMBED}" \
        || curl -sL -m 300 -o "$BUILD/$EMBED" \
        "https://www.python.org/ftp/python/${EMBED_VER}/${EMBED}"
fi

GREEN="$BUILD/green"
mkdir -p "$GREEN/pocketshell" "$GREEN/setup" "$GREEN/python"
unzip -q "$BUILD/$EMBED" -d "$GREEN/python"
cp pocketshell/*.py "$GREEN/pocketshell/"
cp README.md LICENSE pyproject.toml "$GREEN/"
cp setup/install.bat setup/uninstall.bat setup/uninstall.ps1 "$GREEN/setup/"

echo "[3/3] green 专用资产 + 打包 pocketshell-green.zip ..."
python3 tools/green_assets.py "$GREEN"
find "$GREEN" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
rm -f "$ROOT/pocketshell-green.zip"
(cd "$BUILD" && zip -r "$ROOT/pocketshell-green.zip" green \
    -x '*/__pycache__/*' '*.pyc' > /dev/null)

rm -rf "$BUILD"
echo "完成:"
ls -la "$ROOT/pocketshell.zip" "$ROOT/pocketshell-green.zip"

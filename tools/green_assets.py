#!/usr/bin/env python3
"""生成 GREEN 版专用资产：install.ps1(捆绑 python 模板)、green.bat、README 头部。

用法: python3 tools/green_assets.py <green目录>
"""
import sys
from pathlib import Path

BOM = "\ufeff"

GREEN_INSTALL_PS1 = BOM + """# install.ps1 - PocketShell GREEN installer (uses bundled python)
# Generates <CmdName>.cmd, adds setup folder to CURRENT USER's PATH.
param([string]$CmdName = 'pocketshell')

if ($CmdName -notmatch '^[A-Za-z0-9_-]+$') {
    Write-Host '命令名只能包含字母/数字/下划线/连字符，请重试。' -ForegroundColor Red
    exit 1
}

$setupDir = (Split-Path -Parent $MyInvocation.MyCommand.Path).TrimEnd('\\\\')
$cmdFile  = Join-Path $setupDir ($CmdName + '.cmd')

# GREEN 版入口：直接使用捆绑的 python\\python.exe
$content = @'
@echo off
rem PocketShell entry (GREEN) - uses bundled python
setlocal
chcp 65001 >nul
set PYTHONDONTWRITEBYTECODE=1
"%~dp0..\\python\\python.exe" "%~dp0..\\pocketshell\\__main__.py" %*
endlocal
'@
[System.IO.File]::WriteAllText($cmdFile, $content + \"`r`n\", (New-Object System.Text.ASCIIEncoding))

$p = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($null -eq $p) { $p = '' }
$items = @($p -split ';' | Where-Object { $_ -ne '' })
if ($items -contains $setupDir) {
    Write-Host '[PocketShell] 目录已在用户 PATH 中，无需重复添加。'
} else {
    $items += $setupDir
    [Environment]::SetEnvironmentVariable('Path', ($items -join ';'), 'User')
    Write-Host ('[PocketShell] 已加入用户 PATH：' + $setupDir)
}
Write-Host ('命令入口已生成：' + $CmdName + '.cmd')
Write-Host ('请新开一个终端，然后输入 ' + $CmdName + ' 试试，例如：' + $CmdName + ' 查看当前目录')
"""

GREEN_BAT = (
    "@echo off\r\n"
    "rem PocketShell GREEN - bundled Python 3.13, zero install\r\n"
        "setlocal\r\n"
    "chcp 65001 >nul\r\n"
    "set PYTHONDONTWRITEBYTECODE=1\r\n"
    '"%~dp0python\\python.exe" "%~dp0pocketshell\\__main__.py" %*\r\n'
    "endlocal\r\n"
)

GREEN_README_HEAD = """# PocketShell — GREEN 绿色版(内置 Python 3.13,真·零安装)

> **⚠️ 仅限 Windows**:内置的是 Windows 版 Python,**Linux / macOS 请用便携版源码**。
> 本目录已**内置 Python 3.13.15 解释器**(`python\\` 子目录),机器上不需要装任何东西:
> 不用装 Python、不用 pip、不写注册表、不改系统 PATH、不碰 %APPDATA%,
> 全部文件(程序 + Python + 配置 + 会话 + 记忆)都在本目录内,拷走即迁移。
>
> - 启动:双击 `green.bat`,或在终端运行 `green.bat "你的问题"`
> - 装进 PATH:双击 `setup\\install.bat`,输入想要的命令名(如 `g`),新终端里 `g "问题"` 即用
> - 体积约 40MB(含 Python),换取"任何 Windows 机器开箱即用"
> - 内置 Python 版本固定为 3.13.15,不随系统更新;需要新版本时重新下载本包
> - 下方文档其余内容与便携版一致

---

"""


def main() -> None:
    green_dir = Path(sys.argv[1])
    (green_dir / "setup").mkdir(parents=True, exist_ok=True)
    (green_dir / "setup" / "install.ps1").write_text(GREEN_INSTALL_PS1, encoding="utf-8")
    (green_dir / "green.bat").write_text(GREEN_BAT, encoding="ascii")
    readme = green_dir / "README.md"
    src = readme.read_text(encoding="utf-8")
    src = GREEN_README_HEAD + src
    readme.write_text(src, encoding="utf-8")
    print("green assets written to", green_dir)


if __name__ == "__main__":
    main()

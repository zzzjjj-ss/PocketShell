# SPDX-License-Identifier: MPL-2.0
"""命令安全层：代码级硬拦截，不依赖模型自觉。

分级判定：
- BLOCK   ：删除/清空/格式化/权限破坏类指令 —— 直接拒绝，绝不执行。
- CONFIRM ：高危但非删除类（关机、卸载、改权限等）—— 需用户输入 y 确认。
- ALLOW   ：放行。

防绕过检测：
- PowerShell -EncodedCommand / -enc / base64 解码执行
- Invoke-Expression (iex) 动态执行
- 变量间接调用（& $var）
- cmd /c 或 powershell -c 嵌套（递归分析内嵌命令一次）

注意：本层是尽力而为的静态分析，不是完整沙箱；配合系统提示词约束与执行前确认，
三层防护降低风险，但无法 100% 防御任意混淆，使用时仍建议在受控环境。
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from typing import List, Optional

BLOCK = "block"
CONFIRM = "confirm"
ALLOW = "allow"


@dataclass
class SafetyResult:
    verdict: str = ALLOW
    reason: str = ""
    matches: List[str] = field(default_factory=list)
    # 类别："" 普通危险 / "write" 写文件操作（受 FILE_WRITE_CONFIRM 独立开关控制）
    category: str = ""


# ---------------------------------------------------------------------------
# 规则表：每条规则 = (正则, 判定, 说明)。正则对整段命令做大小写不敏感匹配。
# 顺序敏感：先匹配的更优先（BLOCK > CONFIRM）。
# ---------------------------------------------------------------------------

# -- BLOCK：删除 / 清空 / 格式化 / 注册表删除 / 计划任务删除 / 服务删除等 --

BLOCK_RULES: List[tuple] = [
    # PowerShell 编码命令 / 动态执行 / 变量间接调用 —— 无法静态分析，一律拦截
    (r"-EncodedCommand\b|-enc\b", "检测到 PowerShell 编码命令（-EncodedCommand），无法静态分析，已拦截"),
    (r"Invoke-Expression|\biex\b", "检测到 Invoke-Expression (iex) 动态执行，已拦截"),
    (r"&[\s]*\$", "检测到变量间接调用（& $var），可能绕过安全检查，已拦截"),
    (r"FromBase64String", "检测到 Base64 解码执行，可能隐藏危险命令，已拦截"),
    # 文件/目录删除（cmd 与 PowerShell 别名、bash）
    (r"(?:^|[;&|(]|\s)(?:del|erase|deltree)(?:\s|$)", "检测到删除指令（del/erase/deltree）"),
    (r"(?:^|[;&|(]|\s)(?:rm|rmdir|rd|unlink)\b", "检测到删除指令（rm/rmdir/rd/unlink）"),
    (r"Remove-Item\b", "检测到 Remove-Item 删除指令"),
    (r"Remove-ItemProperty\b", "检测到 Remove-ItemProperty 删除注册表/属性"),
    (r"Remove-Service\b|Remove-WindowsCapability\b", "检测到服务/组件删除指令"),
    # 清空内容 / 回收站
    (r"Clear-Content\b|Clear-Item\b|Clear-RecycleBin\b", "检测到清空指令（Clear-Content/Clear-Item/Clear-RecycleBin）"),
    # 格式化 / 磁盘清理
    (r"Format-Volume\b|format(?![-_](?:table|list|wide|custom))(?:\s|$)", "检测到磁盘格式化指令"),
    (r"diskpart[\s\S]*\bclean\b", "检测到 diskpart clean（清盘）指令"),
    # 注册表删除
    (r"\breg\s+delete\b", "检测到注册表删除指令（reg delete）"),
    # 计划任务删除
    (r"\bschtasks\b[^\n]*/delete\b|Unregister-ScheduledTask\b", "检测到计划任务删除指令"),
    # WMI / 服务删除
    (r"\bwmic\b[^\n]*\bdelete\b|\bsc\s+delete\b|\bRemove-Service\b", "检测到服务/WMI 删除指令"),
    # 用户/账户删除
    (r"\bnet\s+user\b[^\n]*/delete\b|\bnet\s+localgroup\b[^\n]*/delete\b", "检测到用户账户删除指令"),
    # 安全擦除
    (r"\bshred\b", "检测到安全擦除指令（shred）"),
    # 危险通配符删除（-rf / -Recurse -Force / /s /q 组合删除）
    (r"\brm\b[^\n]*-r[^\n]*f\b|\brmdir\b[^\n]*/s\b[^\n]*/q\b", "检测到递归强制删除（rm -rf / rmdir /s /q）"),
    (r"\bRemove-Item\b[^\n]*-(?:Recurse|Force)", "检测到递归强制删除（Remove-Item -Recurse/-Force）"),
]

# -- CONFIRM：高危但非删除类 --

CONFIRM_RULES: List[tuple] = [
    (r"\b(?:shutdown|restart|reboot|logoff)\b", "关机/重启/注销指令"),
    (r"\btaskkill\b|\bStop-Process\b", "强制结束进程指令"),
    (r"\bnet\s+user\b|\bnet\s+localgroup\b", "用户/组管理指令"),
    (r"\bsc\s+stop\b|\bStop-Service\b", "停止服务指令"),
    (r"\bschtasks\b|\bRegister-ScheduledTask\b", "计划任务管理指令"),
    (r"\bdiskpart\b", "磁盘管理指令（diskpart）"),
    (r"\bicacls\b|\btakeown\b", "文件权限/所有权修改指令"),
    (r"\bchmod\b|\bchown\b", "文件权限/所有者修改指令（Linux）"),
    (r"\bmsiexec\b[^\n]*/uninstall\b|\buninstall\b", "卸载程序指令"),
    (r"\bSet-ExecutionPolicy\b", "修改 PowerShell 执行策略"),
    (r"\breg\s+add\b", "修改注册表指令"),
    (r"\bformat\b(?![-_](?!volume)[A-Za-z])", "格式化相关指令（非删除匹配兜底）"),
    (r"\bStart-Process\b|\bInvoke-Item\b", "启动程序/打开文件指令"),
    (r"\bmklink\b|\bNew-Item\b[^\n]*-ItemType\s+SymbolicLink", "创建符号链接指令"),
    (r"\b(?:pip|npm|gem|apt|winget|choco)\s+uninstall\b", "卸载软件包指令"),
]

# -- 写文件操作（CONFIRM，受 FILE_WRITE_CONFIRM 开关控制；记忆文件 memory.txt 不受影响） --
# 创建/覆盖/追加/移动/重命名/复制文件都属于"修改文件"，执行前需用户确认。
# 注意：删除类（Remove-Item 等）已在上方 BLOCK，不会降级到确认。

WRITE_RULES: List[tuple] = [
    (r">>", "写文件操作（追加写入）"),
    # 单 > 重定向：前面是命令分隔符/空格，后面不是 = 或 &（排除 2>&1、>= 等）
    (r"(?:^|[;&|\s])[12]?>(?![=&])", "写文件操作（覆盖写入）"),
    (r"Set-Content\b|Add-Content\b|Out-File\b|Set-Item\b", "写文件操作（PowerShell 写入）"),
    (r"New-Item\b|Copy-Item\b|Move-Item\b|Rename-Item\b", "写文件操作（创建/复制/移动/重命名）"),
    (r"\bcopy\b|\bxcopy\b|\brobocopy\b|\breplace\b", "写文件操作（复制文件）"),
    (r"\bmove\b|\bren(?:ame)?\b", "写文件操作（移动/重命名文件）"),
]


def _split_segments(command: str) -> List[str]:
    """按命令分隔符切分，便于识别命令边界（避免误伤参数中的单词）。"""
    parts = re.split(r";|\|\||&&|\n", command)
    segments = []
    for p in parts:
        p = p.strip()
        if p:
            segments.append(p)
    return segments


def _strip_quotes(s: str) -> str:
    return s.strip("\"'")


# ---------------------------------------------------------------------------
# 自毁防护：禁止 agent 删除/修改自己所在目录（防止把自己改死）。
# 触发条件：命令文本包含 agent 目录完整路径，或当前工作目录就在 agent 目录内，
# 且命令命中破坏性/修改性动词。
# ---------------------------------------------------------------------------

# 破坏性动词（命中即 BLOCK，无论目标是否明确）
_SELF_DESTRUCTIVE = [
    r"(?:^|[;&|(]|\s)(?:del|erase|deltree)(?:\s|$)",
    r"(?:^|[;&|(]|\s)(?:rm|rmdir|rd|unlink)\b",
    r"Remove-Item\b",
    r"Clear-Content\b|Clear-Item\b|Clear-RecycleBin\b",
    r"Format-Volume\b|format(?![-_](?:table|list|wide|custom))(?:\s|$)",
    r"diskpart[\s\S]*\bclean\b",
    r"\bshred\b",
]

# 修改性动词（移动/重命名/覆盖写入，同样会改死 agent）
_SELF_MODIFY = [
    r"(?:^|[;&|(]|\s)(?:move|ren|rename|mv)(?:\s|$)",
    r"Move-Item\b|Rename-Item\b",
    r"Set-Content\b|Add-Content\b|Out-File\b",
    r">",  # 重定向覆盖写入
]


def _is_inside(path: str, parent: str) -> bool:
    """判断 path（解析后）是否等于或在 parent 目录内。"""
    try:
        p = os.path.realpath(path)
        q = os.path.realpath(parent)
        return p == q or p.startswith(q + os.sep)
    except OSError:
        return False


def _check_self_dir(command: str, cwd: Optional[str]) -> Optional[SafetyResult]:
    """检查命令是否针对项目自身目录；命中返回 BLOCK 结果，否则 None。

    保护范围是整个项目根（ROOT_DIR）：包程序、run.bat、config.json、sessions 等
    都在其中——删掉任何一个都可能让 PocketShell 失效，因此一律拦截。
    """
    from .config import ROOT_DIR

    project_dir = str(ROOT_DIR)
    norm_cmd = command.replace("\\", "/").lower()
    norm_project = project_dir.replace("\\", "/").lower()
    in_self_text = norm_project in norm_cmd
    in_self_cwd = bool(cwd) and _is_inside(cwd, project_dir)
    if not (in_self_text or in_self_cwd):
        return None

    if in_self_text:
        # 命令明确指向项目目录内文件：删除/修改一律硬拦
        for pattern in _SELF_DESTRUCTIVE:
            if re.search(pattern, command, re.IGNORECASE):
                return SafetyResult(
                    verdict=BLOCK,
                    reason="禁止删除/清空/格式化项目目录下的文件（防止 PocketShell 被改死）",
                    matches=[pattern],
                )
        for pattern in _SELF_MODIFY:
            if re.search(pattern, command, re.IGNORECASE):
                return SafetyResult(
                    verdict=BLOCK,
                    reason="禁止移动/重命名/覆盖项目目录下的文件（防止 PocketShell 被改死）",
                    matches=[pattern],
                )
        return None

    # 仅 cwd 在项目目录内（命令无明确路径）：删除类硬拦（Remove-Item * 会清空自身）；
    # 写操作交给 FILE_WRITE_CONFIRM 确认层，避免误伤"在项目目录里新建文件"。
    for pattern in _SELF_DESTRUCTIVE:
        if re.search(pattern, command, re.IGNORECASE):
            return SafetyResult(
                verdict=BLOCK,
                reason="禁止在项目目录内执行删除/清空操作（防止 PocketShell 被改死）",
                matches=[pattern],
            )
    return None


def analyze_command(command: str, cwd: Optional[str] = None) -> SafetyResult:
    """分析命令，返回判定结果。cwd 传当前工作目录以识别 agent 目录内操作。"""
    if not command or not command.strip():
        return SafetyResult(verdict=ALLOW)

    # 自毁防护优先（命中即 BLOCK，不继续走其它规则）
    self_result = _check_self_dir(command, cwd)
    if self_result is not None:
        return self_result

    result = SafetyResult()

    # 先整体跑 BLOCK 规则（覆盖跨段模式，如 diskpart clean）
    for pattern, reason in BLOCK_RULES:
        if re.search(pattern, command, re.IGNORECASE):
            result.verdict = BLOCK
            result.reason = reason
            result.matches.append(pattern)
            return result

    # 嵌套命令递归分析（cmd /c "..." 或 powershell -c "..."）
    nested = re.findall(
        r'(?:cmd\s*/c|powershell(?:\.exe)?\s*(?:-c|-command|/c))\s*["\']([^"\']+)["\']',
        command,
        re.IGNORECASE,
    )
    for inner in nested:
        inner_result = analyze_command(_strip_quotes(inner))
        if inner_result.verdict == BLOCK:
            return inner_result
        if inner_result.verdict == CONFIRM and result.verdict != BLOCK:
            result.verdict = CONFIRM
            result.reason = inner_result.reason
            result.category = inner_result.category

    # 再按段跑 BLOCK 规则（命令边界更精确）
    for segment in _split_segments(command):
        for pattern, reason in BLOCK_RULES:
            if re.search(pattern, segment, re.IGNORECASE):
                result.verdict = BLOCK
                result.reason = reason
                result.matches.append(pattern)
                return result

    # CONFIRM 规则（整段匹配）
    for pattern, reason in CONFIRM_RULES:
        if re.search(pattern, command, re.IGNORECASE):
            if result.verdict == ALLOW:
                result.verdict = CONFIRM
                result.reason = reason
                result.matches.append(pattern)

    # 写文件操作（CONFIRM 独立类别，受 FILE_WRITE_CONFIRM 开关控制）。
    # BLOCK 优先级更高：删除类命令不会走到这里。
    for pattern, reason in WRITE_RULES:
        if re.search(pattern, command, re.IGNORECASE):
            if result.verdict != BLOCK:
                result.verdict = CONFIRM
                result.reason = reason
                result.category = "write"
                result.matches.append(pattern)
            return result
    return result


def block_reply(command: str, result: SafetyResult) -> str:
    """BLOCK 判定时返回给模型的固定回复（同时写入系统提示词约定的格式）。"""
    return (
        f"命令已被安全策略拦截，未执行：`{command}`\n"
        f"原因：{result.reason}\n"
        "请停止尝试执行删除/破坏性指令，改用只读操作或询问用户手动执行。"
    )


def confirm_prompt(command: str, reason: str) -> str:
    return (
        f"⚠️ 该命令属于高危操作（{reason}），请确认是否执行：\n"
        f"  {command}\n"
        "输入 y 执行，其它任意输入取消: "
    )

# SPDX-License-Identifier: MPL-2.0
"""通用工具：Windows 检测、shell 识别、token 估算、文本截断、标准库 HTTP。"""

from __future__ import annotations

import os
import platform
import re
import shlex
import subprocess
import urllib.error
import urllib.request
from typing import List, Optional, Tuple

# ---------------- 平台检测 ----------------

IS_WINDOWS = platform.system() == "Windows"


def _console_processes() -> List[int]:
    """返回附加到当前控制台的所有进程 PID（Windows，GetConsoleProcessList）。"""
    if not IS_WINDOWS:
        return []
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        pid_list = (wintypes.DWORD * 32)()
        n = kernel32.GetConsoleProcessList(pid_list, 32)
        return [int(pid_list[i]) for i in range(n)]
    except Exception:
        return []


def _pid_to_name(pid: int) -> str:
    """把 PID 映射为可执行文件名（小写），失败返回空串。"""
    if not IS_WINDOWS:
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value.replace("\\", "/").split("/")[-1].lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass
    return ""


def _decide_shell(console_pids: List[int], my_pid: int) -> str:
    """从控制台进程列表判定用户实际使用的 shell（纯逻辑，便于测试）。

    规则：控制台进程 = 用户 shell + 我们自身（及其子进程）。从列表里排除
    自身与无关进程，优先取 cmd.exe / powershell.exe / pwsh.exe。
    - 命中 powershell.exe / pwsh.exe → "powershell.exe"
    - 命中 cmd.exe → "cmd.exe"
    - 都未命中 → 回退 "cmd.exe"（Windows 兜底，与入口脚本一致）
    """
    for pid in console_pids:
        if pid == my_pid:
            continue
        name = _pid_to_name(pid)
        if name in ("powershell.exe", "pwsh.exe"):
            return "powershell.exe"
    for pid in console_pids:
        if pid == my_pid:
            continue
        name = _pid_to_name(pid)
        if name == "cmd.exe":
            return "cmd.exe"
    return "cmd.exe"


def detect_shell() -> str:
    """返回命令执行使用的 shell 名称：powershell.exe / cmd.exe / bash / zsh ...

    关键修正：旧的 PSModulePath 段数判定在 cmd 下恒为 >=3，把 cmd 用户误判成
    PowerShell，导致提示词说一套、实际执行另一套，模型只能盲试语法（一会儿
    Get-Item 一会儿 dir /x）。现在改为检测**当前控制台宿主进程**：
    GetConsoleProcessList 拿到的进程列表必然包含用户实际在用的 shell，
    据此返回 cmd.exe 或 powershell.exe——提示词与执行层保持一致，
    模型不再需要猜语法。
    注：返回值与 run_command 的执行方式必须一致，且会展示在系统提示词里。
    """
    if IS_WINDOWS:
        return _decide_shell(_console_processes(), os.getpid())
    return os.path.basename(os.environ.get("SHELL", "/bin/sh"))


def describe_os() -> str:
    """返回用于系统提示词的操作系统描述。"""
    if IS_WINDOWS:
        return f"Windows {platform.release()}"
    return platform.system()


# ---------------- token 估算 ----------------

# 中文/全角字符按 1 个 token 计（实际约 0.6-1），英文约 4 字符 1 token。
# 这是保守估算，用于预算控制，宁可低估 token 数（即高估用量）以保安全。
def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    # CJK 与全角字符
    cjk = len(re.findall(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", text))
    other = len(text) - cjk
    return cjk + max(1, other // 3)


def estimate_messages_tokens(messages: List[dict]) -> int:
    total = 0
    for m in messages:
        total += estimate_tokens(m.get("content") or "")
        # 工具调用参数
        for tc in m.get("tool_calls") or []:
            total += estimate_tokens(tc.get("function", {}).get("arguments", ""))
    return total


# ---------------- 文本截断 ----------------

def truncate_text(text: str, max_chars: int = 2000, ellipsis: str = "\n…[输出已截断]") -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + ellipsis


# ---------------- 命令执行 ----------------

def run_command(command: str, timeout: int = 60) -> Tuple[int, str]:
    """在用户 shell 中执行命令，返回 (exit_code, output)。

    Windows 下按 detect_shell() 检测结果执行：用户终端是 PowerShell 就走
    powershell.exe，是 cmd 就走 cmd.exe /d /c（与系统提示词、工具描述保持一致，
    避免"提示说一套、实际执行另一套"的语义错乱）。执行方式与用户手动在终端
    中运行完全一致，不附加任何编码前缀；输出以 UTF-8 优先解码，失败回退本地编码。
    """
    if IS_WINDOWS:
        shell_name = detect_shell()
        if shell_name == "powershell.exe":
            # -NoProfile 避免用户 profile 干扰；-NonInteractive 防止交互挂起；-Command 执行
            full = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            # cmd：/d 忽略 autorun 干扰；/c 执行后退出。
            # 注意：不加 chcp 等任何前缀，保持与用户手动在 cmd 中执行完全一致。
            full = ["cmd.exe", "/d", "/c", command]
    else:
        full = [os.environ.get("SHELL", "/bin/sh"), "-c", command]

    try:
        proc = subprocess.run(
            full,
            capture_output=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return -1, f"命令执行超时（>{timeout}s）"
    except OSError as e:
        return -1, f"命令启动失败: {e}"

    output = proc.stdout or b""
    if proc.stderr:
        output += b"\n[stderr]\n" + proc.stderr
    # Windows 下 PowerShell 默认输出可能为 UTF-16LE 或 GBK，逐级尝试解码
    text = decode_output(output)
    return proc.returncode, text


def decode_output(data: bytes) -> str:
    for enc in ("utf-8", "utf-16-le", "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


# ---------------- 标准库 HTTP（零第三方依赖，替代 requests） ----------------

def http_get(
    url: str,
    timeout: int = 15,
    headers: Optional[dict] = None,
) -> str:
    """GET 请求并返回解码后的文本。

    编码优先取响应头 Content-Type 的 charset，缺省 utf-8。
    抛 urllib.error.URLError / HTTPError（调用方按需捕获）。
    """
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    charset = None
    try:
        charset = resp.headers.get_content_charset()
    except Exception:
        charset = None
    if not charset:
        # 无 charset 时先用 utf-8，失败回退 gbk（Bing 中文页可能用 gbk）
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", errors="replace")
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")

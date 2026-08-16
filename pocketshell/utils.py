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


def detect_shell() -> str:
    """返回实际使用的 shell 名称：powershell.exe / cmd.exe / bash / zsh ..."""
    if IS_WINDOWS:
        # 与上游一致：PSModulePath 中目录数 >= 3 判定为 PowerShell
        ps_path = os.environ.get("PSModulePath", "")
        if len(ps_path.split(os.pathsep)) >= 3:
            return "powershell.exe"
        return "cmd.exe"
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

    Windows 下自动选择 PowerShell / cmd，输出以 UTF-8 优先解码，失败回退本地编码。
    """
    if IS_WINDOWS:
        shell_name = detect_shell()
        if shell_name == "powershell.exe":
            # -NoProfile 避免用户 profile 干扰；-Command 执行
            full = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            full = ["cmd.exe", "/c", command]
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

# SPDX-License-Identifier: MPL-2.0
"""终端彩色 Markdown 渲染（纯标准库，无第三方依赖）。

- enable_ansi()   ：Windows 下通过 ctypes 启用 VT 转义序列（cmd/PowerShell 均可）。
- supports_color()：是否应该上色（stdout 为终端且未设置 NO_COLOR）。
- MarkdownStreamRenderer：流式渲染器，逐块喂入模型输出，实时着色。
  处理：``` 代码块（含语言标记）、行内 `code`、行首 # 标题、- 列表符号。
  流式下无法预知后文，粗体 **x** 不处理（保留原文）；代码块边界与行内反引号用状态机精确切换。
- render_block()  ：整块渲染（标题/行内码/粗体/代码块），用于非流式场景。

颜色约定：代码块=青色，标题=亮青色加粗，行内码=青色，列表符号=灰色。
"""

from __future__ import annotations

import os
import re
import sys

try:
    import ctypes
except ImportError:  # 非 Windows / 极简环境
    ctypes = None

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GRAY = "\033[90m"

_vt_state: bool | None = None


def _enable_vt() -> bool:
    """启用 Windows 控制台的 VT 转义；非 Windows 视为支持。"""
    if os.name != "nt" or ctypes is None:
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(
                handle, mode.value | 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            )
            return True
    except Exception:
        pass
    return False


def enable_ansi() -> bool:
    """供 CLI 在启动时调用；返回 VT 是否可用。"""
    global _vt_state
    if _vt_state is None:
        _vt_state = _enable_vt()
    return _vt_state


def supports_color() -> bool:
    """是否应该输出颜色：终端 + 未禁用颜色 + VT 可用。"""
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    return enable_ansi()


class MarkdownStreamRenderer:
    """流式 Markdown 着色器：feed(chunk) → 着色后的文本。

    普通字符即时输出（终端不卡顿）；行首特殊标记（```、#、-、*、`）累积整行
    判断后再渲染；行内反引号在普通流中即时切换颜色。
    """

    def __init__(self, color: bool = True) -> None:
        self.color = color
        self.in_code = False  # 是否在 ``` 代码块内
        self.inline = False  # 是否在行内 `code` 中
        self.line = ""  # 行首特殊标记的累积缓冲
        self.line_started = False  # 当前行是否已输出过普通字符

    def feed(self, text: str) -> str:
        if not self.color:
            return text
        out = []
        for ch in text:
            if ch == "\n":
                if self.line:
                    out.append(self._render_line(self.line))
                    self.line = ""
                else:
                    out.append("\n")
                self.line_started = False
                continue
            if self.line:
                # 行首特殊标记累积中：整行缓冲到换行再渲染
                self.line += ch
                continue
            if self.in_code:
                # 代码块内：内容原样输出（颜色由进入代码块时保持）；
                # 行首反引号累积，用于识别 ``` 闭合行
                if not self.line_started and ch == "`":
                    self.line += ch
                else:
                    out.append(ch)
                    self.line_started = True
                continue
            if ch == "`":
                if not self.line_started:
                    # 行首反引号：可能是 ``` 代码块，累积判断
                    self.line += ch
                else:
                    self.inline = not self.inline
                    out.append(RESET if not self.inline else CYAN)
                continue
            if not self.line_started and ch in "#-*":
                # 行首 # 标题 / - * 列表（或粗体）：累积整行判断
                self.line += ch
                continue
            if self.inline:
                out.append(ch)  # 行内代码内容（颜色已由 CYAN 保持）
                continue
            out.append(ch)
            self.line_started = True
        return "".join(out)

    def reset(self) -> str:
        """输出结束：flush 残留行，复位未闭合的颜色。"""
        out = []
        if self.line:
            out.append(self._render_line(self.line))
            self.line = ""
        if self.inline:
            self.inline = False
            out.append(RESET)
        if self.in_code:
            self.in_code = False
            out.append(RESET)
        return "".join(out)

    def _render_line(self, line: str) -> str:
        stripped = line.strip()
        # 代码块定界符：``` 或 ```lang
        if stripped.startswith("```"):
            self.in_code = not self.in_code
            if self.in_code:
                return CYAN + line
            return line + RESET
        if self.in_code:
            return CYAN + line
        # 行内反引号：交替上色（split 奇数段为代码）
        if "`" in line:
            parts = line.split("`")
            colored = []
            for i, part in enumerate(parts):
                if i % 2 == 1:
                    colored.append(CYAN + part + RESET)
                else:
                    colored.append(part)
            line = "".join(colored)
        # 行首标题：### 标题
        m = re.match(r"^(\s*)(#{1,6})\s+(.+)$", line)
        if m:
            return m.group(1) + BOLD + CYAN + m.group(3) + RESET
        # 列表符号：- / * / +
        m2 = re.match(r"^(\s*)([-*+])\s+", line)
        if m2:
            return m2.group(1) + GRAY + m2.group(2) + RESET + line[m2.end():]
        return line


_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")


def render_block(text: str, color: bool = True) -> str:
    """整块渲染 Markdown（非流式）：代码块、行内码、粗体、标题、列表。"""
    if not color:
        return text
    out = []
    in_code = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            out.append(CYAN + line + (RESET if not in_code else ""))
            continue
        if in_code:
            out.append(CYAN + line)
            continue
        line = _INLINE_CODE.sub(lambda m: CYAN + m.group(1) + RESET, line)
        line = _BOLD.sub(lambda m: BOLD + m.group(1) + RESET, line)
        m = re.match(r"^(\s*)(#{1,6})\s+(.+)$", line)
        if m:
            out.append(m.group(1) + BOLD + CYAN + m.group(3) + RESET)
            continue
        m2 = re.match(r"^(\s*)([-*+])\s+", line)
        if m2:
            out.append(m2.group(1) + GRAY + m2.group(2) + RESET + line[m2.end():])
            continue
        out.append(line)
    joined = "\n".join(out)
    if in_code:
        joined += RESET
    return joined

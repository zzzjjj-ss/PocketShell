# SPDX-License-Identifier: MPL-2.0
"""工具定义与执行：execute_shell / remember / recall / fetch_url / web_search。

每个工具 = {"name", "description", "parameters"(JSON Schema), "handler"(callable)}
handler 返回字符串，会回传给模型（经截断控制，省 token）。
"""

from __future__ import annotations

import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Dict, List

from . import safety
from .config import AGENT_DIR, cfg
from .utils import IS_WINDOWS, http_get, truncate_text

# ---------------- 记忆文件（默认 agent 目录下，便携；可在配置中改路径） ----------------

def _memory_file() -> Path:
    return Path(cfg.get("MEMORY_FILE"))


def _remember(info: str) -> str:
    mem = _memory_file()
    try:
        mem.parent.mkdir(parents=True, exist_ok=True)
        with open(mem, "a", encoding="utf-8") as f:
            f.write(info.strip() + "\n")
        return f"已记住：{info.strip()}"
    except OSError as e:
        return f"保存记忆失败：{e}"


def _recall(query: str) -> str:
    del query  # 简化实现：忽略关键词，返回全部记忆
    mem = _memory_file()
    if not mem.exists():
        return "当前没有任何保存的记忆。"
    try:
        content = mem.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return f"读取记忆失败：{e}"
    if not content.strip():
        return "当前没有任何保存的记忆。"
    return "已保存的记忆：\n" + truncate_text(content.strip(), 3000)


def _forget(info: str) -> str:
    """删除所有包含指定文本的记忆条目（仅影响记忆文件，与系统删除无关）。"""
    mem = _memory_file()
    if not mem.exists():
        return "当前没有任何记忆。"
    try:
        lines = mem.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        return f"读取记忆失败：{e}"
    query = info.strip()
    if not query:
        return "请提供要删除的记忆内容。"
    kept = [l for l in lines if query not in l]
    removed = len(lines) - len(kept)
    if removed == 0:
        return f"未找到包含「{query}」的记忆。"
    try:
        mem.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    except OSError as e:
        return f"删除记忆失败：{e}"
    return f"已删除 {removed} 条包含「{query}」的记忆。"


def _update_memory(old_info: str, new_info: str) -> str:
    """把包含指定文本的记忆条目替换为新内容。"""
    mem = _memory_file()
    if not mem.exists():
        return "当前没有任何记忆。"
    try:
        lines = mem.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        return f"读取记忆失败：{e}"
    old = old_info.strip()
    new = new_info.strip()
    if not old or not new:
        return "请提供要修改的旧内容与新内容。"
    updated = []
    changed = 0
    for l in lines:
        if old in l:
            updated.append(new)
            changed += 1
        else:
            updated.append(l)
    if changed == 0:
        return f"未找到包含「{old}」的记忆。"
    try:
        mem.write_text("\n".join(updated) + ("\n" if updated else ""), encoding="utf-8")
    except OSError as e:
        return f"修改记忆失败：{e}"
    return f"已更新 {changed} 条记忆：\n「{old}」→「{new}」"


# ---------------- 网页抓取 ----------------

class _TextExtractor(HTMLParser):
    """用标准库提取 HTML 文本，去除 script/style/nav 等标签。"""

    SKIP = {"script", "style", "nav", "footer", "header", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1
        if tag in ("p", "div", "li", "br", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self.parts.append(text)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(self.parts)).strip()


def _fetch_url(url: str) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        text = http_get(url, timeout=15, headers=headers)
        extractor = _TextExtractor()
        extractor.feed(text)
        text = extractor.text()
        if not text:
            return "网页内容为空或无法提取正文。"
        return truncate_text(text, 3000)
    except Exception as e:
        return f"抓取网页出错：{e}"


# ---------------- 网页搜索（Bing，国内可访问） ----------------

class _BingParser(HTMLParser):
    """解析 Bing 搜索结果页：提取 (标题, 摘要, 链接)。
    Bing 结构：<li class="b_algo"><h2><a>标题</a></h2>…<p>摘要</p></li>
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_algo = False  # 是否位于 <li class="b_algo"> 内
        self._in_h2 = False
        self._in_p = False
        self._current: Dict[str, str] = {}
        self.results: List[Dict[str, str]] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li" and "b_algo" in attrs.get("class", "").split():
            self._in_algo = True
            self._current = {"title": "", "url": "", "snippet": ""}
        elif tag == "h2" and self._in_algo:
            self._in_h2 = True
        elif tag == "a" and self._in_h2:
            href = attrs.get("href", "")
            if href and not href.startswith("javascript") and not self._current["url"]:
                self._current["url"] = href
        elif tag == "p" and self._in_algo:
            self._in_p = True

    def handle_endtag(self, tag):
        if tag == "h2" and self._in_h2:
            self._in_h2 = False
        elif tag == "p" and self._in_p:
            self._in_p = False
        elif tag == "li" and self._in_algo:
            self._in_algo = False
            if self._current and self._current["url"]:
                self.results.append(self._current)
            self._current = {}

    def handle_data(self, data):
        if self._in_h2:
            self._current["title"] += data.strip()
        elif self._in_p:
            self._current["snippet"] += data.strip()


def _parse_rss_results(rss_text: str, limit: int = 5) -> List[Dict[str, str]]:
    """解析 Bing RSS(格式稳定,不依赖 HTML 结构)。返回 [{title,url,snippet}]。"""
    from xml.etree import ElementTree as ET

    root = ET.fromstring(rss_text)
    results = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", desc)  # description 常为 HTML 片段
        desc = re.sub(r"\s+", " ", desc).strip()
        if title or link:
            results.append({"title": title, "url": link, "snippet": desc})
        if len(results) >= limit:
            break
    return results


def _format_results(results: List[Dict[str, str]]) -> str:
    if not results:
        return "未找到搜索结果。"
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet'][:200]}")
    return "\n\n".join(lines)


def _web_search(query: str) -> str:
    """Bing 搜索。优先 RSS 接口(结构稳定)；RSS 失败/为空时回退 HTML 解析。"""
    from urllib.parse import urlencode

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        rss = http_get(
            "https://www.bing.com/search?" + urlencode({"q": query, "format": "rss"}),
            timeout=12,
            headers=headers,
        )
        results = _parse_rss_results(rss)
        if results:
            return _format_results(results)
    except Exception:
        pass  # RSS 不可用,回退 HTML

    # 回退:HTML 页面解析(旧逻辑)
    try:
        text = http_get(
            "https://www.bing.com/search?" + urlencode({"q": query, "setlang": "zh-hans"}),
            timeout=12,
            headers=headers,
        )
        parser = _BingParser()
        parser.feed(text)
        return _format_results(parser.results[:5])
    except Exception as e:
        return f"搜索出错：{e}"


# ---------------- shell 执行（带安全层） ----------------

def _execute_shell(shell_command: str) -> str:
    from .utils import run_command

    result = safety.analyze_command(shell_command, cwd=os.getcwd())
    if result.verdict == safety.BLOCK:
        return safety.block_reply(shell_command, result)

    if result.verdict == safety.CONFIRM:
        # 写文件操作（创建/覆盖/移动/重命名/复制）受 FILE_WRITE_CONFIRM 独立开关控制；
        # 其它高危操作受 CONFIRM_DANGEROUS 控制。记忆文件 memory.txt 由记忆工具直接管理，不经过这里。
        if result.category == "write":
            # 工作目录授权：命令目标在当前授权的工作目录内 → 免确认直接执行
            # （删除类仍被 BLOCK 硬拦，不会走到这里）
            if safety.is_workspace_write(shell_command):
                need_confirm = False
            else:
                need_confirm = cfg.get_bool("FILE_WRITE_CONFIRM", True)
        else:
            need_confirm = cfg.get_bool("CONFIRM_DANGEROUS", True)
        if need_confirm:
            # 交互环境要求确认；非交互（如管道）默认拒绝，安全优先
            try:
                reply = input(safety.confirm_prompt(shell_command, result.reason)).strip().lower()
            except (EOFError, OSError):
                return f"非交互环境下高危命令未执行：{shell_command}"
            if reply != "y":
                return "用户取消了该命令的执行。"

    exit_code, output = run_command(shell_command)
    body = f"退出码: {exit_code}\n输出:\n{output}"
    return truncate_text(body, cfg.get_int("TOOL_OUTPUT_MAX_CHARS", 2000))


# ---------------- 工具注册表 ----------------

TOOLS: List[Dict] = [
    {
        "name": "execute_shell_command",
        "description": (
            "在用户的 Windows 终端（PowerShell 或 cmd）中执行一条 shell 命令并返回输出。"
            "仅用于执行安全的只读或必要操作（查看目录、读取文件、运行工具等）。"
            "删除/格式化/清空等破坏性指令会被安全层自动拦截，不要尝试绕过。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "shell_command": {
                    "type": "string",
                    "description": "要执行的完整 shell 命令。",
                }
            },
            "required": ["shell_command"],
        },
        "handler": _execute_shell,
    },
    {
        "name": "remember",
        "description": (
            "把一条信息永久保存到记忆文件（如用户的工具目录、常用设置、偏好等），"
            "下次可通过 recall 检索。适合需要长期记住的事实。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "info": {
                    "type": "string",
                    "description": "需要记住的信息内容。",
                }
            },
            "required": ["info"],
        },
        "handler": _remember,
    },
    {
        "name": "recall",
        "description": "读取之前通过 remember 保存的全部记忆内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要回忆的关键词或问题（当前实现返回全部记忆）。",
                }
            },
            "required": ["query"],
        },
        "handler": _recall,
    },
    {
        "name": "forget",
        "description": (
            "删除记忆文件中所有包含指定文本的记忆条目（如用户要求清除某条记忆、"
            "信息已过时等）。仅影响 agent 自己的记忆文件，不属于危险删除操作。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "info": {
                    "type": "string",
                    "description": "要删除的记忆内容（按包含匹配，删除所有匹配条目）。",
                }
            },
            "required": ["info"],
        },
        "handler": _forget,
    },
    {
        "name": "update_memory",
        "description": (
            "修改已保存的记忆：把包含指定旧文本的条目替换为新内容"
            "（如用户的目录搬了家、设置变更）。仅影响 agent 自己的记忆文件。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "old_info": {
                    "type": "string",
                    "description": "要修改的记忆中的旧内容（按包含匹配）。",
                },
                "new_info": {
                    "type": "string",
                    "description": "替换后的新内容。",
                },
            },
            "required": ["old_info", "new_info"],
        },
        "handler": _update_memory,
    },
    {
        "name": "fetch_url",
        "description": "抓取指定 URL 的网页正文文本（去除导航/脚本），用于查看链接内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要抓取的完整网址，例如 https://example.com",
                }
            },
            "required": ["url"],
        },
        "handler": _fetch_url,
    },
    {
        "name": "web_search",
        "description": "通过 Bing 搜索互联网，返回前几条结果的标题、摘要与链接。"
        "用于查询最新信息、实时数据或未知内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词或问题。",
                }
            },
            "required": ["query"],
        },
        "handler": _web_search,
    },
]

_TOOLS_BY_NAME: Dict[str, Dict] = {t["name"]: t for t in TOOLS}


def get_tool_schemas(include: List[str] | None = None) -> List[Dict]:
    """返回 OpenAI 兼容的 tools schema 列表；include 为 None 时返回全部。"""
    result = []
    for t in TOOLS:
        if include and t["name"] not in include:
            continue
        result.append(
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
        )
    return result


def run_tool(name: str, arguments: str) -> str:
    """按名称执行工具。arguments 为 JSON 字符串。"""
    tool = _TOOLS_BY_NAME.get(name)
    if not tool:
        return f"未知工具：{name}"
    try:
        kwargs = json.loads(arguments) if arguments else {}
        if not isinstance(kwargs, dict):
            return f"工具参数格式错误：{arguments}"
        return tool["handler"](**kwargs)
    except json.JSONDecodeError:
        return f"工具参数不是合法 JSON：{arguments}"
    except TypeError as e:
        return f"工具参数不匹配：{e}"
    except Exception as e:
        return f"工具执行异常：{e}"

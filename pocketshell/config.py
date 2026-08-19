# SPDX-License-Identifier: MPL-2.0
"""配置加载：优先级 环境变量 > config.json > 内置默认值。

设计原则（零安装、便携）：
- 唯一配置文件为 agent 根目录下 config.json（与 run.bat 同级），含全部配置项与注释，
  首次运行自动生成完整模板。
- 数据文件（sessions/、memory.txt）默认也在 agent 目录下，拷走整个目录即完成迁移。
- 不阻塞：首次运行绝不交互式询问（getpass），API Key 缺失时给出明确报错与获取链接。

环境变量命名规则：PS_ + 配置键名（如 CONTEXT_TOKEN_BUDGET → PS_CONTEXT_TOKEN_BUDGET）；
另有裸键别名 OPENAI_API_KEY / PS_API_KEY 均可设置 API Key。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, List

# 项目根目录（解压根 / 仓库根）：包目录的父目录。
# 所有用户数据（config.json / sessions/ / memory.txt）默认放这里，
# 保证"根目录一个 config.json"，而不是藏进包目录。
ROOT_DIR = Path(__file__).resolve().parent.parent

# agent 包所在目录（程序文件；自毁防护保护整个 ROOT_DIR）
AGENT_DIR = Path(__file__).resolve().parent

CONFIG_PATH = Path(os.environ.get("PS_CONFIG_PATH", ROOT_DIR / "config.json"))

# 环境变量前缀：PS_（PocketShell）
_ENV_PREFIX = "PS_"

# 内置默认值（均为字符串，与配置文件的键值格式一致）
# 注意：这里只放常量默认值；环境变量由 Config.get 每次实时读取（优先于本表）。
DEFAULTS: Dict[str, str] = {
    # ---- 模型与端点 ----
    "DEFAULT_MODEL": "deepseek-v4-flash",
    # 官方 DeepSeek 端点；支持指向任意 OpenAI 兼容端点
    "API_BASE_URL": "https://api.deepseek.com",
    "OPENAI_API_KEY": "",
    # ---- 会话与 token 预算 ----
    # 单次请求的消息历史 token 预算（超出截断最旧消息，保留 system 与最近内容）。
    # DeepSeek V4 上下文窗口为 1M，日常几十轮对话远不会触顶；默认 64K 在"保留早期
    # 指令信息"与"控制消耗"之间平衡。想更省可调小（如 16000），想全程不截断调大。
    "CONTEXT_TOKEN_BUDGET": "65536",
    # 系统提示词注入频率：每 N 轮对话注入一次 system 提示词（省 token 关键）。
    # 原理：system 提示词出现在上下文后，模型在后续轮次仍"记得"其内容，
    # 无需每轮重复发送；利用 DeepSeek 上下文缓存，中间轮次请求更小。
    # 1 = 每轮注入（最保守，安全约束最新鲜）；3 = 每 3 轮注入一次（默认，平衡）；
    # 0 或更大值同理按 N 轮一次。注意：会话首轮、cwd 变化轮次总是注入。
    "SYSTEM_PROMPT_INTERVAL": "3",
    # 会话 JSON 文件保存的最大消息数
    "SESSION_MAX_MESSAGES": "100",
    # ---- 工具输出 ----
    # shell / 网页抓取等工具结果回传给模型的最大字符数（省 token 关键）
    "TOOL_OUTPUT_MAX_CHARS": "2000",
    # ---- 输出长度 ----
    # 模型单次回答的最大 token 数（硬限制，防止长文失控烧 token）
    "MAX_OUTPUT_TOKENS": "4096",
    # ---- 安全 ----
    # 非删除但高危的命令是否要求 y/n 确认（"true"/"false"）
    "CONFIRM_DANGEROUS": "true",
    # 写文件操作（创建/覆盖/追加/移动/重命名/复制）是否要求 y/n 确认。
    # 独立于 CONFIRM_DANGEROUS；记忆文件 memory.txt 由记忆工具直接管理，不受影响。
    "FILE_WRITE_CONFIRM": "true",
    # ---- 采样 ----
    # 采样温度 (0-2)
    "TEMPERATURE": "0.0",
    # 核采样 (0-1)
    "TOP_P": "1.0",
    # ---- 工具与交互 ----
    # 是否启用工具调用（execute_shell/remember/recall/web_search/fetch_url）
    "ENABLE_TOOLS": "true",
    # 单次对话中工具调用轮数的硬上限（防止模型失败后无限重试/换姿势螺旋烧 token）
    "MAX_TOOL_ROUNDS": "10",
    # 流式输出（"true"/"false"）
    "STREAM": "true",
    # 每轮对话后是否显示 token 消耗统计（含缓存命中）
    "SHOW_USAGE": "true",
    "REQUEST_TIMEOUT": "120",
    # ---- 路径（默认都在项目根目录下，便携） ----
    "SESSIONS_DIR": str(ROOT_DIR / "sessions"),
    "MEMORY_FILE": str(ROOT_DIR / "memory.txt"),
    # ---- 自定义指令 ----
    # 追加到系统提示词末尾的额外指令（可留空）；写在这里不动代码即可改 agent 行为
    "CUSTOM_INSTRUCTIONS": "",
}


def _strip_json_comments(text: str) -> str:
    """剥离 JSON 中的 // 与 /* */ 注释（字符串感知，不会误删 "https://..." 里的 //）。"""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


class Config:
    """轻量配置容器：env > 配置文件 > 默认值。"""

    def __init__(self, path: Path = CONFIG_PATH, defaults: Dict[str, str] | None = None) -> None:
        self.path = path
        self._defaults = defaults or DEFAULTS
        self._file_values: Dict[str, str] = {}
        if self.path.exists():
            self._read()

    def _read(self) -> None:
        """解析 config.json（支持 // 与 /* */ 注释，字符串内不误删）。

        编码自动识别（Windows 关键）：UTF-8(含 BOM) → GBK/ANSI → latin-1 兜底。
        空值（"" 或 null）视为"未设置"，回落到默认值，便于模板留空走默认。
        """
        text = None
        for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
            try:
                text = self.path.read_text(encoding=enc)
                break
            except (OSError, UnicodeDecodeError):
                continue
        if text is None:
            return
        try:
            data = json.loads(_strip_json_comments(text))
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        for key, value in data.items():
            if value is None or value == "":
                continue  # 空值 = 用默认
            if isinstance(value, bool):
                self._file_values[str(key)] = "true" if value else "false"
            else:
                self._file_values[str(key)] = str(value)

    def reload(self) -> None:
        """重新读取配置文件（生成/升级配置模板后调用，修复同进程读不到的问题）。"""
        self._file_values = {}
        if self.path.exists():
            self._read()

    def get(self, key: str) -> str:
        # 环境变量优先：PS_+键名 或裸键（OPENAI_API_KEY）
        env_value = os.environ.get(_ENV_PREFIX + key)
        if env_value is not None:
            return env_value
        env_value = os.environ.get(key)
        if env_value is not None:
            return env_value
        if key in self._file_values:
            return self._file_values[key]
        if key in self._defaults:
            return self._defaults[key]
        raise KeyError(f"缺少配置项: {key}")

    def get_bool(self, key: str, fallback: bool = False) -> bool:
        try:
            return self.get(key).strip().lower() in ("true", "1", "yes", "on")
        except KeyError:
            return fallback

    def get_int(self, key: str, fallback: int = 0) -> int:
        try:
            return int(self.get(key))
        except (ValueError, KeyError):
            return fallback

    def get_api_key(self) -> str:
        # 兼容来源：裸键 OPENAI_API_KEY、PS_API_KEY（环境变量）
        key = os.environ.get("PS_API_KEY", "").strip()
        if not key:
            key = self.get("OPENAI_API_KEY").strip()
        if not key:
            raise RuntimeError(
                "未配置 DeepSeek API Key。\n"
                "  方式一（推荐）：设置环境变量 PS_API_KEY=sk-xxx\n"
                "  方式二：把 Key 写入配置文件后重试\n"
                f"  配置文件路径: {self.path}\n"
                "  API Key 获取: https://platform.deepseek.com/api_keys"
            )
        return key

    def as_dict(self) -> Dict[str, str]:
        d: Dict[str, str] = {}
        for key in self._defaults:
            try:
                d[key] = self.get(key)
            except KeyError:
                continue
        return d


# 模块级单例，供各模块使用
cfg = Config()


_CONFIG_TEMPLATE = """{
  // ============ agent 配置文件（config.json） ============
  // 优先级：环境变量 > 本文件 > 内置默认值。
  // 环境变量规则：PS_ + 键名（如 CONTEXT_TOKEN_BUDGET → PS_CONTEXT_TOKEN_BUDGET）。
  // 修改后重启 agent 生效；注释请独占一行（行内 // 会被当作注释内容删除）。
  // API Key 也可用环境变量 PS_API_KEY 或 OPENAI_API_KEY 设置。

  // ---------- 模型与 API ----------
  // 默认模型：deepseek-v4-flash（快） / deepseek-v4-pro（强）
  "DEFAULT_MODEL": "deepseek-v4-flash",
  // DeepSeek API Key（必填，否则无法对话）
  "OPENAI_API_KEY": "__OPENAI_API_KEY__",
  // API 端点，可指向任意 OpenAI 兼容服务
  "API_BASE_URL": "https://api.deepseek.com",

  // ---------- 上下文与 token（省 token 关键） ----------
  // 消息历史 token 预算，超出自动丢弃最旧消息（V4 窗口 1M，日常 64K 足够）
  "CONTEXT_TOKEN_BUDGET": 65536,
  // 系统提示词注入频率：每 N 轮对话注入一次（默认 3，省 token 关键）。
  // 原理：system 提示词出现一次后，模型在上下文缓存里"记得"它，中间轮次不再重复发送。
  // 1 = 每轮注入（最保守）；3 = 每 3 轮注入一次；会话首轮与 cwd 变化轮总是注入。
  "SYSTEM_PROMPT_INTERVAL": 3,
  // 单次回答最大 token 数（输出长度硬限制；0 表示不限制）
  "MAX_OUTPUT_TOKENS": 4096,
  // 工具结果回传给模型的最大字符数（截断省 token）
  "TOOL_OUTPUT_MAX_CHARS": 2000,
  // 会话文件保存的最大消息数
  "SESSION_MAX_MESSAGES": 100,

  // ---------- 采样参数 ----------
  // 温度 (0-2)，越高越随机
  "TEMPERATURE": 0.0,
  // 核采样 (0-1)
  "TOP_P": 1.0,

  // ---------- 安全 ----------
  // 高危命令（关机/杀进程/改权限/卸载等）是否要求输入 y 确认
  "CONFIRM_DANGEROUS": true,
  // 写文件操作（创建/覆盖/追加/移动/重命名/复制）是否要求输入 y 确认
  // 与 CONFIRM_DANGEROUS 相互独立；记忆文件 memory.txt 由记忆工具管理，不需要确认
  "FILE_WRITE_CONFIRM": true,

  // ---------- 工具与交互 ----------
  // 是否启用工具调用（execute_shell/remember/recall/forget/update_memory/web_search/fetch_url）
  "ENABLE_TOOLS": true,
  // 单次对话工具调用轮数硬上限（防模型失败后无限重试/换姿势螺旋烧 token；超出即停止并如实汇报）
  "MAX_TOOL_ROUNDS": 10,
  // 流式输出
  "STREAM": true,
  // 每轮对话后显示 token 消耗统计（含缓存命中，省 token 看得见；命令行 --no-usage 可关）
  "SHOW_USAGE": true,
  // 请求超时秒数
  "REQUEST_TIMEOUT": 120,

  // ---------- 路径（留空则用默认：agent 目录下，随目录迁移） ----------
  "SESSIONS_DIR": "",
  "MEMORY_FILE": "",

  // ---------- 自定义指令 ----------
  // 追加到系统提示词末尾的额外指令，改这里不动代码即可定制 agent 行为。
  // 例如："CUSTOM_INSTRUCTIONS": "永远用简体中文回答；提到文件时给出完整路径"
  // 注意：内置安全铁律始终保留在最前面，自定义指令只会追加在后面，不会覆盖安全规则。
  "CUSTOM_INSTRUCTIONS": ""
}
"""


def _write_config_template() -> None:
    # 用占位符替换而非 .format()，避免 JSON 花括号与格式串冲突
    text = _CONFIG_TEMPLATE.replace("__OPENAI_API_KEY__", "")
    try:
        cfg.path.parent.mkdir(parents=True, exist_ok=True)
        cfg.path.write_text(text, encoding="utf-8")
    except OSError:
        pass


def _template_key_blocks() -> List[tuple]:
    """从模板中提取 (前导注释块, 键行) 列表，供合并缺失键使用。

    返回 [(comment_lines, key_line), ...]，comment_lines 为注释文本行（含 // 与空行）。
    """
    lines = _CONFIG_TEMPLATE.splitlines()
    blocks: List[tuple] = []
    pending_comments: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("//") or not stripped:
            pending_comments.append(line)
            continue
        m = re.match(r'^\s*"([A-Za-z_]+)"\s*:', line)
        if m:
            blocks.append((pending_comments, line))
            pending_comments = []
    return blocks


def _merge_missing_template_keys() -> bool:
    """把模板中有、现有 config.json 缺失的键（连同注释）补进文件，保留已有值。

    已存在的 config.json 不会被覆盖，只是补齐新版本新增的配置项
    （否则老用户的 config.json 永远看不到新参数）。返回是否写入了新键。
    """
    if not cfg.path.exists():
        return False
    text = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            text = cfg.path.read_text(encoding=enc)
            break
        except (OSError, UnicodeDecodeError):
            continue
    if text is None:
        return False
    try:
        data = json.loads(_strip_json_comments(text))
    except (json.JSONDecodeError, TypeError):
        return False  # 文件损坏/非法，不动
    if not isinstance(data, dict):
        return False

    missing = []
    for comments, key_line in _template_key_blocks():
        m = re.match(r'^\s*"([A-Za-z_]+)"\s*:', key_line)
        key = m.group(1)
        if key not in data:
            missing.append((comments, key_line))
    if not missing:
        return False

    # 组装插入块：注释 + 键行，缩进对齐（模板是 2 空格）
    insert_lines = []
    for comments, key_line in missing:
        for c in comments:
            insert_lines.append("  " + c.strip() if c.strip() else "")
        insert_lines.append(key_line)
    # 插入块会放在文件末尾（最后一个键之后），末行不能带尾逗号
    if insert_lines and insert_lines[-1].rstrip().endswith(","):
        insert_lines[-1] = insert_lines[-1].rstrip()[:-1]
    block = "\n".join(insert_lines)

    # 在最后一个 "}" 前插入；同时保证前一键行有逗号
    rindex = text.rfind("}")
    if rindex < 0:
        return False
    head, tail = text[:rindex], text[rindex:]
    head = head.rstrip()
    if not head.endswith(","):
        # 找最后一行非空行，补逗号
        lines = head.splitlines()
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip():
                lines[i] = lines[i].rstrip() + ","
                break
        head = "\n".join(lines)
    new_text = head + "\n" + block + "\n" + tail
    try:
        cfg.path.write_text(new_text, encoding="utf-8")
    except OSError:
        return False
    return True


def ensure_config_file() -> None:
    """确保配置文件就绪：
    - config.json 不存在 → 生成完整模板。
    - config.json 已存在 → 绝不覆盖已有值，但会自动补齐新版本新增的配置项
      （如 SYSTEM_PROMPT_INTERVAL），升级后也能看到新参数。
    """
    if cfg.path.exists():
        _merge_missing_template_keys()
        cfg.reload()  # 合并后同进程立即读到新键
        return
    _write_config_template()
    cfg.reload()  # 同进程立即读到刚生成的配置

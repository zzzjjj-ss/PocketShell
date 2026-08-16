"""会话管理：UTF-8 JSON 持久化 + 工具消息清理 + token 预算截断（省 token 核心）。

会话文件位于 agent 目录下 sessions/<name>.json（便携：拷走整个 agent 目录即迁移）。

关键点（修复旧 fork 的会话 bug）：
- 所有消息先加入会话存储（_conversations），再序列化发送给 API，绝不重绑定列表。
- 保存前清理不完整的 tool 消息链，避免 API 400。
- 按 token 预算从最旧消息开始丢弃（保留 system 与最近内容）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from .config import AGENT_DIR, cfg
from .utils import estimate_messages_tokens

SESSIONS_DIR = Path(
    os.environ.get("SGPT_SESSIONS_DIR", str(AGENT_DIR / "sessions"))
)


def clean_tool_messages(messages: List[Dict]) -> List[Dict]:
    """移除不完整的工具调用消息链：
    - 孤立的 tool 消息（前面没有 assistant 带 tool_calls）
    - assistant 带 tool_calls 但后面缺少足够数量的 tool 消息
    """
    cleaned: List[Dict] = []
    i, n = 0, len(messages)
    while i < n:
        msg = messages[i]
        role = msg.get("role")
        if role == "assistant" and "tool_calls" in msg:
            expected = len(msg["tool_calls"])
            j = i + 1
            tool_count = 0
            while j < n and messages[j].get("role") == "tool":
                tool_count += 1
                j += 1
            if tool_count < expected:
                i = j  # 跳过不完整的 assistant 及其 tool 消息
                continue
            cleaned.append(msg)
            cleaned.extend(messages[i + 1 : j])
            i = j
        elif role == "tool":
            i += 1  # 孤立 tool 消息
        else:
            cleaned.append(msg)
            i += 1
    return cleaned


class Session:
    def __init__(self, name: str = "default", ephemeral: bool = False) -> None:
        self.name = name
        self.ephemeral = ephemeral  # True 时 save() 不落盘（单次问答模式）
        safe = Path(name).name  # 防止路径穿越
        sessions_dir = Path(cfg.get("SESSIONS_DIR"))
        self.path = sessions_dir / f"{safe}.json"
        self.messages: List[Dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return
        if isinstance(data, list):
            self.messages = clean_tool_messages(data)

    def save(self) -> None:
        if self.ephemeral:
            return
        Path(cfg.get("SESSIONS_DIR")).mkdir(parents=True, exist_ok=True)
        cleaned = clean_tool_messages(self.messages)
        # 限制最大消息数，防止文件无限膨胀
        max_msgs = cfg.get_int("SESSION_MAX_MESSAGES", 100)
        if len(cleaned) > max_msgs:
            # 保留第一条（通常是 system）+ 最近 max_msgs-1 条
            cleaned = cleaned[:1] + cleaned[-(max_msgs - 1):]
        self.path.write_text(
            json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def reset(self) -> None:
        self.messages = []
        self.path.unlink(missing_ok=True)

    def add(self, message: Dict) -> None:
        self.messages.append(message)

    def system(self, role_text: str) -> None:
        if not self.messages or self.messages[0].get("role") != "system":
            self.messages.insert(0, {"role": "system", "content": role_text})

    def ensure_system(self, role_text: str) -> None:
        """确保第一条是 system 消息；若会话已存在 system 则保留原样。"""
        if not self.messages:
            self.system(role_text)

    def add_user(self, prompt: str) -> None:
        self.add({"role": "user", "content": prompt})

    def add_assistant(self, content: str) -> None:
        self.add({"role": "assistant", "content": content})

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        self.add({"role": "tool", "content": content, "tool_call_id": tool_call_id})

    def messages_for_api(self) -> List[Dict]:
        """返回发送给 API 的消息：清理 + 按 token 预算截断。"""
        msgs = clean_tool_messages(self.messages)
        budget = cfg.get_int("CONTEXT_TOKEN_BUDGET", 16000)
        while msgs and estimate_messages_tokens(msgs) > budget and len(msgs) > 1:
            # 丢弃最旧的非 system 消息
            for idx in range(len(msgs)):
                if msgs[idx].get("role") != "system":
                    del msgs[idx]
                    break
            else:
                break
        return msgs

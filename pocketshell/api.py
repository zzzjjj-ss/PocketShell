# SPDX-License-Identifier: MPL-2.0
"""DeepSeek API 客户端：流式对话 + 工具调用循环。

- 纯标准库 urllib 调 OpenAI 兼容 /chat/completions 接口（零第三方依赖，体积小）。
- 流式 SSE 解析，支持 DeepSeek 推理模型的 reasoning_content（展示但绝不回存历史，省 token）。
- 工具调用循环：模型请求工具 → 本地执行 → 结果回填 → 继续对话。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from .config import cfg
from .tools import run_tool

API_URL = "{base}/chat/completions"
# 工具调用消息中可能出现的角色字段（避免工具消息被误清理）
TOOL_ROLE = "tool"


class ApiError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class StreamClosed(ApiError):
    pass


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.get_api_key()}",
        "Content-Type": "application/json",
    }


def _post(url: str, headers: Dict[str, str], payload: Dict, timeout: int) -> Tuple[int, Any]:
    """标准库 POST（JSON 流式），返回 (status_code, response 对象)。

    响应对象为 http.client.HTTPResponse（可逐行迭代读取 SSE）；
    4xx/5xx 时返回 HTTPError 对象（同样可读 body）。网络错误抛 ApiError。
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, resp
    except urllib.error.HTTPError as e:
        return e.code, e
    except urllib.error.URLError as e:
        raise ApiError(f"网络请求失败：{e.reason}") from e


def _iter_lines(resp: Any):
    """统一产出 SSE 数据行（已去换行，跳过空行）。

    兼容两种响应：requests 风格（iter_lines()）与 urllib http.client（逐行迭代 bytes）。
    """
    if hasattr(resp, "iter_lines"):
        for raw in resp.iter_lines(decode_unicode=True):
            if raw:
                yield raw
    else:
        for raw in resp:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            raw = raw.rstrip("\r\n")
            if raw:
                yield raw


def _url() -> str:
    base = cfg.get("API_BASE_URL").rstrip("/")
    return f"{base}/chat/completions"


def make_system_prompt() -> str:
    """组装系统提示词：安全约束 + 平台信息 + 当前工作目录 + 使用规则 + 自定义指令。"""
    import os

    from .utils import describe_os, detect_shell

    prompt = f"""你是运行在用户终端里的 AI 助手（shell agent），帮助用户完成 Windows 系统上的操作与问答。

【系统环境】操作系统：{describe_os()}；当前 shell：{detect_shell()}；
当前工作目录：{os.getcwd()}

【安全铁律 - 必须无条件遵守】
1. 严禁执行任何删除、清空、格式化、破坏性操作。包括但不限于：
   del/erase/rm/rmdir/rd/unlink、Remove-Item、Clear-Content、Clear-RecycleBin、
   Format-Volume/format、reg delete、schtasks /delete、wmic ... delete、sc delete、
   Remove-Service、net user /delete、diskpart clean、shred 等。
2. 如果安全层返回“已被安全策略拦截”，立即停止相关尝试，改为只读操作或请用户手动执行。
3. 严禁使用编码命令（-EncodedCommand）、Invoke-Expression、Base64 解码、变量拼接等任何方式绕过安全限制。
4. 删除类需求一律拒绝并解释原因，建议用户手动操作或使用回收站。
5. agent 自身所在目录（程序文件目录）是禁区：严禁删除/清空/移动/重命名/覆盖其中的任何文件，
   防止 agent 功能失效（安全层同样会硬拦截此类操作）。
6. 写文件操作（创建/覆盖/追加/移动/重命名/复制文件）必须先征求用户确认（安全层会弹出确认提示），
   用户同意后才可执行；agent 自己的记忆文件 memory.txt 由记忆工具管理，不需要确认。

【工具使用规则】
0. 涉及目录、文件、路径问题必须先确认实际位置：用 pwd / Get-Location / dir 等命令
   查看当前目录，用 where / Get-ChildItem 确认文件是否存在。**严禁凭猜测编造路径**，
   系统环境里给出的“当前工作目录”只是启动时的值，实际以命令查询结果为准。
1. 优先使用工具获取真实信息（当前目录、文件内容、命令输出），不要凭空猜测。
2. 执行 shell 命令前先想清楚是否必要；能用只读命令（dir/Get-ChildItem/type/Get-Content）就不用破坏性命令。
3. 用户询问需要长期记住的信息（目录、设置、偏好）时，先调用 recall 查记忆；没有时用 remember 保存；
   记忆需要清除或变更时用 forget / update_memory（这两个工具仅操作 agent 自己的记忆文件，
   不属于危险删除，安全铁律不适用）。
4. 工具输出如果被截断，基于已有信息回答，不要臆造缺失部分。

【回答风格】
- 使用简洁的中文回答。
- 涉及代码/命令时用 Markdown 代码块。
- 不确定时明确说明，不要编造。
"""

    # 追加用户自定义指令（config.json 的 CUSTOM_INSTRUCTIONS，安全铁律不受影响）
    extra = cfg.get("CUSTOM_INSTRUCTIONS").strip()
    if extra:
        prompt += f"\n\n【用户自定义指令】\n{extra}\n"
    return prompt


def _parse_sse_line(line: str) -> Optional[Dict]:
    """解析一行 SSE 数据：'data: {json}'，返回 JSON dict；结束标记返回 None。"""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if payload == "[DONE]":
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def stream_completion(
    model: str,
    messages: List[Dict],
    tools: Optional[List[Dict]],
    temperature: float,
    top_p: float,
    max_tokens: Optional[int] = None,
    on_chunk: Optional[Callable[[str], None]] = None,
    on_usage: Optional[Callable[[Dict], None]] = None,
) -> Tuple[str, List[Dict], Optional[str]]:
    """发起一次流式请求，返回 (assistant_content, tool_calls, reasoning_text)。

    tool_calls 为 OpenAI 格式列表；无工具调用时为空列表。
    抛 ApiError 表示请求失败。
    on_usage(usage_dict) 在收到 usage（token 统计）时回调一次。
    """
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "stream": True,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    # 流式响应末尾返回 usage（DeepSeek 支持；不支持的端点会忽略该字段）
    payload["stream_options"] = {"include_usage": True}

    timeout = cfg.get_int("REQUEST_TIMEOUT", 120)
    last_error: Optional[Exception] = None
    for attempt in range(3):  # 简单重试：429/5xx/网络错误
        try:
            status_code, resp = _post(_url(), _headers(), payload, timeout)
        except ApiError as e:
            last_error = e
            time.sleep(1 + attempt)
            continue

        if status_code == 401:
            raise ApiError(
                "API Key 无效或未授权（401）。请检查 SGPT_API_KEY 配置。",
                status_code=401,
            )
        if status_code == 429:
            last_error = ApiError("请求过于频繁（429），已限流。", status_code=429)
            time.sleep(2 + attempt * 2)
            resp.close()
            continue
        if status_code >= 500:
            last_error = ApiError(f"服务端错误（{status_code}）。", status_code=status_code)
            time.sleep(2 + attempt * 2)
            resp.close()
            continue
        if status_code != 200:
            try:
                body = resp.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                body = ""
            resp.close()
            raise ApiError(f"API 返回错误 {status_code}: {body}", status_code=status_code)

        try:
            return _consume_stream(resp, on_chunk, on_usage)
        except StreamClosed:
            resp.close()
            raise
        except ApiError:
            resp.close()
            raise
    raise last_error or ApiError("请求失败（未知原因）")


def _consume_stream(
    resp: Any,
    on_chunk: Optional[Callable[[str], None]],
    on_usage: Optional[Callable[[Dict], None]] = None,
) -> Tuple[str, List[Dict], Optional[str]]:
    """消费 SSE 流，累积 content 与 tool_calls 分片。"""
    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    tool_calls: List[Dict] = []
    finish_reason: Optional[str] = None
    usage: Optional[Dict] = None

    for raw in _iter_lines(resp):
        data = _parse_sse_line(raw)
        if data is None:
            continue
        # 流式末尾的 usage 块（无 choices）：记录 token 统计
        if data.get("usage"):
            usage = data["usage"]
        choices = data.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        delta = choice.get("delta") or {}

        # DeepSeek 推理模型的思考内容：仅展示，不回传、不存历史
        reasoning = delta.get("reasoning_content")
        if reasoning:
            reasoning_parts.append(reasoning)
            if on_chunk:
                on_chunk("\r\033[2K\033[90m[思考中…]\033[0m")
                sys.stdout.flush()

        content = delta.get("content")
        if content:
            content_parts.append(content)
            if on_chunk:
                on_chunk(content)

        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            while len(tool_calls) <= idx:
                tool_calls.append({"id": "", "function": {"name": "", "arguments": ""}})
            tool_id = tc.get("id") or ""
            fn = tc.get("function") or {}
            if tool_id:
                tool_calls[idx]["id"] = tool_id
            if fn.get("name"):
                tool_calls[idx]["function"]["name"] = fn["name"]
            if fn.get("arguments"):
                tool_calls[idx]["function"]["arguments"] += fn["arguments"]

        # 注意：不在 finish_reason 处 break —— 流末尾还有 usage 统计块
        # （include_usage 时在 [DONE] 之前单独一条），必须读完整流才能拿到。
        # 流由 [DONE] 或服务端关闭自然结束。

    resp.close()
    if not content_parts and not tool_calls and not finish_reason:
        raise StreamClosed("连接中断：未收到完整响应。")

    if usage and on_usage:
        on_usage(usage)

    reasoning_text = "".join(reasoning_parts) or None
    return "".join(content_parts), tool_calls, reasoning_text


def _assistant_msg(content: Optional[str], tool_calls: List[Dict]) -> Dict:
    msg: Dict[str, Any] = {"role": "assistant"}
    if content:
        msg["content"] = content
    else:
        msg["content"] = None
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"],
                },
            }
            for tc in tool_calls
        ]
    return msg


def run_conversation(
    session,
    model: str,
    temperature: float,
    top_p: float,
    tools: Optional[List[Dict]] = None,
    max_tokens: Optional[int] = None,
    on_chunk: Optional[Callable[[str], None]] = None,
    on_tool: Optional[Callable[[str, str], None]] = None,
    on_usage: Optional[Callable[[Dict], None]] = None,
) -> str:
    """完整的对话回合：流式输出 + 工具调用循环，结束后保存会话。

    返回助手最终文本。on_tool(name, arguments) 用于展示工具调用；
    on_usage(usage_dict) 在每轮底层请求收到 usage 时回调（工具循环可能多轮）。
    """
    messages = session.messages_for_api()
    final_content = ""

    while True:
        content, tool_calls, reasoning = stream_completion(
            model, messages, tools, temperature, top_p,
            max_tokens=max_tokens, on_chunk=on_chunk, on_usage=on_usage,
        )

        if tool_calls:
            # 记录 assistant 的工具调用消息
            assistant = _assistant_msg(None, tool_calls)
            messages.append(assistant)
            session.add(assistant)
            for tc in tool_calls:
                if on_tool:
                    on_tool(tc["function"]["name"], tc["function"]["arguments"])
                result = run_tool(tc["function"]["name"], tc["function"]["arguments"])
                tool_msg = {"role": TOOL_ROLE, "content": result, "tool_call_id": tc["id"]}
                messages.append(tool_msg)
                session.add(tool_msg)
            continue  # 继续下一轮请求

        # 正常结束：记录 assistant 回复
        final_content = content or ""
        session.add_assistant(final_content)
        session.save()
        return final_content

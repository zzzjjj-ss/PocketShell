# SPDX-License-Identifier: MPL-2.0
"""DeepSeek API 客户端：流式对话 + 工具调用循环。

- 纯标准库 urllib 调 OpenAI 兼容 /chat/completions 接口（零第三方依赖，体积小）。
- 流式 SSE 解析，支持 DeepSeek 推理模型的 reasoning_content（展示但绝不回存历史，省 token）。
- 工具调用循环：模型请求工具 → 本地执行 → 结果回填 → 继续对话。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from .config import cfg
from .tools import run_tool
from .utils import estimate_messages_tokens, estimate_tokens

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
    """组装系统提示词：安全约束 + 平台信息 + 当前工作目录 + 使用规则 + 自定义指令。

    注意：此提示词每轮对话都会发送，是固定 token 开销，务必保持精简。
    真正的安全拦截由代码层（safety.py）保证，提示词仅作辅助约束。
    """
    import os

    from .utils import describe_os, detect_shell

    shell = detect_shell()
    if shell == "powershell.exe":
        shell_block = (
            "所有命令都由 Windows PowerShell 执行，必须写 PowerShell 语法：\n"
            "  - 列目录: Get-ChildItem（别用 cmd 的 dir /x、for %f、chcp）\n"
            "  - 查文件: Get-Item '路径' | Select-Object FullName, Length\n"
            "  - 当前目录: Get-Location；改目录: Set-Location '路径'\n"
            "  - 执行外部程序: & 'C:\\路径\\ffmpeg.exe' -i ... （或 where.exe ffmpeg 找位置）\n"
            "  - 管道过滤: Get-ChildItem | Where-Object {{ $_.Name -like '*关键词*' }}\n"
            "  不要嵌套 powershell -NoProfile -Command，命令本身已在 PowerShell 中执行。"
        )
        path_cmd = "Get-Location（查看当前目录）；确认文件用 Get-Item 或 Get-ChildItem 列目录"
    else:
        shell_block = (
            "所有命令都由 Windows cmd 执行，必须写 cmd 语法：\n"
            "  - 列目录: dir /b（或 dir 查看详细信息，别用 PowerShell 的 Get-ChildItem/Select-Object）\n"
            "  - 查文件: dir /b 路径 或 if exist 路径 echo 存在\n"
            "  - 当前目录: cd（不带参数显示当前目录）；改目录: cd /d 路径\n"
            "  - 执行外部程序: 路径\\ffmpeg.exe -i ... （或 where ffmpeg 找位置）\n"
            "  - 文件名带空格/中文时用双引号包裹，循环枚举用 for %f in (...) do ...\n"
            "  - 多条命令用 & 连接（不是 ;，那是 PowerShell 的分隔符）；取输出尾部用 2>&1 | more 或直接看完整输出\n"
            "  不要嵌套 powershell -Command；需要 PowerShell 功能时也尽量用 cmd 等价写法。"
        )
        path_cmd = "cd（查看当前目录）；确认文件用 dir /b 列目录或 if exist 检查"

    prompt = f"""你是终端 AI 助手，帮用户完成 Windows 操作与问答。

【环境】{describe_os()} / 命令由你当前终端 {shell} 执行 / cwd:{os.getcwd()}

【shell 语法】(重要){shell_block}

【安全铁律】
1. 禁止删除/清空/格式化/破坏类操作（安全层会硬拦截，勿尝试绕过：禁 -EncodedCommand/iex/Base64）。
2. 安全层返回"已拦截"即停止，改只读操作或请用户手动执行。
3. 写文件（创建/覆盖/移动/重命名/复制）需先征求确认；-setworkspace 授权目录内写文件免确认。
4. 禁止删除/移动/覆盖程序自身目录（PocketShell 所在目录）内任何文件。

【工具规则】
1. 第一步永远是列当前工作目录（cmd: dir /b；PowerShell: Get-ChildItem），看到真实文件名后再动手。任务文件就在当前工作目录 cwd 里：直接用文件名/相对路径操作，不要猜路径、不要 cd 去别的目录找文件。
2. 绝对路径只在用户明确给出时使用；不要自己编造盘符绝对路径（如 D:\\xxx\\file.mp3）。
3. 需长期记住的信息（目录/设置/偏好）先 recall 查，没有则 remember 存；清除/变更用 forget/update_memory。
4. 工具输出被截断时基于已有信息回答，不臆造缺失部分。
5. 命令失败时先看错误输出判断原因（如程序不存在/路径不对/权限不足），换等价正确写法；不要反复盲试同一种失败命令，更不要换 shell 再试同一条命令。

【回答】简洁中文；代码/命令用 Markdown 代码块；不确定就说明，不编造。
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
                "API Key 无效或未授权（401）。请检查 PS_API_KEY 配置。",
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


def _estimate_split(messages: List[Dict]) -> Dict[str, int]:
    """估算一份 messages 的构成：提示词注入 / 上下文累计 / 本轮新输入。

    用于用量展示的拆分（本地估算，非 API 精确值）：
    - est_system: system 提示词（隔次注入时中间轮次为 0）
    - est_context: system 之外的历史消息（上一轮已有内容）
    - est_new: 本轮新增内容（用户新消息 / 工具结果 / 工具调用声明）
    三者和 ≈ estimate_messages_tokens(messages) ≈ prompt_tokens。
    """
    sys_tok = ctx_tok = new_tok = 0
    for i, m in enumerate(messages):
        role = m.get("role")
        tok = estimate_messages_tokens([m])
        if role == "system":
            sys_tok += tok
        elif i == len(messages) - 1:
            new_tok += tok
        else:
            ctx_tok += tok
    return {"est_system": sys_tok, "est_context": ctx_tok, "est_new": new_tok}


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
    force_system: bool = False,
) -> str:
    """完整的对话回合：流式输出 + 工具调用循环，结束后保存会话。

    返回助手最终文本。on_tool(name, arguments) 用于展示工具调用；
    on_usage(usage_dict) 在每轮底层请求收到 usage 时回调（工具循环可能多轮）。
    force_system=True 时本轮请求强制包含 system 提示词（如 cwd 变化需要刷新）。
    """
    messages = session.messages_for_api(force_system=force_system)
    final_content = ""

    while True:
        # 本轮请求的构成拆分（提示词/上下文/新输入），附在 usage 回调里一并上报
        est = _estimate_split(messages)

        def _usage_cb(u: Dict, _est: Dict = est) -> None:
            if on_usage:
                merged = dict(u)
                merged["est_system"] = _est["est_system"]
                merged["est_context"] = _est["est_context"]
                merged["est_new"] = _est["est_new"]
                on_usage(merged)

        content, tool_calls, reasoning = stream_completion(
            model, messages, tools, temperature, top_p,
            max_tokens=max_tokens, on_chunk=on_chunk, on_usage=_usage_cb,
        )

        if tool_calls:
            # 记录 assistant 的工具调用消息
            assistant = _assistant_msg(None, tool_calls)
            messages.append(assistant)
            session.add(assistant)
            for tc in tool_calls:
                if on_tool:
                    on_tool(tc["function"]["name"], tc["function"]["arguments"])
                name = tc["function"]["name"]
                arguments = tc["function"]["arguments"]
                result = run_tool(name, arguments)
                tool_msg = {"role": TOOL_ROLE, "content": result, "tool_call_id": tc["id"]}
                messages.append(tool_msg)
                session.add(tool_msg)
            continue  # 继续下一轮请求

        # 正常结束：记录 assistant 回复
        final_content = content or ""
        session.add_assistant(final_content)
        session.save()
        return final_content

# SPDX-License-Identifier: MPL-2.0
"""工具调用循环的 mock 测试：模拟 DeepSeek SSE 流，验证
一轮工具调用（assistant tool_calls → tool 结果 → 继续请求 → 最终文本）。

运行：cd /home/zhang/sgpt && HOME=/tmp/sgpt_test_home python3 -m pytest agent/tests/test_api_loop.py -q
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pocketshell import api  # noqa: E402
from pocketshell.session import Session  # noqa: E402


class FakeResponse:
    """模拟 requests.Response：可迭代 SSE 行。"""

    def __init__(self, sse_lines, status_code=200):
        self._lines = sse_lines
        self.status_code = status_code
        self.text = ""

    def iter_lines(self, decode_unicode=True):
        for line in self._lines:
            yield line

    def close(self):
        pass


def _sse(*chunks):
    """把多个 delta chunk 拼成 SSE 行序列（含结束行）。"""
    lines = []
    for chunk in chunks:
        lines.append("data: " + json.dumps({"choices": [chunk]}))
        lines.append("")
    lines.append("data: [DONE]")
    lines.append("")
    return lines


def _tool_chunk(index, name, args_json, tool_id):
    return {
        "delta": {
            "tool_calls": [
                {
                    "index": index,
                    "id": tool_id,
                    "function": {"name": name, "arguments": args_json},
                }
            ]
        },
        "finish_reason": "tool_calls",
    }


def _content_chunk(text, finish="stop"):
    return {"delta": {"content": text}, "finish_reason": finish}


@pytest.fixture()
def tmp_session(tmp_path, monkeypatch):
    monkeypatch.setenv("SGPT_SESSIONS_DIR", str(tmp_path))
    return Session("loop")


def test_tool_loop_end_to_end(tmp_session, monkeypatch):
    """两轮请求：工具调用 → 执行 → 最终回答。"""
    tool_args = json.dumps({"shell_command": "echo hello"})

    # 第一轮：模型要求调用 execute_shell_command
    first_round = _sse(_tool_chunk(0, "execute_shell_command", tool_args, "call_1"))
    # 第二轮：模型给出最终回答
    second_round = _sse(
        _content_chunk("命令已执行，输出 hello。"),
    )

    def fake_post(url, headers, payload, timeout):
        if not fake_post.called:
            fake_post.called = True
            return 200, FakeResponse(first_round)
        return 200, FakeResponse(second_round)

    fake_post.called = False
    monkeypatch.setattr(api, "_post", fake_post)
    monkeypatch.setattr(api, "_url", lambda: "https://api.deepseek.com/chat/completions")

    # 需要一个假 API key
    monkeypatch.setenv("SGPT_API_KEY", "test-key")

    tmp_session.add_user("查看当前目录")

    result = api.run_conversation(
        tmp_session,
        model="deepseek-v4-flash",
        temperature=0.0,
        top_p=1.0,
        tools=[{"type": "function", "function": {"name": "execute_shell_command"}}],
    )
    assert result == "命令已执行，输出 hello。"
    assert fake_post.called

    # 会话保存了完整链：user? 这里我们直接 add_user 手动模拟
    roles = [m["role"] for m in tmp_session.messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert tmp_session.messages[-1]["content"] == "命令已执行，输出 hello。"
    # 会话文件已落盘
    assert tmp_session.path.exists()


def test_tool_loop_blocks_destructive(tmp_session, monkeypatch):
    """模型试图执行 rm -rf：安全层返回拦截消息给模型，工具循环继续。"""
    tool_args = json.dumps({"shell_command": "rm -rf /tmp/foo"})

    first_round = _sse(_tool_chunk(0, "execute_shell_command", tool_args, "call_1"))
    second_round = _sse(_content_chunk("明白，我不会执行删除操作。"))

    def fake_post(url, headers, payload, timeout):
        if not fake_post.called:
            fake_post.called = True
            return 200, FakeResponse(first_round)
        return 200, FakeResponse(second_round)

    fake_post.called = False
    monkeypatch.setattr(api, "_post", fake_post)
    monkeypatch.setattr(api, "_url", lambda: "https://api.deepseek.com/chat/completions")
    monkeypatch.setenv("SGPT_API_KEY", "test-key")

    result = api.run_conversation(
        tmp_session,
        model="deepseek-v4-flash",
        temperature=0.0,
        top_p=1.0,
        tools=[{"type": "function", "function": {"name": "execute_shell_command"}}],
    )
    assert result == "明白，我不会执行删除操作。"
    # tool 消息内容是拦截提示
    tool_msg = [m for m in tmp_session.messages if m["role"] == "tool"]
    assert tool_msg and "已被安全策略拦截" in tool_msg[0]["content"]


def test_stream_closed_raises(monkeypatch):
    """流意外中断应抛 StreamClosed。"""
    def fake_post(url, headers, payload, timeout):
        return 200, FakeResponse(["data: [DONE]", ""])

    monkeypatch.setattr(api, "_post", fake_post)
    monkeypatch.setattr(api, "_url", lambda: "https://api.deepseek.com/chat/completions")
    monkeypatch.setenv("SGPT_API_KEY", "test-key")

    with pytest.raises(api.StreamClosed):
        api.stream_completion(
            model="deepseek-v4-flash",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.0,
            top_p=1.0,
        )


def test_401_raises_api_error(monkeypatch):
    def fake_post(url, headers, payload, timeout):
        return 401, FakeResponse([])

    monkeypatch.setattr(api, "_post", fake_post)
    monkeypatch.setattr(api, "_url", lambda: "https://api.deepseek.com/chat/completions")
    monkeypatch.setenv("SGPT_API_KEY", "test-key")

    with pytest.raises(api.ApiError) as exc:
        api.stream_completion(
            model="deepseek-v4-flash",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.0,
            top_p=1.0,
        )
    assert exc.value.status_code == 401


def test_reasoning_content_not_in_history(tmp_session, monkeypatch):
    """DeepSeek 推理内容只展示、不回存历史。"""
    first_round = _sse(
        {"delta": {"reasoning_content": "让我想想…"}, "finish_reason": None},
        {"delta": {"content": "最终答案。"}, "finish_reason": "stop"},
    )

    def fake_post(url, headers, payload, timeout):
        return 200, FakeResponse(first_round)

    monkeypatch.setattr(api, "_post", fake_post)
    monkeypatch.setattr(api, "_url", lambda: "https://api.deepseek.com/chat/completions")
    monkeypatch.setenv("SGPT_API_KEY", "test-key")

    tmp_session.add_user("问题")
    result = api.run_conversation(
        tmp_session,
        model="deepseek-v4-flash",
        temperature=0.0,
        top_p=1.0,
        tools=None,
    )
    assert result == "最终答案。"
    # 历史里不能出现 reasoning 字段
    for m in tmp_session.messages:
        assert "reasoning_content" not in m


def test_max_tokens_in_payload(monkeypatch):
    """max_tokens 应出现在请求 payload 中。"""
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["payload"] = payload
        return 200, FakeResponse(_sse(_content_chunk("回答。")))

    monkeypatch.setattr(api, "_post", fake_post)
    monkeypatch.setattr(api, "_url", lambda: "https://api.deepseek.com/chat/completions")
    monkeypatch.setenv("SGPT_API_KEY", "test-key")

    api.stream_completion(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        temperature=0.0,
        top_p=1.0,
        max_tokens=2048,
    )
    assert captured["payload"]["max_tokens"] == 2048


def test_max_tokens_default_from_config(monkeypatch):
    """未显式指定时,默认取配置 MAX_OUTPUT_TOKENS。"""
    monkeypatch.setenv("SGPT_MAX_OUTPUT_TOKENS", "512")
    from pocketshell.config import cfg
    assert cfg.get_int("MAX_OUTPUT_TOKENS", 4096) == 512


def test_direct_main_entry(tmp_path):
    """直接运行 __main__.py，目录名不是 agent（如 agent-latest）也应能工作，
    即使父目录下残留着残缺的旧 agent 目录（曾经的真实 bug 场景）。"""
    import os
    import shutil
    import subprocess
    import sys as _sys

    src = Path(__file__).resolve().parent.parent / "pocketshell"  # 包目录
    # 先放一个残缺的旧 agent 目录残留
    (tmp_path / "pocketshell").mkdir()
    (tmp_path / "pocketshell" / "stale.txt").write_text("stale", encoding="utf-8")
    dst = tmp_path / "pocketshell-latest"  # 改名场景
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"),
    )
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [_sys.executable, str(dst / "__main__.py"), "--version"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "PocketShell" in result.stdout


def test_default_chat_persists(tmp_path, monkeypatch):
    """未指定 --chat 时，提问应保存到常驻默认会话 default.json。"""
    from pocketshell import cli

    monkeypatch.setenv("SGPT_SESSIONS_DIR", str(tmp_path))
    monkeypatch.setenv("SGPT_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("SGPT_API_KEY", "test-key")

    def fake_post(url, headers, payload, timeout):
        return 200, FakeResponse(_sse(_content_chunk("你好！")))

    monkeypatch.setattr(api, "_post", fake_post)
    monkeypatch.setattr(api, "_url", lambda: "https://api.deepseek.com/chat/completions")

    rc = cli.main(["--no-tools", "你好"])
    assert rc == 0
    session_file = tmp_path / "default.json"
    assert session_file.exists(), "默认会话应落盘"
    data = json.loads(session_file.read_text(encoding="utf-8"))
    roles = [m["role"] for m in data]
    assert "user" in roles and "assistant" in roles


def test_clear_default_chat(tmp_path, monkeypatch):
    """--clear default 应删除常驻默认会话文件。"""
    from pocketshell import cli

    monkeypatch.setenv("SGPT_SESSIONS_DIR", str(tmp_path))
    monkeypatch.setenv("SGPT_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("SGPT_API_KEY", "test-key")

    # 先造一个会话文件
    (tmp_path / "default.json").write_text(
        json.dumps([{"role": "user", "content": "hi"}]), encoding="utf-8"
    )
    assert cli.main(["--clear", "default"]) == 0
    assert not (tmp_path / "default.json").exists()


def test_system_prompt_contains_cwd():
    """系统提示词应包含当前工作目录。"""
    import os
    from pocketshell.api import make_system_prompt
    assert os.getcwd() in make_system_prompt()


def test_system_prompt_refreshes_cwd(tmp_path, monkeypatch):
    """cwd 变化后,_run_turn 应刷新 system 消息,模型不会用过时的目录。"""
    import os
    from pocketshell import api, cli
    from pocketshell.session import Session

    monkeypatch.setenv("SGPT_SESSIONS_DIR", str(tmp_path))

    def fake_run(session, **kw):
        session.add_assistant("ok")
        return "ok"

    monkeypatch.setattr(cli, "run_conversation", fake_run)

    s = Session("t", ephemeral=True)
    # 第一次提问:cwd = /a
    monkeypatch.setattr(os, "getcwd", lambda: "/a")
    cli._run_turn(s, "hi", "m", 0.0, 1.0, False)
    assert "/a" in s.messages[0]["content"]
    # 第二次提问:cwd 变为 /b,system 应被刷新
    monkeypatch.setattr(os, "getcwd", lambda: "/b")
    cli._run_turn(s, "hi2", "m", 0.0, 1.0, False)
    assert "/b" in s.messages[0]["content"]
    assert "/a" not in s.messages[0]["content"]


def _sse_with_usage(*chunks, usage):
    """构造带末尾 usage 块的 SSE 行序列。"""
    lines = []
    for chunk in chunks:
        lines.append("data: " + json.dumps({"choices": [chunk]}))
        lines.append("")
    lines.append("data: " + json.dumps({"usage": usage}))
    lines.append("")
    lines.append("data: [DONE]")
    lines.append("")
    return lines


def test_stream_usage_reported(monkeypatch):
    """流式响应末尾的 usage 应通过 on_usage 回调返回（含缓存命中统计）。"""
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_cache_hit_tokens": 80,
        "prompt_cache_miss_tokens": 20,
    }
    sse = _sse_with_usage(_content_chunk("你好"), usage=usage)

    def fake_post(url, headers, payload, timeout):
        return 200, FakeResponse(sse)

    monkeypatch.setattr(api, "_post", fake_post)
    monkeypatch.setattr(api, "_url", lambda: "https://api.deepseek.com/chat/completions")
    monkeypatch.setenv("SGPT_API_KEY", "test-key")

    captured = {}
    content, _, _ = api.stream_completion(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        temperature=0.0,
        top_p=1.0,
        on_usage=lambda u: captured.update(u),
    )
    assert content == "你好"
    assert captured.get("prompt_tokens") == 100
    assert captured.get("prompt_cache_hit_tokens") == 80


def test_run_conversation_forwards_usage(tmp_session, monkeypatch):
    """run_conversation 应把 on_usage 透传给底层请求（工具循环累计）。"""
    tool_args = json.dumps({"shell_command": "echo hi"})
    first_round = _sse(_tool_chunk(0, "execute_shell_command", tool_args, "call_1"))
    second_round = _sse_with_usage(
        _content_chunk("完成。"),
        usage={"prompt_tokens": 200, "completion_tokens": 30,
               "prompt_cache_hit_tokens": 150, "prompt_cache_miss_tokens": 50},
    )

    def fake_post(url, headers, payload, timeout):
        if not fake_post.called:
            fake_post.called = True
            return 200, FakeResponse(first_round)
        return 200, FakeResponse(second_round)

    fake_post.called = False
    monkeypatch.setattr(api, "_post", fake_post)
    monkeypatch.setattr(api, "_url", lambda: "https://api.deepseek.com/chat/completions")
    monkeypatch.setenv("SGPT_API_KEY", "test-key")

    usages = []
    tmp_session.add_user("继续")
    result = api.run_conversation(
        tmp_session,
        model="deepseek-v4-flash",
        temperature=0.0,
        top_p=1.0,
        tools=[{"type": "function", "function": {"name": "execute_shell_command"}}],
        on_usage=lambda u: usages.append(u),
    )
    assert result == "完成。"
    assert usages, "on_usage 应被调用"
    assert usages[-1]["prompt_tokens"] == 200


# ---------------- 真实 urllib 端到端论证（不 mock，本地 HTTP 服务器） ----------------

def test_urllib_end_to_end_streaming(monkeypatch):
    """论证标准库方案：用 http.server 起本地服务器，按 DeepSeek 真实格式
    （chunked 传输 + SSE 事件 + 末尾 usage 块）逐块推送，
    验证 urllib 全链路（_post → _iter_lines → stream_completion）正确解析。"""
    import http.server
    import threading

    events = [
        '{"choices":[{"delta":{"content":"你"}}]}',
        '{"choices":[{"delta":{"content":"好"}}]}',
        '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
        '{"usage":{"prompt_tokens":10,"completion_tokens":2,'
        '"prompt_cache_hit_tokens":5,"prompt_cache_miss_tokens":5}}',
    ]
    received = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            received["payload"] = json.loads(self.rfile.read(length))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for ev in events:
                chunk = ("data: " + ev + "\n\n").encode()
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(api, "_url", lambda: f"http://127.0.0.1:{port}/chat/completions")
        monkeypatch.setenv("SGPT_API_KEY", "test-key")
        usages = []
        content, tools, reasoning = api.stream_completion(
            model="deepseek-v4-flash",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            temperature=0.0,
            top_p=1.0,
            on_usage=lambda u: usages.append(u),
        )
        assert content == "你好", f"流式内容解析错误: {content!r}"
        assert usages and usages[-1]["prompt_cache_hit_tokens"] == 5, "usage 块未被解析"
        assert received["payload"]["model"] == "deepseek-v4-flash"
        assert received["payload"]["stream_options"] == {"include_usage": True}
        assert received["payload"]["messages"] == [{"role": "user", "content": "hi"}]
    finally:
        server.shutdown()


def test_urllib_http_error_path(monkeypatch):
    """论证 401 真实路径：urllib HTTPError（非 200）被正确转成 ApiError。"""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"bad key"}}')
            self.wfile.flush()

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(api, "_url", lambda: f"http://127.0.0.1:{port}/chat/completions")
        monkeypatch.setenv("SGPT_API_KEY", "test-key")
        with pytest.raises(api.ApiError) as exc:
            api.stream_completion(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": "hi"}],
                tools=None,
                temperature=0.0,
                top_p=1.0,
            )
        assert exc.value.status_code == 401
        assert "401" in str(exc.value)
    finally:
        server.shutdown()

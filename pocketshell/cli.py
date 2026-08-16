# SPDX-License-Identifier: MPL-2.0
"""命令行入口：默认常驻对话 / REPL 连续对话 / --chat 多会话。

用法示例：
    python -m agent "查看当前目录"          （进入常驻默认对话，历史累积）
    python -m agent --chat work "继续昨天"   （指定会话）
    python -m agent --repl
    echo "你好" | python -m agent
    python -m agent --list-chats
    python -m agent --clear default
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List, Optional

from . import __version__
from .api import ApiError, make_system_prompt, run_conversation
from .config import cfg, ensure_config_file
from .render import MarkdownStreamRenderer, enable_ansi, supports_color
from .session import Session
from .tools import get_tool_schemas

# 流式 Markdown 着色器（stdout 非终端或 NO_COLOR 时自动退化为原样输出）
_renderer = MarkdownStreamRenderer(color=supports_color())


def _print_stream(chunk: str) -> None:
    sys.stdout.write(_renderer.feed(chunk))
    sys.stdout.flush()


def _accumulate_usage(total: Dict[str, int], usage: dict) -> None:
    """累加一轮底层请求的 usage 到总量（工具循环可能多轮）。"""
    for key in ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        total[key] = total.get(key, 0) + int(usage.get(key) or 0)


def _print_tool_call(name: str, arguments: str) -> None:
    try:
        args = json.loads(arguments)
        joined = ", ".join(f"{k}={v!r}" for k, v in args.items())
    except (json.JSONDecodeError, TypeError):
        joined = arguments
    print(f"\n\033[90m> 调用工具 \033[36m{name}\033[90m({joined})\033[0m\n", flush=True)


def _show_usage(total: Dict[str, int]) -> None:
    """打印本次对话的 token 消耗（累计多轮请求）与缓存命中情况。"""
    if not total.get("prompt_tokens") and not total.get("completion_tokens"):
        return
    p = total.get("prompt_tokens", 0)
    c = total.get("completion_tokens", 0)
    hit = total.get("prompt_cache_hit_tokens", 0)
    miss = total.get("prompt_cache_miss_tokens", 0)
    line = f"⚡ tokens: 输入 {p} + 输出 {c} = {p + c}"
    if hit or miss:
        rate = hit / (hit + miss) * 100 if (hit + miss) else 0
        line += f" | 缓存命中 {hit} ({rate:.0f}%)"
    print(f"\n\033[90m{line}\033[0m", flush=True)


def _run_turn(
    session: Session,
    prompt: str,
    model: str,
    temperature: float,
    top_p: float,
    use_tools: bool,
    max_tokens: Optional[int] = None,
    show_usage: bool = True,
) -> str:
    import os as _os

    # system 提示词含"当前工作目录"：每次提问前刷新，目录变化后模型不会用过时的 cwd。
    # 用整段提示词精确比较（而不是子串判断），避免"cd 到父目录"这类前缀目录
    # 变化被漏掉（如 D:\work\project -> D:\work 中 /work 是 /work/project 的子串）。
    system_text = make_system_prompt()
    force_system = False
    if not session.messages or session.messages[0].get("role") != "system":
        session.system(system_text)
        force_system = True  # 首轮必注入
    elif session.messages[0].get("content") != system_text:
        session.messages[0]["content"] = system_text
        force_system = True  # cwd（或自定义指令）变化：本轮强制注入最新 system
    session.add_user(prompt)
    tools = get_tool_schemas() if use_tools else None
    print(f"\n\033[90m[会话: {session.name}] 提问: {prompt}\033[0m" if not sys.stdout.isatty() else "", end="")

    usage_total: Dict[str, int] = {}
    result = run_conversation(
        session,
        model=model,
        temperature=temperature,
        top_p=top_p,
        tools=tools,
        max_tokens=max_tokens,
        on_chunk=_print_stream,
        on_tool=_print_tool_call,
        on_usage=lambda u: _accumulate_usage(usage_total, u),
        force_system=force_system,
    )
    if show_usage:
        _show_usage(usage_total)
    return result


def _list_chats() -> None:
    from pathlib import Path

    sessions_dir = Path(cfg.get("SESSIONS_DIR"))
    if not sessions_dir.exists():
        print("（暂无会话）")
        return
    for p in sorted(sessions_dir.glob("*.json")):
        print(p.stem)


def _show_context(session: Session) -> None:
    from .utils import estimate_messages_tokens

    budget = cfg.get_int("CONTEXT_TOKEN_BUDGET", 65536)
    used = estimate_messages_tokens(session.messages)
    msgs = session.messages_for_api()
    sent = estimate_messages_tokens(msgs)
    print(
        f"会话消息 {len(session.messages)} 条 | 当前占用 ~{used} tokens | "
        f"发送给模型 ~{sent} tokens | 预算 {budget}"
    )
    if used > budget:
        print(f"（已超预算，将自动丢弃最旧 {len(session.messages) - len(msgs)} 条消息）")


def _show_chat(name: str) -> None:
    session = Session(name)
    if not session.messages:
        print(f"（会话 {name} 为空）")
        return
    for m in session.messages:
        role = m.get("role")
        content = m.get("content")
        if role == "tool":
            continue
        if role == "assistant" and m.get("tool_calls"):
            content = None
        if content:
            print(f"\n\033[1m[{role}]\033[0m {content}")


def _doctor() -> int:
    """配置健康检查：定位 API Key 为什么读不到。"""
    import os

    print("=== agent 配置健康检查 ===")
    print(f"配置文件: {cfg.path}")
    print(f"  文件存在: {cfg.path.exists()}")
    if cfg.path.exists():
        try:
            from .config import _strip_json_comments

            text = None
            for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
                try:
                    text = cfg.path.read_text(encoding=enc)
                    break
                except (OSError, UnicodeDecodeError):
                    continue
            if text:
                try:
                    data = json.loads(_strip_json_comments(text))
                    if isinstance(data, dict):
                        print(f"  配置项数量: {len(data)}")
                        for k, v in data.items():
                            if k == "OPENAI_API_KEY":
                                filled = str(v).strip() and str(v) != "__OPENAI_API_KEY__"
                                print(f"  OPENAI_API_KEY = {'<已填写>' if filled else '<空>'}")
                            elif k == "DEFAULT_MODEL":
                                print(f"  DEFAULT_MODEL = {v}")
                    else:
                        print("  config.json 内容不是 JSON 对象")
                except json.JSONDecodeError:
                    print("  config.json 解析失败，请检查 JSON 语法（注释必须独占一行）")
        except OSError as e:
            print(f"  读取失败: {e}")
    print("环境变量:")
    for name in ("OPENAI_API_KEY", "PS_API_KEY", "SGPT_API_KEY"):
        val = os.environ.get(name, "")
        print(f"  {name}: {'<已设置>' if val else '<未设置>'}")
    try:
        key = cfg.get_api_key()
        masked = key[:6] + "…" + key[-4:] if len(key) > 10 else "<太短>"
        print(f"\n结论: API Key 可读 ✓ ({masked})")
    except RuntimeError:
        print("\n结论: ✗ API Key 未配置 — 请在配置文件中填入 OPENAI_API_KEY=sk-xxx")
        print("       或设置环境变量 PS_API_KEY=sk-xxx")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pocketshell",
        description="基于 DeepSeek 的终端 AI 助手（Windows 优先，安全防护内置）",
    )
    parser.add_argument("prompt", nargs="?", default="", help="提问内容（留空则读取 stdin）")
    parser.add_argument("--model", default=cfg.get("DEFAULT_MODEL"), help="模型代号（默认 deepseek-v4-flash）")
    parser.add_argument("--temperature", type=float, default=float(cfg.get("TEMPERATURE")), help="采样温度 (0-2)")
    parser.add_argument("--top-p", type=float, default=float(cfg.get("TOP_P")), help="核采样 (0-1)")
    parser.add_argument("--max-output", type=int, default=None,
                        help=f"单次回答最大 token 数（默认 {cfg.get('MAX_OUTPUT_TOKENS')}，0 表示不限制）")
    parser.add_argument("--chat", metavar="NAME", help="会话名（持久化对话历史）")
    parser.add_argument("--repl", action="store_true", help="进入连续对话模式")
    parser.add_argument("--no-tools", action="store_true", help="禁用工具调用")
    parser.add_argument("--no-usage", action="store_true", help="不显示每轮 token 消耗统计")
    parser.add_argument("--list-chats", action="store_true", help="列出所有会话")
    parser.add_argument("--show-chat", metavar="NAME", help="查看会话历史")
    parser.add_argument("--clear", metavar="NAME", help="删除会话")
    parser.add_argument("--doctor", action="store_true", help="配置健康检查（诊断 API Key 读取问题）")
    parser.add_argument("--version", action="store_true", help="显示版本")
    parser.add_argument("-setworkspace", "--setworkspace", nargs="?", const="__CWD__", metavar="DIR",
                        help="把 DIR（默认当前目录）设为工作目录：其内写文件免确认，删除仍硬拦；"
                             "传 off 关闭。仅本次运行生效，关闭窗口或 cd 离开后自动失效")
    args = parser.parse_args(argv)

    if args.version:
        print(f"PocketShell {__version__}")
        return 0

    # 首次运行创建示例配置文件（幂等）
    ensure_config_file()

    # 启用终端 ANSI 颜色（Windows cmd/PowerShell 需要显式开启 VT）
    enable_ansi()

    if args.doctor:
        return _doctor()

    # -setworkspace：设定工作目录（仅本次运行生效；内存态，不落盘）
    if args.setworkspace is not None:
        import os as _os
        from .safety import set_workspace

        if str(args.setworkspace).lower() in ("off", "none"):
            set_workspace(None)
            print("已关闭工作目录授权。")
            return 0
        if args.setworkspace == "__CWD__":
            path = _os.getcwd()
        else:
            path = args.setworkspace
        ws = set_workspace(path)
        print(f"已设置工作目录: {ws}")
        print("说明: 该目录内写文件免确认；删除/格式化等破坏性操作仍被硬拦截；")
        print("      关闭窗口或 cd 离开后授权自动失效，需重新设置。")
        return 0

    if args.list_chats:
        _list_chats()
        return 0
    if args.show_chat:
        _show_chat(args.show_chat)
        return 0
    if args.clear:
        Session(args.clear).reset()
        print(f"已清除会话: {args.clear}")
        return 0

    # 读取 prompt：参数 > stdin
    prompt = args.prompt
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()

    use_tools = (not args.no_tools) and cfg.get_bool("ENABLE_TOOLS")
    show_usage = (not args.no_usage) and cfg.get_bool("SHOW_USAGE", True)
    model = args.model
    temperature = max(0.0, min(2.0, args.temperature))
    top_p = max(0.0, min(1.0, args.top_p))
    if args.max_output == 0:
        max_tokens = None  # 显式不限制
    else:
        max_tokens = args.max_output if args.max_output else cfg.get_int("MAX_OUTPUT_TOKENS", 4096)

    try:
        if args.repl:
            session = Session(args.chat or "default")
            session.ensure_system(make_system_prompt())
            print(f"进入 REPL 模式（会话: {session.name}），输入 exit 退出，Ctrl+C 结束。")
            if prompt:
                print(f"\n\033[90m> {prompt}\033[0m")
                _run_turn(session, prompt, model, temperature, top_p, use_tools, max_tokens, show_usage)
                print()
                sys.stdout.write(_renderer.reset())
                sys.stdout.flush()
            while True:
                try:
                    user_input = input(">>> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if not user_input:
                    continue
                if user_input.lower() in ("exit", "quit"):
                    break
                if user_input.lower() in ("/context", "/ctx"):
                    _show_context(session)
                    continue
                if user_input.lower() in ("/clear", "/reset"):
                    session.reset()
                    session.ensure_system(make_system_prompt())
                    print("已清空当前会话上下文。")
                    continue
                if user_input.lower().startswith("/setworkspace"):
                    import os as _os
                    from .safety import set_workspace, get_workspace

                    rest = user_input[len("/setworkspace"):].strip()
                    if rest.lower() in ("off", "none"):
                        set_workspace(None)
                        print("已关闭工作目录授权。")
                    elif rest:
                        ws = set_workspace(rest)
                        print(f"已设置工作目录: {ws}")
                    else:
                        ws = set_workspace(_os.getcwd())
                        print(f"已设置工作目录: {ws}")
                    print("（该目录内写文件免确认，删除仍硬拦；关闭窗口或 cd 离开后失效）")
                    continue
                _run_turn(session, user_input, model, temperature, top_p, use_tools, max_tokens, show_usage)
                print()
                sys.stdout.write(_renderer.reset())
                sys.stdout.flush()
            return 0

        if args.chat:
            session = Session(args.chat)
        else:
            # 常驻默认对话：不指定 --chat 时，历史保存在 sessions/default.json
            session = Session("default")
        _run_turn(session, prompt, model, temperature, top_p, use_tools, max_tokens, show_usage)
        print()
        sys.stdout.write(_renderer.reset())
        sys.stdout.flush()
        return 0

    except ApiError as e:
        print(f"\n\033[31m错误: {e}\033[0m", file=sys.stderr)
        return 1
    except RuntimeError as e:  # 如未配置 API Key
        print(f"\n\033[31m{e}\033[0m", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except Exception as e:  # 兜底
        print(f"\n\033[31m意外错误: {type(e).__name__}: {e}\033[0m", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

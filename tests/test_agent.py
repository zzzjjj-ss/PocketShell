# SPDX-License-Identifier: MPL-2.0
"""冒烟测试：safety 拦截规则、会话 token 截断、工具执行、API 工具循环(mock)。

运行：cd /home/zhang/sgpt && HOME=/tmp/sgpt_test_home PYTHONPATH=/tmp/pip_site python3 -m pytest pocketshell/tests/ -q
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pocketshell import safety  # noqa: E402
from pocketshell.session import Session, clean_tool_messages  # noqa: E402
from pocketshell.tools import run_tool  # noqa: E402


# ---------------- safety: BLOCK ----------------

@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf /home/user/data",
        "del C:\\temp\\file.txt",
        "del /s /q C:\\temp",
        "erase C:\\test.txt",
        "rmdir /s /q C:\\temp",
        "rd C:\\temp",
        "Remove-Item C:\\temp\\file.txt",
        "Remove-Item -Path C:\\temp -Recurse -Force",
        "Remove-ItemProperty -Path HKLM:... -Name Foo",
        "Clear-Content C:\\log.txt",
        "Clear-Item C:\\temp",
        "Clear-RecycleBin",
        "Format-Volume -DriveLetter C",
        "format C: /fs:NTFS",
        "reg delete HKLM\\Software\\Test",
        "schtasks /delete /tn MyTask /f",
        "Unregister-ScheduledTask -TaskName MyTask",
        "wmic process where name='x.exe' delete",
        "sc delete MyService",
        "Remove-Service -Name MyService",
        "net user olduser /delete",
        "shred /home/user/file",
        "diskpart clean",
        "powershell -EncodedCommand SQBFAFgA",
        "powershell -enc SQBFAFgA",
        "Invoke-Expression 'rm -rf /'",
        "iex 'Get-Content x'",
        "$c = 'rm'; & $c -rf /",
        "powershell -c \"[Convert]::FromBase64String('QQ==')\"",
    ],
)
def test_block_rules(cmd):
    result = safety.analyze_command(cmd)
    assert result.verdict == safety.BLOCK, f"{cmd!r} 应被拦截, 实际 {result.verdict}: {result.reason}"


# ---------------- safety: ALLOW ----------------

@pytest.mark.parametrize(
    "cmd",
    [
        "dir",
        "ls",
        "echo hello",
        "type C:\\file.txt",
        "Get-Content C:\\file.txt",
        "Get-ChildItem C:\\temp",
        "ipconfig",
        "ping 127.0.0.1",
        "set",
        "where python",
        "Get-Date",
    ],
)
def test_allow_rules(cmd):
    result = safety.analyze_command(cmd)
    assert result.verdict == safety.ALLOW, f"{cmd!r} 应放行, 实际 {result.verdict}: {result.reason}"


# ---------------- safety: CONFIRM ----------------

@pytest.mark.parametrize(
    "cmd",
    [
        "shutdown /s",
        "taskkill /f /im notepad.exe",
        "Stop-Process -Name notepad",
        "icacls C:\\temp /reset",
        "takeown /f C:\\windows\\system32",
        "net user newuser /add",
    ],
)
def test_confirm_rules(cmd):
    result = safety.analyze_command(cmd)
    assert result.verdict == safety.CONFIRM, f"{cmd!r} 应为 CONFIRM, 实际 {result.verdict}: {result.reason}"


# ---------------- safety: 下载保存类写文件（WRITE_RULES） ----------------

@pytest.mark.parametrize(
    "cmd",
    [
        "curl -s -L https://example.com/page.html -o page.html",
        "curl -O https://example.com/file.zip",
        "curl --output data.json https://example.com/api",
        "wget -O out.html https://example.com",
        "wget -o log.txt https://example.com",
        "Invoke-WebRequest -Uri https://example.com -OutFile page.html",
        "iwr https://example.com -OutFile page.html",
        "curl -s -L --max-time 30 https://liquipedia.net/x -o ewc.html",
    ],
)
def test_download_write_rules(cmd):
    """curl -o/-O、wget -O/-o、--output、Invoke-WebRequest -OutFile 都属于写文件，应 CONFIRM。"""
    result = safety.analyze_command(cmd)
    assert result.verdict == safety.CONFIRM, f"{cmd!r} 应为 CONFIRM, 实际 {result.verdict}: {result.reason}"
    assert result.category == "write", f"{cmd!r} 应为 write 类, 实际 {result.category}"


# ---------------- 嵌套命令递归分析 ----------------

def test_nested_block():
    result = safety.analyze_command('cmd /c "del C:\\temp\\x.txt"')
    assert result.verdict == safety.BLOCK


def test_block_reply_format():
    result = safety.analyze_command("rm -rf /")
    reply = safety.block_reply("rm -rf /", result)
    assert "已被安全策略拦截" in reply
    assert "未执行" in reply


# ---------------- clean_tool_messages ----------------

def test_clean_drops_orphan_tool():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "tool", "content": "orphan", "tool_call_id": "x"},  # 孤立 tool
    ]
    cleaned = clean_tool_messages(msgs)
    assert all(m.get("role") != "tool" for m in cleaned)


def test_clean_drops_incomplete_tool_chain():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "tool_calls": [{"id": "t1"}, {"id": "t2"}]},  # 要 2 个 tool 回复
        {"role": "tool", "content": "r1", "tool_call_id": "t1"},  # 只有 1 个
    ]
    cleaned = clean_tool_messages(msgs)
    assert cleaned == [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


def test_clean_keeps_complete_tool_chain():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "tool_calls": [{"id": "t1"}, {"id": "t2"}]},
        {"role": "tool", "content": "r1", "tool_call_id": "t1"},
        {"role": "tool", "content": "r2", "tool_call_id": "t2"},
    ]
    cleaned = clean_tool_messages(msgs)
    assert len(cleaned) == 5


# ---------------- Session 持久化与 token 截断 ----------------

@pytest.fixture()
def tmp_session(tmp_path, monkeypatch):
    # Session 从 cfg 动态读取 SESSIONS_DIR，setenv 即可生效
    monkeypatch.setenv("PS_SESSIONS_DIR", str(tmp_path))
    return Session("test")


def test_session_save_load_utf8(tmp_session):
    tmp_session.add_user("你好,世界")
    tmp_session.add_assistant("你好!")
    tmp_session.save()

    s2 = Session("test")
    assert s2.messages[0]["content"] == "你好,世界"
    assert s2.messages[1]["content"] == "你好!"


def test_session_token_truncation(tmp_session, monkeypatch):
    monkeypatch.setenv("PS_CONTEXT_TOKEN_BUDGET", "34")
    # 塞入大量消息触发截断
    big = "x" * 100  # ~33 tokens
    tmp_session.add_user("u1")
    tmp_session.add_assistant(big)
    tmp_session.add_user("u2")  # 总 ~35 tokens > 34
    msgs = tmp_session.messages_for_api()
    assert len(msgs) == 2  # 最旧的 user u1 被丢弃
    assert msgs[0]["role"] == "assistant"
    assert msgs[-1]["content"] == "u2"


def test_session_system_kept_on_truncation(tmp_session, monkeypatch):
    monkeypatch.setenv("PS_CONTEXT_TOKEN_BUDGET", "30")
    tmp_session.ensure_system("system-prompt")
    tmp_session.add_user("u1")
    tmp_session.add_assistant("x" * 100)
    msgs = tmp_session.messages_for_api()
    assert msgs[0]["role"] == "system"
    assert len(msgs) >= 1


def test_session_reset(tmp_session):
    tmp_session.add_user("hello")
    tmp_session.save()
    tmp_session.reset()
    assert not tmp_session.messages
    assert not tmp_session.path.exists()


# ---------------- 工具执行（本地，不依赖网络） ----------------

def test_run_tool_unknown():
    assert "未知工具" in run_tool("no_such_tool", "{}")


def test_run_tool_bad_args():
    assert "参数" in run_tool("remember", "not json")


def test_run_tool_remember_recall(tmp_path, monkeypatch):
    mem = tmp_path / "mem.txt"
    monkeypatch.setenv("PS_MEMORY_FILE", str(mem))
    out = run_tool("remember", json.dumps({"info": "用户的目录是 D:\\work"}))
    assert "已记住" in out
    out2 = run_tool("recall", json.dumps({"query": "目录"}))
    assert "D:\\work" in out2


def test_execute_shell_blocked(tmp_path, monkeypatch):
    """BLOCK 指令应返回拦截消息,不真正执行。"""
    mem = tmp_path / "mem.txt"
    monkeypatch.setenv("PS_MEMORY_FILE", str(mem))
    out = run_tool("execute_shell_command", json.dumps({"shell_command": "rm -rf /tmp/x"}))
    assert "拦截" in out and "未执行" in out


# ---------------- web_search / fetch_url 解析（离线，不依赖网络） ----------------

def test_bing_parser_extracts_results():
    from pocketshell.tools import _BingParser

    html = """
    <html><body>
      <li class="b_algo">
        <h2><a href="https://example.com/page1">示例标题一</a></h2>
        <p>这是第一条结果的摘要内容。</p>
      </li>
      <li class="b_algo">
        <h2><a href="https://example.com/page2">示例标题二</a></h2>
        <p>第二条摘要。</p>
      </li>
    </body></html>
    """
    parser = _BingParser()
    parser.feed(html)
    assert len(parser.results) == 2
    assert parser.results[0]["title"] == "示例标题一"
    assert parser.results[0]["url"] == "https://example.com/page1"
    assert "摘要" in parser.results[0]["snippet"]


def test_text_extractor_strips_scripts():
    from pocketshell.tools import _TextExtractor

    html = """
    <html><body>
      <script>var x = 1;</script>
      <style>.a{}</style>
      <h1>标题</h1>
      <p>正文段落内容。</p>
    </body></html>
    """
    extractor = _TextExtractor()
    extractor.feed(html)
    text = extractor.text()
    assert "标题" in text
    assert "正文段落内容" in text
    assert "var x" not in text
    assert ".a{}" not in text


# ---------------- 配置文件模板与优先级 ----------------

def test_ensure_config_file_generates_full_template(tmp_path, monkeypatch):
    from pocketshell import config as config_mod

    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod.cfg, "path", cfg_path)

    config_mod.ensure_config_file()
    assert cfg_path.exists()
    text = cfg_path.read_text(encoding="utf-8")
    # 完整模板应包含主要配置项与注释
    for key in ("DEFAULT_MODEL", "OPENAI_API_KEY", "CONTEXT_TOKEN_BUDGET",
                "MAX_OUTPUT_TOKENS", "CONFIRM_DANGEROUS", "TEMPERATURE",
                "ENABLE_TOOLS", "SESSIONS_DIR", "MEMORY_FILE"):
        assert f'"{key}"' in text, f"模板缺少 {key}"
    assert '"DEFAULT_MODEL": "deepseek-v4-flash"' in text


def test_config_file_value_overrides_default(tmp_path, monkeypatch):
    from pocketshell import config as config_mod

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{ "TEMPERATURE": 1.5 }', encoding="utf-8")
    monkeypatch.setattr(config_mod.cfg, "path", cfg_path)
    c = config_mod.Config(cfg_path)
    assert c.get("TEMPERATURE") == "1.5"
    assert c.get_bool("ENABLE_TOOLS") is True


def test_never_overwrites_existing_config(tmp_path, monkeypatch):
    """已存在的 config.json 不会被覆盖：已有键值原样保留，仅补充模板新增的键。"""
    from pocketshell import config as config_mod

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{ "OPENAI_API_KEY": "sk-existing" }', encoding="utf-8")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod.cfg, "path", cfg_path)

    config_mod.ensure_config_file()
    text = cfg_path.read_text(encoding="utf-8")
    # 已有 key 原样保留
    assert '"sk-existing"' in text
    # 模板新增的键被补进（如 SYSTEM_PROMPT_INTERVAL），且文件仍是合法 JSON
    import json as _json
    data = _json.loads(config_mod._strip_json_comments(text))
    assert data["OPENAI_API_KEY"] == "sk-existing"
    assert "SYSTEM_PROMPT_INTERVAL" in data


# ---------------- 编码容错、注释剥离与迁移 ----------------

def test_strip_json_comments_keeps_urls():
    """字符串内的 //（如 https://）不能被注释剥离误删。"""
    from pocketshell.config import _strip_json_comments

    text = '''
{
  // 注释行
  "API_BASE_URL": "https://api.deepseek.com",
  "OPENAI_API_KEY": "sk-x", /* 块注释 */
  "note": "a // b"
}
'''
    cleaned = _strip_json_comments(text)
    assert "https://api.deepseek.com" in cleaned
    assert "sk-x" in cleaned
    assert "a // b" in cleaned
    assert "注释行" not in cleaned
    import json
    data = json.loads(cleaned)
    assert data["API_BASE_URL"] == "https://api.deepseek.com"


def test_read_commented_json_config(tmp_path, monkeypatch):
    """带 // 注释的 config.json 应正常解析。"""
    from pocketshell import config as config_mod

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        '{\n  // 这是注释\n  "OPENAI_API_KEY": "sk-json-key",\n'
        '  "CONTEXT_TOKEN_BUDGET": 32000\n}\n',
        encoding="utf-8",
    )
    c = config_mod.Config(cfg_path)
    assert c.get("OPENAI_API_KEY") == "sk-json-key"
    assert c.get("CONTEXT_TOKEN_BUDGET") == "32000"


def test_read_gbk_encoded_config(tmp_path, monkeypatch):
    """用记事本以 ANSI(GBK) 保存的配置文件也应能读出键值。"""
    from pocketshell import config as config_mod

    cfg_path = tmp_path / "config.json"
    # 模拟 GBK 保存：中文注释 + key
    raw = '{ // 注释\n "OPENAI_API_KEY": "sk-gbk-key" }\n'.encode("gbk")
    cfg_path.write_bytes(raw)
    c = config_mod.Config(cfg_path)
    assert c.get("OPENAI_API_KEY") == "sk-gbk-key"


def test_read_bom_utf8_config(tmp_path, monkeypatch):
    """带 UTF-8 BOM 的配置文件应正常解析（utf-8-sig 容错）。"""
    from pocketshell import config as config_mod

    cfg_path = tmp_path / "config.json"
    cfg_path.write_bytes(b"\xef\xbb\xbf{ \"OPENAI_API_KEY\": \"sk-bom-key\" }\n")
    c = config_mod.Config(cfg_path)
    assert c.get("OPENAI_API_KEY") == "sk-bom-key"


def test_same_process_reads_generated_config(tmp_path, monkeypatch):
    """生成模板后同一进程应立即读到（reload 修复缓存 bug）。"""
    from pocketshell import config as config_mod

    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod.cfg, "path", cfg_path)
    # 模拟：cfg 创建时文件不存在（已删除）
    config_mod.cfg._file_values = {}

    config_mod.ensure_config_file()
    # 生成后 reload，OPENAI_API_KEY 键应可读
    assert config_mod.cfg.get("OPENAI_API_KEY") == ""
    # 填上 key 后再读（模拟用户编辑后同进程继续）
    cfg_path.write_text('{ "OPENAI_API_KEY": "sk-after-edit" }', encoding="utf-8")
    config_mod.cfg.reload()
    assert config_mod.cfg.get("OPENAI_API_KEY") == "sk-after-edit"


# ---------------- 记忆删除与修改 ----------------

def test_forget_removes_matching_entries(tmp_path, monkeypatch):
    mem = tmp_path / "mem.txt"
    monkeypatch.setenv("PS_MEMORY_FILE", str(mem))
    run_tool("remember", json.dumps({"info": "ffmpeg 在 D:\\tools\\ffmpeg"}))
    run_tool("remember", json.dumps({"info": "Python 在 C:\\Python"}))
    out = run_tool("forget", json.dumps({"info": "ffmpeg"}))
    assert "已删除 1 条" in out
    rest = run_tool("recall", json.dumps({"query": ""}))
    assert "ffmpeg" not in rest
    assert "Python" in rest


def test_forget_no_match(tmp_path, monkeypatch):
    mem = tmp_path / "mem.txt"
    monkeypatch.setenv("PS_MEMORY_FILE", str(mem))
    run_tool("remember", json.dumps({"info": "某条记忆"}))
    out = run_tool("forget", json.dumps({"info": "不存在的关键词"}))
    assert "未找到" in out


def test_update_memory_replaces(tmp_path, monkeypatch):
    mem = tmp_path / "mem.txt"
    monkeypatch.setenv("PS_MEMORY_FILE", str(mem))
    run_tool("remember", json.dumps({"info": "工作目录是 D:\\old\\work"}))
    out = run_tool("update_memory", json.dumps({
        "old_info": "D:\\old\\work",
        "new_info": "D:\\new\\work",
    }))
    assert "已更新 1 条" in out
    rest = run_tool("recall", json.dumps({"query": ""}))
    assert "D:\\new\\work" in rest
    assert "D:\\old\\work" not in rest


# ---------------- 自毁防护：禁止操作 agent 自身目录 ----------------

def test_self_dir_delete_blocked():
    """命令含 agent 目录路径 + 删除动词 → BLOCK。"""
    from pocketshell.config import AGENT_DIR
    result = safety.analyze_command(f'Remove-Item "{AGENT_DIR}\\api.py" -Force')
    assert result.verdict == safety.BLOCK
    assert "项目目录" in result.reason


def test_self_dir_rm_rf_blocked():
    """rm -rf agent 目录 → BLOCK。"""
    from pocketshell.config import AGENT_DIR
    result = safety.analyze_command(f"rm -rf {AGENT_DIR}")
    assert result.verdict == safety.BLOCK
    assert "项目目录" in result.reason


def test_self_dir_cwd_delete_blocked():
    """cwd 在 agent 目录内 + 通配删除 → BLOCK（防止 Remove-Item * 清空自身）。"""
    from pocketshell.config import AGENT_DIR
    result = safety.analyze_command("Remove-Item * -Recurse", cwd=str(AGENT_DIR))
    assert result.verdict == safety.BLOCK
    assert "项目目录" in result.reason


def test_self_dir_rename_blocked():
    """重命名 agent 文件 → BLOCK。"""
    from pocketshell.config import AGENT_DIR
    result = safety.analyze_command(
        f'Rename-Item "{AGENT_DIR}\\__main__.py" "main.py"', cwd=str(AGENT_DIR)
    )
    assert result.verdict == safety.BLOCK
    assert "项目目录" in result.reason


def test_self_dir_overwrite_blocked():
    """覆盖写入 agent 文件 → BLOCK。"""
    from pocketshell.config import AGENT_DIR
    result = safety.analyze_command(f'Set-Content "{AGENT_DIR}\\api.py" "xxx"')
    assert result.verdict == safety.BLOCK


def test_self_dir_readonly_allowed():
    """agent 目录内只读命令 → 放行。"""
    from pocketshell.config import AGENT_DIR
    result = safety.analyze_command("dir /b", cwd=str(AGENT_DIR))
    assert result.verdict == safety.ALLOW
    result2 = safety.analyze_command(f'type "{AGENT_DIR}\\config.py"')
    assert result2.verdict == safety.ALLOW


def test_self_dir_unrelated_delete_still_blocked():
    """cwd 在外、删除别处文件 → 仍按原规则 BLOCK，且不是自毁原因。"""
    result = safety.analyze_command("Remove-Item C:\\temp\\x.txt -Force", cwd="C:\\work")
    assert result.verdict == safety.BLOCK
    assert "agent 自身目录" not in result.reason


# ---------------- CUSTOM_INSTRUCTIONS 追加指令 ----------------

def test_custom_instructions_appended(monkeypatch):
    """config.json 的 CUSTOM_INSTRUCTIONS 应追加到系统提示词末尾。"""
    from pocketshell import api
    monkeypatch.setenv("PS_CUSTOM_INSTRUCTIONS", "永远用简体中文回答；提到文件时给出完整路径")
    prompt = api.make_system_prompt()
    assert "【用户自定义指令】" in prompt
    assert "永远用简体中文回答" in prompt
    # 安全铁律仍在（追加不覆盖）
    assert "安全铁律" in prompt
    # 自定义指令在末尾
    assert prompt.index("【用户自定义指令】") > prompt.index("【回答】")


def test_custom_instructions_empty(monkeypatch):
    """未设置时提示词不含自定义指令段。"""
    from pocketshell import api
    monkeypatch.delenv("PS_CUSTOM_INSTRUCTIONS", raising=False)
    prompt = api.make_system_prompt()
    assert "【用户自定义指令】" not in prompt


# ---------------- 文件写操作确认（FILE_WRITE_CONFIRM） ----------------

@pytest.mark.parametrize("cmd", [
    'echo hello > test.txt',
    'echo hello >> test.txt',
    'Set-Content test.txt "x"',
    'Out-File test.txt',
    'Copy-Item a.txt b.txt',
    'Move-Item a.txt b.txt',
    'Rename-Item a.txt b.txt',
    'copy a.txt b.txt',
])
def test_write_operation_is_confirm(cmd, tmp_path):
    """写文件操作 → CONFIRM 且 category=write（需用户确认）。"""
    result = safety.analyze_command(cmd, cwd=str(tmp_path))
    assert result.verdict == safety.CONFIRM
    assert result.category == "write"
    assert "写文件操作" in result.reason


def test_write_redirect_excludes_stderr(tmp_path):
    """2>&1 是错误重定向不是写文件 → 不触发写确认。"""
    result = safety.analyze_command("dir 2>&1", cwd=str(tmp_path))
    assert result.category != "write"


def test_mkdir_not_write(tmp_path):
    """创建目录不是修改文件 → 不触发写确认。"""
    result = safety.analyze_command("mkdir C:\\work\\new", cwd=str(tmp_path))
    assert result.verdict == safety.ALLOW


def test_delete_stays_block_not_confirm(tmp_path):
    """删除命令保持 BLOCK，不降级为写确认。"""
    result = safety.analyze_command("Remove-Item C:\\work\\a.txt", cwd=str(tmp_path))
    assert result.verdict == safety.BLOCK
    assert result.category != "write"


def test_write_confirm_noninteractive_denied(tmp_path, monkeypatch):
    """FILE_WRITE_CONFIRM=true 且非交互（无输入）→ 拒绝执行。"""
    monkeypatch.setenv("PS_MEMORY_FILE", str(tmp_path / "mem.txt"))
    out = run_tool("execute_shell_command", json.dumps({"shell_command": "echo hi > x.txt"}))
    assert "未执行" in out


def test_write_confirm_disabled_runs(tmp_path, monkeypatch):
    """FILE_WRITE_CONFIRM=false 时写文件命令直接执行。"""
    monkeypatch.setenv("PS_MEMORY_FILE", str(tmp_path / "mem.txt"))
    monkeypatch.setenv("PS_FILE_WRITE_CONFIRM", "false")
    from pocketshell import tools as _tools
    from pocketshell import utils as _utils
    calls = []
    def fake_run_command(cmd, timeout=60):
        calls.append(cmd)
        return 0, "ok"
    monkeypatch.setattr(_utils, "run_command", fake_run_command)
    out = run_tool("execute_shell_command", json.dumps({"shell_command": "echo hi > x.txt"}))
    assert calls and "hi > x.txt" in calls[0]
    assert "ok" in out


# ---------------- 彩色 Markdown 渲染（render.py） ----------------

def test_render_block_headings_and_code():
    from pocketshell.render import render_block
    out = render_block("# 标题\n正文 `code` 和 **粗体**\n```\nx = 1\n```", color=True)
    assert "\033[1m\033[36m标题\033[0m" in out          # 标题亮青加粗
    assert "\033[36mcode\033[0m" in out                  # 行内码
    assert "\033[1m粗体\033[0m" in out                   # 粗体
    assert "\033[36mx = 1\n" in out                  # 代码块内容行青色
    assert "\033[36m```\033[0m" in out                   # 闭合行复位


def test_render_block_no_color():
    from pocketshell.render import render_block
    out = render_block("# 标题\n`code`\n```\nx\n```", color=False)
    assert out == "# 标题\n`code`\n```\nx\n```"


def test_render_stream_code_block_toggle():
    from pocketshell.render import MarkdownStreamRenderer
    r = MarkdownStreamRenderer(color=True)
    out = r.feed("```python\n")
    assert "\033[36m" in out
    assert r.in_code is True
    out2 = r.feed("print(1)\n```\n")
    assert "print(1)" in out2          # 内容原样（青色由进入代码块时的色码保持）
    assert out2.endswith("\033[0m")    # 闭合行复位颜色
    assert r.in_code is False
    assert r.reset() == ""


def test_render_stream_inline_code():
    from pocketshell.render import MarkdownStreamRenderer
    r = MarkdownStreamRenderer(color=True)
    out = r.feed("请运行 `dir` 查看。")
    assert "\033[36mdir\033[0m" in out


def test_render_stream_disabled():
    from pocketshell.render import MarkdownStreamRenderer
    r = MarkdownStreamRenderer(color=False)
    assert r.feed("```\nx\n```") == "```\nx\n```"
    assert r.reset() == ""


# ---------------- setup/ 安装脚本静态验证 ----------------

def test_install_ps1_generates_cmd_entry():
    """install.ps1 应包含正确的命令入口模板与命令名校验。"""
    import re
    setup_dir = Path(__file__).resolve().parent.parent / "setup"
    src = (setup_dir / "install.ps1").read_text(encoding="utf-8")
    # UTF-8 BOM
    assert src.startswith("\ufeff"), "install.ps1 必须带 UTF-8 BOM(Windows PS 5.1 中文不乱码)"
    # 命令名校验
    assert re.search(r"CmdName.*-notmatch.*\^\[A-Za-z0-9_-\]\+", src)
    # 生成的 cmd 模板:指向上级目录的 pocketshell 包
    assert 'python "%~dp0..\\pocketshell\\__main__.py" %*' in src
    assert "@echo off" in src
    # PATH 只改用户级
    assert "GetEnvironmentVariable('Path', 'User')" in src
    assert "SetEnvironmentVariable('Path'" in src


def test_uninstall_ps1_bom_and_safety():
    """uninstall.ps1 应带 BOM,且不删除任何文件。"""
    setup_dir = Path(__file__).resolve().parent.parent / "setup"
    src = (setup_dir / "uninstall.ps1").read_text(encoding="utf-8")
    assert src.startswith("\ufeff")
    assert "Remove-Item" not in src and "del " not in src, "卸载脚本不应删除文件"


def test_install_bat_ascii_and_prompt():
    """install.bat 应全 ASCII(防 GBK 乱码),并询问命令名。"""
    setup_dir = Path(__file__).resolve().parent.parent / "setup"
    src = (setup_dir / "install.bat").read_text(encoding="utf-8")
    assert src.isascii(), "install.bat 必须全 ASCII"
    assert "set /p CMDNAME" in src
    assert "-CmdName" in src
    assert "pocketshell\\__main__.py" not in src, "bat 不应直接引用包路径(由 ps1 生成)"


# ---------------- web_search: RSS 优先 + HTML 回退 ----------------

RSS_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>必应: python</title>
<item><title>Python 官网</title><link>https://www.python.org/</link><description>&lt;b&gt;Python&lt;/b&gt; 编程语言官方站点</description></item>
<item><title>Python 下载</title><link>https://www.python.org/downloads/</link><description>下载页描述</description></item>
</channel></rss>"""


def test_parse_rss_results():
    from pocketshell.tools import _parse_rss_results
    results = _parse_rss_results(RSS_SAMPLE)
    assert len(results) == 2
    assert results[0]["title"] == "Python 官网"
    assert results[0]["url"] == "https://www.python.org/"
    # HTML 标签被剥掉
    assert "<b>" not in results[0]["snippet"]
    assert "Python" in results[0]["snippet"]


def test_parse_rss_results_empty():
    from pocketshell.tools import _parse_rss_results
    assert _parse_rss_results("<rss><channel></channel></rss>") == []


def test_web_search_uses_rss_first(tmp_path, monkeypatch):
    """RSS 有结果时直接用 RSS,不请求 HTML 页。"""
    from pocketshell import tools
    calls = []
    def fake_http_get(url, timeout=12, headers=None):
        calls.append(url)
        return RSS_SAMPLE
    monkeypatch.setattr(tools, "http_get", fake_http_get)
    out = tools._web_search("python")
    assert "Python 官网" in out and "https://www.python.org/" in out
    assert "format=rss" in calls[0], "应先请求 RSS 接口"
    assert len(calls) == 1, "RSS 有结果时不应回退 HTML"


def test_web_search_falls_back_to_html(tmp_path, monkeypatch):
    """RSS 为空时回退 HTML 解析。"""
    from pocketshell import tools
    html = """<html><body><ol id="b_results">
      <li class="b_algo"><h2><a href="https://example.com/x">示例标题</a></h2><p>摘要</p></li>
    </ol></body></html>"""
    calls = []
    def fake_http_get(url, timeout=12, headers=None):
        calls.append(url)
        return html if "format=rss" not in url else "<rss><channel></channel></rss>"
    monkeypatch.setattr(tools, "http_get", fake_http_get)
    out = tools._web_search("python")
    assert "示例标题" in out and "https://example.com/x" in out
    assert len(calls) == 2, "RSS 空时应回退 HTML"


def test_web_search_error_message(monkeypatch):
    """RSS 与 HTML 都失败时返回错误信息。"""
    from pocketshell import tools
    def fake_http_get(url, timeout=12, headers=None):
        raise TimeoutError("timeout")
    monkeypatch.setattr(tools, "http_get", fake_http_get)
    out = tools._web_search("python")
    assert "搜索出错" in out and "timeout" in out


# ---------------- 启动脚本不含 chcp(清屏回归保护) ----------------

def test_enable_utf8_noop_on_nonwindows(monkeypatch):
    """非 Windows 环境下 enable_utf8 无副作用(不抛异常、不动编码)。"""
    from pocketshell import render
    monkeypatch.setattr(render, "os", type("O", (), {"name": "posix"})())
    enc_before = sys.stdout.encoding
    render.enable_utf8()
    assert sys.stdout.encoding == enc_before


def test_launch_scripts_include_chcp():
    """启动脚本必须含 chcp 65001 —— 无它则 ffmpeg 中文文件名报
    Illegal byte sequence（GBK 控制台代码页 vs 程序 UTF-8 路径），加回后正常。"""
    from pocketshell.config import ROOT_DIR
    missing = []
    targets = []
    for pat in ("*.bat", "*.ps1"):
        targets.extend(ROOT_DIR.rglob(pat))
    tmpl = ROOT_DIR / "tools" / "green_assets.py"
    if tmpl.exists():
        targets.append(tmpl)
    for p in targets:
        if "__pycache__" in str(p) or "_build" in str(p) or ".git" in str(p):
            continue
        # 只检查入口类脚本：run.bat / install.ps1（内含生成的 .cmd 模板）/ green 模板。
        # install.bat/uninstall.bat 是安装器本身，不需要 chcp。
        name = p.name.lower()
        if name not in ("run.bat", "install.ps1", "green_assets.py"):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "chcp 65001" not in text.lower():
            missing.append(str(p))
    assert not missing, f"以下入口脚本缺少 chcp 65001(会导致中文文件名 Illegal byte sequence):\n" + "\n".join(missing)


def test_decide_shell_prefers_powershell():
    """控制台进程列表同时含 cmd 与 powershell 时,优先判定为 PowerShell。"""
    from unittest import mock
    from pocketshell import utils

    with mock.patch.object(utils, "_pid_to_name", side_effect=lambda pid: {
        100: "cmd.exe",
        200: "powershell.exe",
        300: "python.exe",
    }.get(pid, "")):
        # 列表含自身(300)与两个 shell → 应返回 powershell.exe
        assert utils._decide_shell([100, 200, 300], 300) == "powershell.exe"
        # 只有 cmd + 自身 → cmd.exe
        assert utils._decide_shell([100, 300], 300) == "cmd.exe"
        # 只有自身与无关进程 → 兜底 cmd.exe
        assert utils._decide_shell([300, 400], 300) == "cmd.exe"


def test_decide_shell_excludes_self():
    """自身 PID 不应参与 shell 判定。"""
    from unittest import mock
    from pocketshell import utils

    with mock.patch.object(utils, "_pid_to_name", side_effect=lambda pid: {
        111: "python.exe",
        222: "powershell.exe",
    }.get(pid, "")):
        # 自身 PID 就是 powershell.exe?不会,但列表中只有自身时不该判为 ps
        assert utils._decide_shell([111], 111) == "cmd.exe"
        # 另一个 powershell 进程(用户终端)才算
        assert utils._decide_shell([111, 222], 111) == "powershell.exe"


def test_tool_schema_description_follows_shell():
    """execute_shell_command 的工具描述应按检测到的 shell 动态变化。"""
    from unittest import mock
    from pocketshell.tools import get_tool_schemas

    with mock.patch("pocketshell.tools.detect_shell", return_value="cmd.exe"):
        desc = get_tool_schemas(["execute_shell_command"])[0]["function"]["description"]
        assert "cmd 语法" in desc
        assert "dir" in desc

    with mock.patch("pocketshell.tools.detect_shell", return_value="powershell.exe"):
        desc = get_tool_schemas(["execute_shell_command"])[0]["function"]["description"]
        assert "PowerShell 语法" in desc
        assert "Get-ChildItem" in desc


def test_run_command_cmd_branch_no_prefix(monkeypatch):
    """cmd 分支执行命令必须与用户手动执行一致，不加 chcp 等任何前缀。"""
    from unittest import mock
    from pocketshell import utils

    calls = []

    def fake_subprocess_run(cmd_list, **kw):
        calls.append(cmd_list)
        class _P:
            returncode = 0
            stdout = b"ok"
            stderr = b""
        return _P()

    with mock.patch.object(utils, "IS_WINDOWS", True), \
         mock.patch.object(utils, "detect_shell", return_value="cmd.exe"), \
         mock.patch.object(utils.subprocess, "run", fake_subprocess_run):
        code, out = utils.run_command("dir /b 回马喷.mp3")
    assert calls, "subprocess.run 未被调用"
    cmd_list = calls[0]
    assert cmd_list[0] == "cmd.exe"
    assert cmd_list[1] == "/d" and cmd_list[2] == "/c"
    assert cmd_list[3] == "dir /b 回马喷.mp3"  # 原样传递，无 chcp 前缀
    assert "chcp" not in cmd_list[3]
    assert code == 0 and out == "ok"


def test_run_command_ps_branch_no_chcp(monkeypatch):
    """PowerShell 分支不用 chcp（PowerShell 原生 UTF-16 传参）。"""
    from unittest import mock
    from pocketshell import utils

    calls = []

    def fake_subprocess_run(cmd_list, **kw):
        calls.append(cmd_list)
        class _P:
            returncode = 0
            stdout = b"ok"
            stderr = b""
        return _P()

    with mock.patch.object(utils, "IS_WINDOWS", True), \
         mock.patch.object(utils, "detect_shell", return_value="powershell.exe"), \
         mock.patch.object(utils.subprocess, "run", fake_subprocess_run):
        code, out = utils.run_command("Get-ChildItem")
    cmd_list = calls[0]
    assert cmd_list[0] == "powershell.exe"
    assert cmd_list[1:4] == ["-NoProfile", "-NonInteractive", "-Command"]
    assert cmd_list[4] == "Get-ChildItem"

"""冒烟测试：safety 拦截规则、会话 token 截断、工具执行、API 工具循环(mock)。

运行：cd /home/zhang/sgpt && HOME=/tmp/sgpt_test_home PYTHONPATH=/tmp/pip_site python3 -m pytest agent/tests/ -q
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
    monkeypatch.setenv("SGPT_SESSIONS_DIR", str(tmp_path))
    return Session("test")


def test_session_save_load_utf8(tmp_session):
    tmp_session.add_user("你好,世界")
    tmp_session.add_assistant("你好!😀")
    tmp_session.save()

    s2 = Session("test")
    assert s2.messages[0]["content"] == "你好,世界"
    assert s2.messages[1]["content"] == "你好!😀"


def test_session_token_truncation(tmp_session, monkeypatch):
    monkeypatch.setenv("SGPT_CONTEXT_TOKEN_BUDGET", "34")
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
    monkeypatch.setenv("SGPT_CONTEXT_TOKEN_BUDGET", "30")
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
    monkeypatch.setenv("SGPT_MEMORY_FILE", str(mem))
    out = run_tool("remember", json.dumps({"info": "用户的目录是 D:\\work"}))
    assert "已记住" in out
    out2 = run_tool("recall", json.dumps({"query": "目录"}))
    assert "D:\\work" in out2


def test_execute_shell_blocked(tmp_path, monkeypatch):
    """BLOCK 指令应返回拦截消息,不真正执行。"""
    mem = tmp_path / "mem.txt"
    monkeypatch.setenv("SGPT_MEMORY_FILE", str(mem))
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
    """已存在的 config.json 绝不能被 ensure_config_file 覆盖/升级。"""
    from pocketshell import config as config_mod

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{ "OPENAI_API_KEY": "sk-existing" }', encoding="utf-8")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod.cfg, "path", cfg_path)

    config_mod.ensure_config_file()
    text = cfg_path.read_text(encoding="utf-8")
    # 文件内容完全不变，key 原样保留
    assert text == '{ "OPENAI_API_KEY": "sk-existing" }'


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


def test_migrate_old_sgptrc_key(tmp_path, monkeypatch):
    """旧版 config/.sgptrc 的 API Key 应迁移进 config.json，旧文件被清理。"""
    from pocketshell import config as config_mod

    # 造一个旧版目录结构
    old_dir = config_mod.AGENT_DIR / "config"
    old_dir.mkdir(parents=True, exist_ok=True)
    old_file = old_dir / ".sgptrc"
    old_file.write_text(
        "# agent 配置文件\nOPENAI_API_KEY=sk-migrated-key\n", encoding="utf-8"
    )
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config_mod.cfg, "path", cfg_path)

    config_mod.ensure_config_file()
    # config.json 已生成且含迁移的 key
    assert cfg_path.exists()
    assert config_mod.cfg.get("OPENAI_API_KEY") == "sk-migrated-key"
    # 旧 .sgptrc 已删除（config/ 目录若空也删除）
    assert not old_file.exists()
    # 清理：删掉可能残留的 config 目录（防止污染 workspace）
    if old_dir.exists() and not any(old_dir.iterdir()):
        old_dir.rmdir()


# ---------------- 记忆删除与修改 ----------------

def test_forget_removes_matching_entries(tmp_path, monkeypatch):
    mem = tmp_path / "mem.txt"
    monkeypatch.setenv("SGPT_MEMORY_FILE", str(mem))
    run_tool("remember", json.dumps({"info": "ffmpeg 在 D:\\tools\\ffmpeg"}))
    run_tool("remember", json.dumps({"info": "Python 在 C:\\Python"}))
    out = run_tool("forget", json.dumps({"info": "ffmpeg"}))
    assert "已删除 1 条" in out
    rest = run_tool("recall", json.dumps({"query": ""}))
    assert "ffmpeg" not in rest
    assert "Python" in rest


def test_forget_no_match(tmp_path, monkeypatch):
    mem = tmp_path / "mem.txt"
    monkeypatch.setenv("SGPT_MEMORY_FILE", str(mem))
    run_tool("remember", json.dumps({"info": "某条记忆"}))
    out = run_tool("forget", json.dumps({"info": "不存在的关键词"}))
    assert "未找到" in out


def test_update_memory_replaces(tmp_path, monkeypatch):
    mem = tmp_path / "mem.txt"
    monkeypatch.setenv("SGPT_MEMORY_FILE", str(mem))
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
    assert "agent 自身目录" in result.reason


def test_self_dir_rm_rf_blocked():
    """rm -rf agent 目录 → BLOCK。"""
    from pocketshell.config import AGENT_DIR
    result = safety.analyze_command(f"rm -rf {AGENT_DIR}")
    assert result.verdict == safety.BLOCK
    assert "agent 自身目录" in result.reason


def test_self_dir_cwd_delete_blocked():
    """cwd 在 agent 目录内 + 通配删除 → BLOCK（防止 Remove-Item * 清空自身）。"""
    from pocketshell.config import AGENT_DIR
    result = safety.analyze_command("Remove-Item * -Recurse", cwd=str(AGENT_DIR))
    assert result.verdict == safety.BLOCK
    assert "agent 自身目录" in result.reason


def test_self_dir_rename_blocked():
    """重命名 agent 文件 → BLOCK。"""
    from pocketshell.config import AGENT_DIR
    result = safety.analyze_command(
        f'Rename-Item "{AGENT_DIR}\\__main__.py" "main.py"', cwd=str(AGENT_DIR)
    )
    assert result.verdict == safety.BLOCK
    assert "agent 自身目录" in result.reason


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
    monkeypatch.setenv("SGPT_CUSTOM_INSTRUCTIONS", "永远用简体中文回答；提到文件时给出完整路径")
    prompt = api.make_system_prompt()
    assert "【用户自定义指令】" in prompt
    assert "永远用简体中文回答" in prompt
    # 安全铁律仍在（追加不覆盖）
    assert "安全铁律" in prompt
    # 自定义指令在末尾
    assert prompt.index("【用户自定义指令】") > prompt.index("【回答风格】")


def test_custom_instructions_empty(monkeypatch):
    """未设置时提示词不含自定义指令段。"""
    from pocketshell import api
    monkeypatch.delenv("SGPT_CUSTOM_INSTRUCTIONS", raising=False)
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
def test_write_operation_is_confirm(cmd):
    """写文件操作 → CONFIRM 且 category=write（需用户确认）。"""
    result = safety.analyze_command(cmd, cwd="C:\\work")
    assert result.verdict == safety.CONFIRM
    assert result.category == "write"
    assert "写文件操作" in result.reason


def test_write_redirect_excludes_stderr():
    """2>&1 是错误重定向不是写文件 → 不触发写确认。"""
    result = safety.analyze_command("dir 2>&1", cwd="C:\\work")
    assert result.category != "write"


def test_mkdir_not_write():
    """创建目录不是修改文件 → 不触发写确认。"""
    result = safety.analyze_command("mkdir C:\\work\\new", cwd="C:\\work")
    assert result.verdict == safety.ALLOW


def test_delete_stays_block_not_confirm():
    """删除命令保持 BLOCK，不降级为写确认。"""
    result = safety.analyze_command("Remove-Item C:\\work\\a.txt", cwd="C:\\work")
    assert result.verdict == safety.BLOCK
    assert result.category != "write"


def test_write_confirm_noninteractive_denied(tmp_path, monkeypatch):
    """FILE_WRITE_CONFIRM=true 且非交互（无输入）→ 拒绝执行。"""
    monkeypatch.setenv("SGPT_MEMORY_FILE", str(tmp_path / "mem.txt"))
    out = run_tool("execute_shell_command", json.dumps({"shell_command": "echo hi > x.txt"}))
    assert "未执行" in out


def test_write_confirm_disabled_runs(tmp_path, monkeypatch):
    """FILE_WRITE_CONFIRM=false 时写文件命令直接执行。"""
    monkeypatch.setenv("SGPT_MEMORY_FILE", str(tmp_path / "mem.txt"))
    monkeypatch.setenv("SGPT_FILE_WRITE_CONFIRM", "false")
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
